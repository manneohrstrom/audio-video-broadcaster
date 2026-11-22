#!/usr/bin/env python3
"""camnode: camera node agent (Phase 2).

Owns the camera on one node: live mode (MediaMTX via systemd) or offline takes
(rpicam-vid with software sync). Serves a small JSON API for the hub.
Standard library only. Config: /etc/camnode/config.json (rendered by Ansible).
"""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.10.0"
NOMINAL_MS = 1000.0 / 30
TAKE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
WC_RE = re.compile(rb'"FrameWallClock":\s*(\d+)')
TS_RE = re.compile(rb'"SensorTimestamp":\s*(\d+)')
SYNC_RE = re.compile(r"Sync achieved!?(?: Difference (-?\d+)\s*us)?")

DEFAULT_CONFIG = {
    "host": os.uname().nodename,
    "port": 8080,
    "takes_dir": "/var/lib/camnode/takes",
    "rpicam_vid": "rpicam-vid",
    # Base tuning file; a copy with the take's own sync port goes into each take (sync_tuning).
    "tuning_file": "/usr/share/libcamera/ipa/rpi/vc4/imx708_wide.json",
    "rpicam_still": "rpicam-still",
    "snapshot_timeout_s": 10,
    "still_meta_dir": "/dev/shm",        # rpicam-still's metadata for one snapshot (RAM)
    "stop_signal": "SIGINT",
    "stop_timeout_s": 15,
    "min_free_gb": 2.0,
    "live_ready_timeout_s": 20,
    "mediamtx_api": "http://127.0.0.1:9997",
    # Green ACT LED as a recording light. Empty string disables it. Needs the
    # udev rule from node/udev/ (trigger and brightness writable by group video).
    "led_path": "/sys/class/leds/ACT",
    "settings": {
        "shutter_us": 30000,      # 30 ms (user, 2026-11-21); the deployed config sets it (cam_shutter_us)
        "gain": 2.0,
        "awb_gains": [1.5, 1.5],
        "lens_position": 1.0,
        "bitrate": 16000000,
    },
}


# ---------------------------------------------------------------- pure helpers

def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        settings = dict(cfg["settings"], **user.pop("settings", {}))
        cfg.update(user)
        cfg["settings"] = settings
    return cfg


def sync_tuning(base, port, outdir):
    """A copy of the tuning file whose software sync (rpi.sync) uses its own UDP port, so
    a sync packet from an earlier take (Wi-Fi multicast arriving seconds late, 2026-11-16)
    can't be taken for this one. Returns its path, or None if the base can't be read."""
    try:
        with open(base) as f:
            t = json.load(f)
        found = False
        for a in t.get("algorithms", []):
            if "rpi.sync" in a:
                a["rpi.sync"] = dict(a["rpi.sync"] or {}, port=int(port))
                found = True
        if not found:
            t.setdefault("algorithms", []).append({"rpi.sync": {"port": int(port)}})
        path = os.path.join(outdir, "tuning.json")
        with open(path, "w") as f:
            json.dump(t, f)
        return path
    except (OSError, ValueError):
        return None


def build_cmd(cfg, role, outdir, settings, tuning=None):
    s = settings
    return ([cfg["rpicam_vid"], "--tuning-file", tuning] if tuning else [cfg["rpicam_vid"]]) + [
        "-n", "-t", "0", "--sync", role,
        "--mode", "2304:1296:10:P", "--width", "1920", "--height", "1080",
        "--framerate", "30", "--codec", "h264", "--bitrate", str(int(s["bitrate"])),
        "--denoise", "cdn_off",
        "--shutter", str(int(s["shutter_us"])), "--gain", str(s["gain"]),
        "--awbgains", "%s,%s" % tuple(s["awb_gains"]),
        "--autofocus-mode", "manual", "--lens-position", str(s["lens_position"]),
        "-o", os.path.join(outdir, "video.h264"),
        "--save-pts", os.path.join(outdir, "video.pts"),
        "--metadata", os.path.join(outdir, "metadata.json"), "--metadata-format", "json",
    ]


def build_still_cmd(cfg, settings, auto=False, metadata=None):
    """One JPEG to stdout (Track B snapshot, spike B-S3: ~0.6 s). With the take's settings,
    or auto=True: shutter fixed, gain and white balance metered by the camera for ~0.7 s
    (B-S2: gain settles in 0.4-0.5 s), for calibration. metadata: file for the frame's
    metadata (the gains the camera chose)."""
    s = settings
    cmd = [cfg["rpicam_still"], "-n"] + (["-t", "700"] if auto else ["-t", "1", "--immediate"]) + [
        "--mode", "2304:1296:10:P", "--width", "1920", "--height", "1080", "--denoise", "cdn_off",
        "--shutter", str(int(s["shutter_us"]))]
    if not auto:
        cmd += ["--gain", str(s["gain"]), "--awbgains", "%s,%s" % tuple(s["awb_gains"])]
    cmd += ["--autofocus-mode", "manual", "--lens-position", str(s["lens_position"]),
            "-e", "jpg", "-q", "85", "-o", "-"]
    if metadata:
        cmd += ["--metadata", metadata, "--metadata-format", "json"]
    return cmd


SNAPSHOT_KEYS = ("shutter_us", "gain", "awb_gains", "lens_position")
# Take settings -> MediaMTX rpiCamera parameters, changeable on a running stream without a
# restart or a gap (spike B-S1). Shutter stays as configured.
STUCK_MSG = "camera stuck in the driver: reboot this camera"
LIVE_KEYS = {"gain": "rpiCameraGain", "awb_gains": "rpiCameraAWBGains", "lens_position": "rpiCameraLensPosition"}


def mediamtx_patch(api, params):
    """Change the running camera path through MediaMTX's API (in memory only: a MediaMTX
    restart goes back to mediamtx.yml, so the hub re-sends settings on every LIVE start)."""
    req = urllib.request.Request(api + "/v3/config/paths/patch/cam", method="PATCH",
                                 data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        r.read()
META_KEYS = ("AnalogueGain", "DigitalGain", "ColourGains", "ColourTemperature", "Lux", "ExposureTime")


def parse_pts(path):
    """Return frame times in ms from an mkvmerge v2 timecode file, or None."""
    try:
        with open(path) as f:
            out = [float(line) for line in f if line.strip() and not line.startswith("#")]
    except (OSError, ValueError):
        return None
    return out or None


def scan_metadata(path):
    """Stream the per-frame metadata (tolerates a truncated file after a crash).

    Returns dict(frames, first_wc_ns, last_wc_ns, sensor_ts_ns list)."""
    wcs_first = wcs_last = None
    ts = []
    n_wc = 0
    try:
        with open(path, "rb") as f:
            for line in f:
                m = TS_RE.search(line)
                if m:
                    ts.append(int(m.group(1)))
                    continue
                m = WC_RE.search(line)
                if m:
                    v = int(m.group(1))
                    n_wc += 1
                    if wcs_first is None:
                        wcs_first = v
                    wcs_last = v
    except OSError:
        return None
    return {"frames": len(ts), "wc_frames": n_wc, "first_wc_ns": wcs_first,
            "last_wc_ns": wcs_last, "sensor_ts_ns": ts}


def first_wallclock(path, limit=8192):
    """Cheap read of frame 0's FrameWallClock while a take is running."""
    try:
        with open(path, "rb") as f:
            m = WC_RE.search(f.read(limit))
    except OSError:
        return None
    return int(m.group(1)) if m else None


class LiveGapCounter:
    """Counts dropped frames while a take records, reading only new metadata bytes.

    A gap is a FrameWallClock step longer than 1.5 frames; missing = round(step/period) - 1."""

    def __init__(self, path, period_ms=NOMINAL_MS):
        self.path, self.period_ns = path, period_ms * 1e6
        self.offset, self.last, self.frames, self.dropped, self.gaps = 0, None, 0, 0, 0

    def update(self):
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return self
        end = data.rfind(b"\n") + 1          # only complete lines
        for m in WC_RE.finditer(data[:end]):
            wc = int(m.group(1))
            if self.last is not None:
                step = wc - self.last
                if step > 1.5 * self.period_ns:
                    self.gaps += 1
                    self.dropped += int(round(step / self.period_ns)) - 1
            self.last = wc
            self.frames += 1
        self.offset += end
        return self


def pts_from_sensor_ts(ts_ns):
    if not ts_ns:
        return []
    t0 = ts_ns[0]
    return [(t - t0) / 1e6 for t in ts_ns]


def write_pts(path, pts_ms):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write("# timecode format v2\n")
        for t in pts_ms:
            f.write("%.3f\n" % t)
    os.replace(tmp, path)


def find_gaps(pts_ms, nominal=NOMINAL_MS):
    """Frame-time gaps longer than 1.5 frames: dropped or stalled frames."""
    gaps = []
    for i in range(1, len(pts_ms)):
        d = pts_ms[i] - pts_ms[i - 1]
        if d > 1.5 * nominal:
            gaps.append({"after_frame": i - 1, "at_s": round(pts_ms[i - 1] / 1000, 3),
                         "gap_ms": round(d, 3), "missing": int(round(d / nominal)) - 1})
    return gaps


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def build_manifest(take_dir, take, exit_code, stop_reason):
    """Assemble manifest.json content from the files rpicam-vid left behind."""
    meta = scan_metadata(os.path.join(take_dir, "metadata.json")) or {
        "frames": 0, "first_wc_ns": None, "last_wc_ns": None, "sensor_ts_ns": []}
    pts_path = os.path.join(take_dir, "video.pts")
    pts = parse_pts(pts_path)
    pts_source = "save-pts"
    if pts is None or (meta["frames"] and len(pts) < meta["frames"]):
        # rpicam-vid writes --save-pts only at exit; the exit crash can lose it.
        rebuilt = pts_from_sensor_ts(meta["sensor_ts_ns"])
        if rebuilt:
            write_pts(pts_path, rebuilt)
            pts, pts_source = rebuilt, "rebuilt-from-metadata"
        else:
            pts_source = "missing"
    pts = pts or []
    sizes = {}
    for name in ("video.h264", "video.pts", "metadata.json", "rpicam.log"):
        p = os.path.join(take_dir, name)
        if os.path.exists(p):
            sizes[name] = os.path.getsize(p)
    gaps = find_gaps(pts)
    return {
        "schema": 1,
        "camera": take["camera"],
        "take_id": take["take_id"],
        "sync_role": take["role"],
        "settings": take["settings"],
        "agent_version": VERSION,
        "armed_at": take["armed_at"],
        "sync_at": take.get("sync_at"),
        "sync_difference_us": take.get("sync_difference_us"),
        "stopped_at": take.get("stopped_at"),
        "stop_reason": stop_reason,
        "first_frame_wallclock_ns": meta["first_wc_ns"],
        "last_frame_wallclock_ns": meta["last_wc_ns"],
        "frames": len(pts),
        "metadata_frames": meta["frames"],
        "duration_s": round(pts[-1] / 1000, 3) if pts else 0,
        "pts_source": pts_source,
        "gaps": gaps,
        "dropped_frames": sum(g["missing"] for g in gaps),
        "exit_code": exit_code,
        "exit_clean": exit_code == 0,
        "files": sizes,
        "sha256": None,
    }


# ---------------------------------------------------------------- system probes

def _run(cmd, timeout=3):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def probe_system(takes_dir):
    st = {}
    m = re.search(r"temp=([\d.]+)", _run(["vcgencmd", "measure_temp"]))
    st["temp_c"] = float(m.group(1)) if m else None
    m = re.search(r"throttled=(0x[0-9a-fA-F]+)", _run(["vcgencmd", "get_throttled"]))
    st["throttled"] = int(m.group(1), 16) if m else None
    try:
        du = shutil.disk_usage(takes_dir)
        st["free_gb"] = round(du.free / 1e9, 2)
    except OSError:
        st["free_gb"] = None
    st["free_hours"] = round(st["free_gb"] * 1e9 * 8 / 16e6 / 3600, 1) if st["free_gb"] else None
    tr = _run(["chronyc", "-c", "tracking"]).strip().split(",")
    if len(tr) > 13:
        st["clock_offset_ms"] = round(float(tr[4]) * 1000, 3)
        st["clock_leap"] = tr[13]
    else:
        st["clock_offset_ms"], st["clock_leap"] = None, None
    srcs = _run(["chronyc", "-c", "-n", "sources"])
    st["clock_synced"] = any(line.split(",")[1:2] == ["*"] for line in srcs.splitlines())
    link = _run(["/usr/sbin/iw", "dev", "wlan0", "link"])
    m = re.search(r"signal:\s*(-?\d+)", link)
    st["wifi_dbm"] = int(m.group(1)) if m else None
    m = re.search(r"tx bitrate:\s*([\d.]+)", link)
    st["wifi_mbps"] = float(m.group(1)) if m else None
    ps = _run(["/usr/sbin/iw", "dev", "wlan0", "get", "power_save"])
    st["wifi_powersave"] = None if not ps else ("on" in ps.split(":")[-1])
    return st


def mediamtx_active():
    return _run(["systemctl", "is-active", "mediamtx"]).strip() == "active"


def mediamtx_ready(api):
    """True once MediaMTX's camera path is publishing (the stream OBS reads)."""
    try:
        with urllib.request.urlopen(api + "/v3/paths/get/cam", timeout=1) as r:
            return bool(json.load(r).get("ready"))
    except Exception:  # noqa: BLE001 - not up yet
        return False


def mediamtx_ctl(action):
    r = subprocess.run(["sudo", "-n", "systemctl", action, "mediamtx"],
                       capture_output=True, text=True, timeout=20)
    return r.returncode == 0, r.stderr.strip()


# ---------------------------------------------------------------- agent

class StatusLed:
    """Blinks the board LED from a thread: armed = short blip each second,
    recording/stopping = steady 2 Hz flash, otherwise the kernel default trigger.

    Blinking in user space (trigger "none" + brightness) rather than the kernel
    "timer" trigger, because timer's delay_on/delay_off files are recreated
    root-owned on every trigger change and a udev rule can't open them up.
    Every failure is swallowed: the LED must never get in the way of a take.
    """
    PATTERNS = {"armed": (0.1, 0.9), "recording": (0.25, 0.25), "stopping": (0.25, 0.25)}

    def __init__(self, path):
        self.path = path
        self.ok = bool(path) and os.access(os.path.join(path, "brightness"), os.W_OK) \
            and os.access(os.path.join(path, "trigger"), os.W_OK)
        self.default_trigger = "actpwr"
        if self.ok:
            try:
                with open(os.path.join(path, "trigger")) as f:
                    m = re.search(r"\[([^\]]+)\]", f.read())
                if m and m.group(1) != "none":
                    self.default_trigger = m.group(1)
                else:   # left blinking by a camnode that died mid-take
                    self._write("trigger", self.default_trigger)
            except OSError:
                self.ok = False
        self.pattern = None
        self._wake = threading.Event()
        if self.ok:
            threading.Thread(target=self._run, daemon=True).start()

    def _write(self, name, value):
        try:
            with open(os.path.join(self.path, name), "w") as f:
                f.write(value)
        except OSError:
            pass

    def set(self, state):
        pattern = self.PATTERNS.get(state)
        if self.ok and pattern != self.pattern:
            self.pattern = pattern
            self._wake.set()

    def _run(self):
        blinking = False
        while True:
            pattern = self.pattern
            if pattern is None:
                if blinking:
                    self._write("trigger", self.default_trigger)
                    blinking = False
                self._wake.wait()
                self._wake.clear()
                continue
            if not blinking:
                self._write("trigger", "none")
                blinking = True
            self._write("brightness", "1")
            if self._wake.wait(pattern[0]):
                self._wake.clear()
                continue
            self._write("brightness", "0")
            if self._wake.wait(pattern[1]):
                self._wake.clear()

    def restore(self):
        if self.ok:
            self.pattern = None
            self._wake.set()
            self._write("trigger", self.default_trigger)


class Busy(Exception):
    pass


class Agent:
    def __init__(self, cfg, probe=probe_system, live_active=mediamtx_active,
                 live_ctl=mediamtx_ctl, live_ready=None, live_patch=None):
        self.cfg = cfg
        self.led = StatusLed(cfg.get("led_path"))
        self.lock = threading.RLock()
        self.proc = None
        self.take = None          # dict while armed/recording/stopping
        self.state = "idle"       # idle | switching | armed | recording | stopping
        self.last_error = None
        self.last_take = None     # manifest of the most recent take
        self.still_proc = None    # rpicam-still while a snapshot runs
        self._probe = probe
        self._live_active = live_active
        self._live_ctl = live_ctl
        self._live_ready = live_ready or (lambda: mediamtx_ready(cfg["mediamtx_api"]))
        self._live_patch = live_patch or (lambda params: mediamtx_patch(cfg["mediamtx_api"], params))
        self._sys = {}
        self._sys_at = 0
        os.makedirs(cfg["takes_dir"], exist_ok=True)
        self.recover_incomplete()
        self.delete_verified()      # marked while recording, or before this camnode deleted at once

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = value
        self.led.set(value)

    # -- takes on disk
    def take_dir(self, take_id):
        return os.path.join(self.cfg["takes_dir"], take_id)

    def recover_incomplete(self):
        """Finalize takes left without a manifest (agent restarted mid-take)."""
        for name in sorted(os.listdir(self.cfg["takes_dir"])):
            d = self.take_dir(name)
            info = os.path.join(d, "take.json")
            if os.path.isfile(info) and not os.path.exists(os.path.join(d, "manifest.json")):
                with open(info) as f:
                    take = json.load(f)
                man = build_manifest(d, take, None, "recovered-after-agent-restart")
                write_json_atomic(os.path.join(d, "manifest.json"), man)
                self.last_take = man
        # Takes still without a checksum: the agent (or the camera) restarted while _hash ran
        # after STOP (cam1, 2026-11-17: rebooted 3 min after a 20 min take). Without it the
        # hub never gets past checking the pull and the camera never frees the space.
        unhashed = [self.take_dir(name) for name in sorted(os.listdir(self.cfg["takes_dir"]))
                    if needs_hash(self.take_dir(name))]
        if unhashed:
            threading.Thread(target=self._hash_all, args=(unhashed,), daemon=True).start()

    def _hash_all(self, dirs):
        """Hash takes one after another (one at a time keeps the SD card free for recording)."""
        for d in dirs:
            try:
                self._hash(d)
            except (OSError, ValueError) as e:
                sys.stderr.write("hash %s: %s\n" % (d, e))

    def list_takes(self):
        out = []
        for name in sorted(os.listdir(self.cfg["takes_dir"])):
            p = os.path.join(self.take_dir(name), "manifest.json")
            if os.path.isfile(p):
                with open(p) as f:
                    m = json.load(f)
                v = os.path.join(self.take_dir(name), "verified.json")
                if os.path.isfile(v):
                    with open(v) as f:
                        m["verified"] = json.load(f)
                out.append(m)
        return out

    def mark_verified(self, take_id, info):
        """The hub has a verified copy on the STUDIO drive; the take may now be deleted lazily."""
        if not TAKE_ID_RE.match(take_id or ""):
            raise ValueError("bad take_id")
        d = self.take_dir(take_id)
        if not os.path.isfile(os.path.join(d, "manifest.json")):
            raise ValueError("no finished take %s" % take_id)
        rec = {"at": time.time_ns(), "mov_sha256": info.get("mov_sha256"), "path": info.get("path")}
        write_json_atomic(os.path.join(d, "verified.json"), rec)
        if self.state == "idle":
            self.delete_verified()          # while recording: the idle housekeeping does it
        return rec

    def free_gb(self):
        return shutil.disk_usage(self.cfg["takes_dir"]).free / 1e9

    def delete_verified(self):
        """Delete every take the hub has verified on the STUDIO drive (verified.json): the
        drive has it, so the camera doesn't keep a copy (user decision 2026-11-17; it used to
        keep them until space ran low). Never touches unverified takes or the take being
        recorded. Returns the deleted ids."""
        deleted = []
        with self.lock:
            current = self.take["take_id"] if self.take else None
            for n in sorted(os.listdir(self.cfg["takes_dir"])):
                if n == current or not os.path.isfile(os.path.join(self.take_dir(n), "verified.json")):
                    continue
                shutil.rmtree(self.take_dir(n))
                deleted.append(n)
                self.log_deleted(n, "verified on the drive")
        return deleted

    def delete_take(self, take_id):
        """The hub page's "delete take" (user, 2026-11-21): the take goes, verified or not,
        e.g. one whose ingest failed. Never the take being recorded. A take that isn't here
        is fine (deleted already). Returns {"deleted": bool}."""
        if not TAKE_ID_RE.match(take_id or ""):
            raise ValueError("bad take_id")
        with self.lock:
            if self.take and self.take["take_id"] == take_id:
                raise Busy("take %s is being recorded" % take_id)
            d = self.take_dir(take_id)
            if not os.path.isdir(d):
                return {"deleted": False}
            shutil.rmtree(d)
            self.log_deleted(take_id, "deleted on the hub page")
        return {"deleted": True}

    def log_deleted(self, take_id, why):
        with open(os.path.join(os.path.dirname(self.cfg["takes_dir"].rstrip("/")), "deleted.jsonl"), "a") as f:
            f.write(json.dumps({"take_id": take_id, "at": time.time_ns(), "why": why,
                                "free_gb_after": round(self.free_gb(), 2)}) + "\n")

    def housekeeping_forever(self, stop, every_s=600):
        while not stop.wait(every_s):
            if self.state == "idle":
                try:
                    self.delete_verified()      # ones marked while recording
                except OSError:
                    pass

    # -- status
    def system(self):
        now = time.monotonic()
        if now - self._sys_at > 1.0:
            self._sys = self._probe(self.cfg["takes_dir"])
            self._sys_at = now
        return self._sys

    def mode(self):
        if self.state != "idle":
            return self.state
        if self._live_active():
            return "live" if self._live_ready() else "live-starting"
        return "idle"

    def status(self):
        with self.lock:
            take = None
            if self.take:
                t = self.take
                wc0 = first_wallclock(os.path.join(t["dir"], "metadata.json"))
                elapsed = (time.time() - wc0 / 1e9) if wc0 else 0
                gc = t.setdefault("gap_counter", LiveGapCounter(os.path.join(t["dir"], "metadata.json")))
                gc.update()
                take = {"take_id": t["take_id"], "role": t["role"],
                        "sync_difference_us": t.get("sync_difference_us"),
                        "first_frame_wallclock_ns": wc0,
                        "elapsed_s": round(max(elapsed, 0), 1),
                        "frames_est": int(max(elapsed, 0) * 30),
                        "frames": gc.frames, "dropped_so_far": gc.dropped, "gaps_so_far": gc.gaps}
            return {"camera": self.cfg["host"], "agent_version": VERSION,
                    "mode": self.mode(), "take": take, "last_error": self.last_error,
                    "last_take": _summary(self.last_take), "time_ns": time.time_ns(),
                    "settings": {k: self.cfg["settings"][k] for k in SNAPSHOT_KEYS},
                    **self.system()}

    # -- live
    def live(self, on):
        with self.lock:
            if self.state != "idle":
                raise Busy("camera is %s" % self.state)
            self.state = "switching"
        ready = None
        try:
            # Not under the lock: systemctl can take seconds and /status must stay responsive.
            ok, err = self._live_ctl("start" if on else "stop")
            if ok and on:
                # Return only once the stream is up, so OBS doesn't connect to nothing.
                end = time.monotonic() + self.cfg["live_ready_timeout_s"]
                while not (ready := self._live_ready()) and time.monotonic() < end:
                    time.sleep(0.25)
        finally:
            with self.lock:
                self.state = "idle"
        if not ok:
            raise RuntimeError("systemctl failed: %s" % err)
        if on and not ready:
            raise RuntimeError("live stream not ready after %ss" % self.cfg["live_ready_timeout_s"])
        return self.mode()

    # -- live settings (Track B)
    def live_settings(self, settings):
        params = {}
        for k, v in (settings or {}).items():
            if k not in LIVE_KEYS:
                raise ValueError("can't change %s on a live stream" % k)
            params[LIVE_KEYS[k]] = [float(x) for x in v] if k == "awb_gains" else float(v)
        if not self._live_active():
            raise Busy("live mode is off")
        self._live_patch(params)
        return {"applied": params}

    # -- snapshot (Track B)
    def snapshot(self, settings=None, auto=False):
        """(jpeg, metadata): a still with the take settings (overridable, e.g. a focus being
        tried), or auto-metered (calibration). Idle only: while it runs the camera is busy,
        so state "snapshot" makes arm/live refuse."""
        bad = set(settings or {}) - set(SNAPSHOT_KEYS)
        if bad:
            raise ValueError("unknown settings: %s" % ", ".join(sorted(bad)))
        with self.lock:
            if self.state != "idle":
                raise Busy("camera is %s" % self.state)
            if self._live_active():
                raise Busy("live mode is on")
            self.state = "snapshot"
        stuck = False
        try:
            s = dict(self.cfg["settings"], **(settings or {}))
            mpath = os.path.join(self.cfg["still_meta_dir"], "camnode-still-%d.json" % os.getpid())
            p = subprocess.Popen(build_still_cmd(self.cfg, s, auto, mpath), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.still_proc = p
            try:
                out, err = p.communicate(timeout=self.cfg["snapshot_timeout_s"])
            except subprocess.TimeoutExpired:
                # 2026-11-16, cam2: rpicam-still froze inside the camera driver (kernel: task
                # CameraManager blocked for more than 120 s); such a process can't be killed
                # and the camera stays unusable until a reboot. Say so instead of hanging.
                p.kill()
                try:
                    p.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    stuck = True
                    raise RuntimeError(STUCK_MSG) from None
                raise RuntimeError("rpicam-still took over %ds" % self.cfg["snapshot_timeout_s"]) from None
            if p.returncode != 0 or not out.startswith(b"\xff\xd8"):
                tail = err.decode(errors="replace").strip().splitlines()[-1:] or ["no output"]
                raise RuntimeError("rpicam-still failed (code %s): %s" % (p.returncode, tail[0]))
            r = subprocess.CompletedProcess(p.args, p.returncode, out, err)
            meta = {}
            try:
                with open(mpath) as f:
                    txt = f.read().strip()
                m = json.loads(txt)
                m = m[-1] if isinstance(m, list) else m
                meta = {k: m[k] for k in META_KEYS if k in m}
            except (OSError, ValueError, IndexError):
                pass
            finally:
                try:
                    os.remove(mpath)
                except OSError:
                    pass
            meta["settings"] = s if not auto else {k: v for k, v in s.items() if k in ("shutter_us", "lens_position")}
            return r.stdout, meta
        finally:
            with self.lock:
                self.still_proc = None
                if stuck:
                    self.state, self.last_error = "stuck", STUCK_MSG
                else:
                    self.state = "idle"

    # -- takes
    def arm(self, take_id, role, settings=None, sync_port=None):
        if not TAKE_ID_RE.match(take_id or ""):
            raise ValueError("bad take_id")
        if role not in ("client", "server"):
            raise ValueError("role must be client or server")
        with self.lock:
            if self.state != "idle":
                raise Busy("camera is %s" % self.state)
            if self._live_active():
                raise Busy("live mode is on")
            free = self.free_gb()
            if free < self.cfg["min_free_gb"]:
                raise Busy("only %.1f GB free" % free)
            d = self.take_dir(take_id)
            if os.path.exists(d):
                raise Busy("take %s already exists" % take_id)
            os.makedirs(d)
            s = dict(self.cfg["settings"], **(settings or {}))
            take = {"camera": self.cfg["host"], "take_id": take_id, "role": role,
                    "settings": s, "armed_at": time.time_ns(), "dir": d}
            write_json_atomic(os.path.join(d, "take.json"),
                              {k: v for k, v in take.items() if k not in ("dir", "gap_counter")})
            tuning = sync_tuning(self.cfg["tuning_file"], sync_port, d) if sync_port else None
            take["sync_port"] = sync_port if tuning else None
            log = open(os.path.join(d, "rpicam.log"), "w")
            self.proc = subprocess.Popen(build_cmd(self.cfg, role, d, s, tuning), stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         text=True, errors="replace")
            self.take, self.state, self.last_error = take, "armed", None
            threading.Thread(target=self._watch, args=(self.proc, take, log), daemon=True).start()
        return {"take_id": take_id, "state": "armed"}

    def _watch(self, proc, take, log):
        for line in proc.stdout:
            log.write(line)
            log.flush()
            m = SYNC_RE.search(line)
            if m:
                with self.lock:
                    if self.take is take and self.state == "armed":
                        # Difference = this camera's start error vs the server (S2:
                        # equals the first-frame wall-clock offset; up to -969 ms seen).
                        take["sync_difference_us"] = int(m.group(1)) if m.group(1) else None
                        take["sync_at"] = time.time_ns()
                        self.state = "recording"
        rc = proc.wait()
        proc.stdout.close()
        log.close()
        with self.lock:
            if self.take is take and self.state != "stopping":
                # Exited without being asked: camera busy, crash, or SD full.
                self.last_error = "rpicam-vid exited unexpectedly (code %s)" % rc
                self._finalize(take, rc, "unexpected-exit")

    def stop(self):
        with self.lock:
            if self.state not in ("armed", "recording"):
                raise Busy("not recording (camera is %s)" % self.mode())
            proc, take = self.proc, self.take
            take["stopped_at"] = time.time_ns()
            self.state = "stopping"
        sig = getattr(signal, self.cfg["stop_signal"])
        proc.send_signal(sig)
        reason = "stop"
        try:
            rc = proc.wait(timeout=self.cfg["stop_timeout_s"])
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()
            reason = "stop-killed-after-timeout"
        with self.lock:
            return self._finalize(take, rc, reason)

    def _finalize(self, take, rc, reason):
        flush_to_card(take["dir"])
        man = build_manifest(take["dir"], take, rc, reason)
        write_json_atomic(os.path.join(take["dir"], "manifest.json"), man)
        self.last_take = man
        self.take, self.proc, self.state = None, None, "idle"
        threading.Thread(target=self._hash, args=(take["dir"],), daemon=True).start()
        return man

    def _hash(self, d):
        """sha256 of the video after STOP, in the background, at low priority."""
        try:
            os.nice(10)
        except OSError:
            pass
        video = os.path.join(d, "video.h264")
        if not os.path.exists(video):
            return
        h = hashlib.sha256()
        with open(video, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        with self.lock:
            p = os.path.join(d, "manifest.json")
            with open(p) as f:
                man = json.load(f)
            man["sha256"] = {"video.h264": h.hexdigest()}
            write_json_atomic(p, man)
            if self.last_take and self.last_take["take_id"] == man["take_id"]:
                self.last_take = man


def flush_to_card(d):
    """Make sure a finished take's files are on the SD card, not just in memory. Linux keeps
    the last seconds of writes in RAM for up to ~30 s: cam1 rebooted 3 min after a 20 min take
    (2026-11-17) and both video.h264 and video.pts came back cut at a 4 KB page, ~6 s short,
    which no ingest can verify. fsync at STOP (a few seconds at most) closes that window."""
    for name in sorted(os.listdir(d)):
        path = os.path.join(d, name)
        if not os.path.isfile(path):
            continue
        try:
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError as e:
            sys.stderr.write("fsync %s: %s\n" % (path, e))
    try:
        fd = os.open(d, os.O_RDONLY)          # the folder's entries too
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def needs_hash(d):
    """A finished take (manifest written) whose video has no checksum yet."""
    p = os.path.join(d, "manifest.json")
    if not (os.path.isfile(p) and os.path.isfile(os.path.join(d, "video.h264"))):
        return False
    try:
        with open(p) as f:
            return not json.load(f).get("sha256")
    except (OSError, ValueError):
        return False


def _summary(man):
    if not man:
        return None
    keys = ("take_id", "frames", "duration_s", "dropped_frames", "exit_code", "pts_source",
            "sync_difference_us",
            "first_frame_wallclock_ns", "stop_reason")
    return {k: man.get(k) for k in keys}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    agent = None
    server_version = "camnode/" + VERSION

    def log_message(self, fmt, *args):
        if not self.path.startswith("/status"):
            sys.stderr.write("%s %s\n" % (self.command, fmt % args))

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def parse_request(self):
        """Only the hub and tools talk to camnode, and they send no Origin. A request with
        one comes from a web page (any site open on the network), so it is refused."""
        if not super().parse_request():
            return False
        if self.headers.get("Origin") is not None:
            self.close_connection = True        # the body, if any, isn't read
            self._send(403, {"error": "requests from web pages are refused"})
            return False
        return True

    def do_GET(self):
        a = self.agent
        if self.path == "/status":
            return self._send(200, a.status())
        if self.path == "/takes":
            return self._send(200, a.list_takes())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        a = self.agent
        try:
            if self.path == "/snapshot":
                b = self._body()
                jpg, meta = a.snapshot(b.get("settings"), bool(b.get("auto")))
                self.send_response(200)
                self.send_header("X-Metadata", json.dumps(meta))
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpg)))
                self.end_headers()
                self.wfile.write(jpg)
                return
            if self.path == "/take/arm":
                b = self._body()
                return self._send(200, a.arm(b.get("take_id"), b.get("role"), b.get("settings"), b.get("sync_port")))
            if self.path == "/take/stop":
                return self._send(200, a.stop())
            m = re.match(r"^/takes/([^/]+)/verified$", self.path)
            if m:
                return self._send(200, a.mark_verified(m.group(1), self._body()))
            m = re.match(r"^/takes/([^/]+)/delete$", self.path)
            if m:
                return self._send(200, a.delete_take(m.group(1)))
            if self.path == "/live/settings":
                return self._send(200, a.live_settings(self._body().get("settings")))
            if self.path in ("/live/start", "/live/stop"):
                return self._send(200, {"mode": a.live(self.path.endswith("start"))})
        except Busy as e:
            return self._send(409, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - report, keep serving
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "not found"})


def main():
    cfg = load_config(os.environ.get("CAMNODE_CONFIG", "/etc/camnode/config.json"))
    Handler.agent = Agent(cfg)
    hk_stop = threading.Event()
    threading.Thread(target=Handler.agent.housekeeping_forever, args=(hk_stop,), daemon=True).start()
    srv = ThreadingHTTPServer(("", cfg["port"]), Handler)
    srv.daemon_threads = True

    def shutdown(*_):
        a = Handler.agent
        p = a.still_proc         # let a snapshot finish (~1.5 s) rather than cut it off
        if p is not None:
            try:
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
        if a.state in ("armed", "recording"):
            try:
                a.stop()   # never leave a take without a manifest
            except Exception:  # noqa: BLE001
                pass
        a.led.restore()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    sys.stderr.write("camnode %s on :%d, takes in %s\n" % (VERSION, cfg["port"], cfg["takes_dir"]))
    srv.serve_forever()


if __name__ == "__main__":
    main()
