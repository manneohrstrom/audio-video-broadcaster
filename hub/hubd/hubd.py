#!/usr/bin/env python3
"""hubd: studio hub control service (Phase 2).

Polls every camnode, runs REC/STOP/LIVE across the rig, serves the web UI.
Standard library only. Config: /etc/hubd/config.json (rendered by Ansible).
"""
import contextlib
import json
import os
import glob
import re
import shutil
import signal
import subprocess
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio  # noqa: E402
import budget  # noqa: E402
import calib  # noqa: E402
import calibrate  # noqa: E402
import lamp  # noqa: E402
import names  # noqa: E402
import phones  # noqa: E402
import scratch  # noqa: E402
import editor_projects  # noqa: E402
import health_history  # noqa: E402

VERSION = "0.38.0"
SHOT_LINGER_S = 1.5         # the camera icon stays on this long after a still
HERE = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG = {
    "port": 80,
    "bind": "",
    "nodes": ["cam1", "cam2", "cam3", "cam4"],
    "node_port": 8080,
    "sync_server": "cam1",
    "poll_s": 2.0,
    "state_dir": "/var/lib/hubd",
    "ingest_dir": "/var/lib/hubd/ingest",
    # YouTube (hub-upload, hub/ingest/youtube_upload.py): the login and the upload folder.
    "youtube_credentials_path": "/var/lib/hubd/youtube/credentials.json",
    "youtube_spool_dir": "/var/lib/hubd/youtube/uploads",
    "drive_mount": "/mnt/studio",       # hub-ingest owns mounting; hubd only reads
    "static_dir": os.path.join(HERE, "static"),
    # Phone cameras (docs/phone-plan.md): footage staged here during a take.
    "phones_dir": None,                 # default: <state_dir>/phones
    # Fixed capture->page delay per phone model (ms), subtracted from first-frame times.
    # Spike 2026-11-15 on a Pixel 3: ~40 ms. One tested phone stands for all.
    "phone_capture_delay_ms": {"default": 40},
    "client_head_start_s": 2.0,
    "sync_timeout_s": 20.0,
    # A camera counts as offline only after this many missed polls in a row (2 s apart).
    # One slow answer (e.g. Wi-Fi busy with ingest pulls) just marks it "slow".
    "offline_after_misses": 3,
    "checks": {"max_offset_ms": 5.0, "max_temp_c": 70.0, "min_free_hours": 1.0},
    # A client can report "Sync achieved" while starting late (S2: up to 969 ms).
    "max_sync_difference_us": 1000,
    # "Red only when in sync": a follower that starts more than this far from the server
    # (2026-11-16: Wi-Fi multicast delivered the server's sync packets 8-21 s late) makes
    # REC stop and start again by itself, up to resync_retries times; then it records
    # anyway, with a warning and a flashing light. 1 frame = 33 333 us.
    "resync_limit_us": 33333,
    "resync_retries": 2,
    "resync_pause_s": 1.0,
    # Record-time limit (budget.py): shown on the page; at zero hubd stops the take.
    # Rates are starting guesses (Pi measured ~8 Mbps, phones ~15); during a take the
    # measured rates take over after a minute.
    "budget": {"hub_reserve_gb": 15.0, "node_reserve_gb": 1.0, "pi_mbps": 10.0, "phone_mbps": 15.0,
               "warn_s": 300, "min_rec_s": 120, "auto_stop": True, "drive_reserve_gb": 0.5},
    # Recording light (lamp.py, Phase 4 C1). type null = no bulb. See lamp.DEFAULT_LAMP.
    "lamp": {"type": None},
    # Bounce uploads and the hub mic recording (audio.py, scratch.py; Phase 4 Track A),
    # staged here per take and copied to the drive by hub-ingest. Default <state_dir>/audio.
    "audio_dir": None,
    "scratch": {},          # see scratch.DEFAULT_SCRATCH; device null = first USB mic
}

# vcgencmd get_throttled: bits 0-3 are "now" (under-voltage, freq capped,
# throttled, soft temp limit). Bits 16-19 are "has happened since boot".
THROTTLED_NOW_MASK = 0xF


def _read(path, conv=str):
    try:
        with open(path) as f:
            return conv(f.read().strip())
    except (OSError, ValueError):
        return None


def memory_mb(meminfo="/proc/meminfo"):
    """(total, available, swap used) in MB, or Nones (not on Linux)."""
    values = {}
    try:
        with open(meminfo) as f:
            for line in f:
                name, _, rest = line.partition(":")
                values[name] = int(rest.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        return None, None, None
    swap_used = values["SwapTotal"] - values["SwapFree"] if "SwapTotal" in values and "SwapFree" in values else None
    return values.get("MemTotal"), values.get("MemAvailable"), swap_used


def hub_health(state_dir, sysfs="/sys", vcgencmd="vcgencmd", meminfo="/proc/meminfo"):
    """The hub's own temperature, fan, clock, throttling, load, memory, swap and SD space.
    Every field is None if its source is missing (e.g. not on a Pi)."""
    t = _read(os.path.join(sysfs, "class/thermal/thermal_zone0/temp"), int)
    fan = None
    for h in glob.glob(os.path.join(sysfs, "class/hwmon/hwmon*")):
        if _read(os.path.join(h, "name")) == "pwmfan":
            fan = _read(os.path.join(h, "fan1_input"), int)
    mhz = _read(os.path.join(sysfs, "devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"), int)
    try:
        out = subprocess.run([vcgencmd, "get_throttled"], capture_output=True, text=True, timeout=2).stdout
        throttled = int(out.strip().split("=")[1], 16)
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        throttled = None
    try:
        free_gb = round(shutil.disk_usage(state_dir).free / 1e9, 1)
    except OSError:
        free_gb = None
    mem_total, mem_available, swap_used = memory_mb(meminfo)
    return {"temp_c": round(t / 1000, 1) if t is not None else None, "fan_rpm": fan,
            "cpu_mhz": mhz // 1000 if mhz else None, "throttled": throttled,
            "load": round(os.getloadavg()[0], 2), "free_gb": free_gb,
            "mem_total_mb": mem_total, "mem_available_mb": mem_available, "swap_used_mb": swap_used}


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        checks = dict(cfg["checks"], **user.pop("checks", {}))
        bud = dict(cfg["budget"], **user.pop("budget", {}))
        cfg.update(user)
        cfg["checks"], cfg["budget"] = checks, bud
    cfg["lamp"] = lamp.merge_config(cfg.get("lamp"))
    return cfg


class HttpNodes:
    """Talks to camnode agents over HTTP. Swapped for a fake in tests."""

    def __init__(self, port):
        self.port = port

    def _url(self, node, path):
        return "http://%s.local:%d%s" % (node, self.port, path)

    def get(self, node, path, timeout=1.5):
        with urllib.request.urlopen(self._url(node, path), timeout=timeout) as r:
            return json.load(r)

    def post(self, node, path, body=None, timeout=10, raw=False):
        req = urllib.request.Request(self._url(node, path), method="POST",
                                     data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read() if raw else json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error", str(e))
            except Exception:  # noqa: BLE001
                msg = str(e)
            raise NodeError("%s: %s" % (node, msg)) from None
        except OSError as e:
            raise NodeError("%s: %s" % (node, e)) from None


def _still(self, node, body, timeout=15):
    """POST /snapshot: (jpeg, metadata) from a camera."""
    req = urllib.request.Request(self._url(node, "/snapshot"), method="POST", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), json.loads(r.headers.get("X-Metadata") or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("error", str(e))
        except Exception:  # noqa: BLE001
            msg = str(e)
        raise NodeError("%s: %s" % (node, msg)) from None
    except OSError as e:
        raise NodeError("%s: %s" % (node, e)) from None


HttpNodes.still = _still


class NodeError(Exception):
    pass


class Refused(Exception):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def log(msg):
    """One line to the journal (stderr)."""
    sys.stderr.write(msg + "\n")


def arm_problems(name, st, checks, still_is_idle=False):
    """Reasons a node may not start a take. st is its last /status or None.
    still_is_idle: a still being taken ("snapshot": the calibration watcher, every 15 s
    while a hub page is open) is no problem. True for the status the page shows (it made
    the cards grow and shrink); REC itself stays strict, after waiting for stills to end."""
    if st is None:
        return ["%s offline" % name]
    p = []
    ready = ("idle", "live", "snapshot") if still_is_idle else ("idle", "live")
    if st.get("mode") not in ready:              # switching/armed/recording/stopping
        p.append("%s is %s" % (name, st.get("mode")))
    if not st.get("clock_synced"):
        p.append("%s clock not synced to hub" % name)
    off = st.get("clock_offset_ms")
    if off is None or abs(off) > checks["max_offset_ms"]:
        p.append("%s clock offset %s ms" % (name, off))
    t = st.get("temp_c")
    if t is None or t >= checks["max_temp_c"]:
        p.append("%s temperature %s °C" % (name, t))
    fh = st.get("free_hours")
    if fh is None or fh < checks["min_free_hours"]:
        p.append("%s only %s h of space" % (name, fh))
    if st.get("wifi_powersave") is not False:
        p.append("%s Wi-Fi power save is not off" % name)
    if (st.get("throttled") or 0) & THROTTLED_NOW_MASK:
        p.append("%s throttled (0x%x)" % (name, st["throttled"]))
    return p


def sync_warnings(diffs, limit_us):
    """diffs: {camera: sync_difference_us}. Human-readable warnings for bad starts."""
    out = []
    for n, d in sorted(diffs.items()):
        if d is not None and abs(d) > limit_us:
            frames = abs(d) / (1e6 / 30)
            out.append("%s started %.1f ms %s (%.1f frames)" % (n, abs(d) / 1000, "late" if d < 0 else "early", frames))
    return out


def exit_ok(c):
    """A camera's exit is fine: code 0, or the known rpicam exit crash (SIGABRT,
    "malloc_consolidate") after every file was written (the PTS file came out complete)."""
    return c.get("exit_code") in (0, None) or (c.get("exit_code") in (-6, 134) and c.get("pts_source") == "save-pts")


def drive_view(takes, index):
    """Fit the take list to the STUDIO drive (stick-db-plan S3, user decisions 2026-11-17).
    Takes ingested to another drive are taken out and counted ("3 takes on another
    drive"). While the indexed drive is out, its takes stay listed, marked drive_absent
    (greyed: no playback, no editing). Takes still in flight are always listed."""
    drive_id = index.get("drive_id")
    if not drive_id:
        return {"drive_id": None, "present": None, "other_drive_takes": 0}
    elsewhere = [take_id for take_id, take in takes.items()
                 if take.get("complete") and take.get("drive_id") and take["drive_id"] != drive_id]
    for take_id in elsewhere:
        del takes[take_id]
    present = bool(index.get("present"))
    if not present:
        for take in takes.values():
            if take.get("complete"):
                take["drive_absent"] = True
    return {"drive_id": drive_id, "present": present, "other_drive_takes": len(elsewhere)}


def take_summary(take_id, manifests, errors, limit_us=1000):
    cams = {}
    wcs = []
    for name, m in sorted(manifests.items()):
        cams[name] = {k: m.get(k) for k in ("frames", "duration_s", "dropped_frames", "exit_code",
                                            "pts_source", "sync_role", "first_frame_wallclock_ns",
                                            "sync_difference_us")}
        if m.get("first_frame_wallclock_ns"):
            wcs.append(m["first_frame_wallclock_ns"])
    frames = [c["frames"] for c in cams.values() if c["frames"] is not None]
    warn = sync_warnings({n: c["sync_difference_us"] for n, c in cams.items()}, limit_us)
    return {
        "sync_warnings": warn,
        "take_id": take_id,
        "cameras": cams,
        "errors": errors,
        "start_spread_us": round((max(wcs) - min(wcs)) / 1e3, 1) if len(wcs) > 1 else None,
        "frame_count_spread": (max(frames) - min(frames)) if frames else None,
        "dropped_frames_total": sum(c["dropped_frames"] or 0 for c in cams.values()),
        "all_clean": (not errors and not warn
                      and all(exit_ok(c) and not c["dropped_frames"] for c in cams.values())),
    }


class Hub:
    def __init__(self, cfg, nodes=None, clock=time.time, sleep=time.sleep):
        self.cfg = cfg
        self.nodes = nodes or HttpNodes(cfg["node_port"])
        self.clock, self.sleep = clock, sleep
        self.lock = threading.RLock()
        self.op_lock = threading.Lock()     # one REC/STOP/LIVE at a time
        self.status = {n: None for n in cfg["nodes"]}
        self.seen = {n: None for n in cfg["nodes"]}
        self.misses = {n: 0 for n in cfg["nodes"]}
        self.health = {}
        self._state = "idle"                # idle | starting | recording | stopping
        self.gen = 0                        # bumped on every state change (see poll_once)
        self.take = None
        self.message = None
        self.pool = ThreadPoolExecutor(max_workers=max(4, 2 * len(cfg["nodes"])))
        os.makedirs(cfg["state_dir"], exist_ok=True)
        self.log_path = os.path.join(cfg["state_dir"], "takes.json")
        self.phones = phones.Phones(cfg, clock=clock)
        self.names = names.Names(os.path.join(cfg["state_dir"], "names.json"))   # user's names: cameras, takes
        self.budget = budget.limits(None, 0, {}, {})
        self.card, self.calib_lock = None, threading.Lock()
        self.snaps_in_flight = 0            # camera snapshots running (REC waits for them)
        # The camera icon on the page: the hub starts every still itself, so it says when a
        # camera is taking one. The camera's own "snapshot" mode is over too quickly for the
        # 2 s node poll to catch (user 2026-11-17: the icon never showed).
        self.shooting = {n: 0 for n in cfg["nodes"]}       # stills/frame grabs in flight
        self.shot_at = {n: 0.0 for n in cfg["nodes"]}      # when the last one ended
        self.hold = 0                       # >0 while REC/LIVE takes the cameras: no new stills
        self.cam_locks = {n: threading.Lock() for n in cfg["nodes"]}   # one still per camera at a time
        self.cam_settings_path = os.path.join(cfg["state_dir"], "camera_settings.json")
        try:
            with open(self.cam_settings_path) as f:
                self.cam_settings = json.load(f)
        except (OSError, ValueError):
            self.cam_settings = {}
        self.calibrator = calibrate.Calibrator(self, clock=clock)
        self.phones.calib_watch = self.calibrator.watching
        # Background work started from the poll loop (tests run it inline).
        self.spawn = lambda fn, *a: threading.Thread(target=fn, args=a, daemon=True).start()
        self.lamp = lamp.Lamp(cfg.get("lamp"), self.lamp_inputs, log)
        adir = cfg.get("audio_dir") or os.path.join(cfg["state_dir"], "audio")
        self.audio = audio.Audio(adir, takes=self.take_ids, busy=self.rig_busy)
        self.history = health_history.HealthHistory()           # the page's sparklines
        # The editor (docs/editor-plan.md, M-E3): one song version at a time.
        self.editor = editor_projects.EditorProjects(
            self.audio, read_take_document=self.read_take_document, find_quick_edit=self.find_quick_edit,
            edits_dir=os.path.join(cfg["state_dir"], "edits"),
            thumbs_dir=os.path.join(cfg["state_dir"], "thumbs"),
            waveforms_dir=os.path.join(cfg["state_dir"], "waveforms"),
            proxies_dir=os.path.join(cfg["state_dir"], "proxies"),
            request_thumbnails=lambda take_id: self.ingest_command("thumbs", take=take_id),
            read_drive_edit=self.read_drive_edit, sync_job=self.sync_job, names=self.names)
        self.scratch = scratch.Scratch(cfg.get("scratch"), adir, log)

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = value
        self.gen += 1
        if getattr(self, "lamp", None):
            self.lamp.poke()

    # -- polling
    def poll_once(self):
        """Fetch every node's /status. If REC/STOP changed the rig state while
        the fetch was in flight, the answers are stale (e.g. "recording" read
        just before a STOP finished) and are dropped: storing them would make
        _adopt take over a take that has already ended."""
        with self.lock:
            gen = self.gen

        def one(n):
            try:
                return n, self.nodes.get(n, "/status")
            except Exception:  # noqa: BLE001 - offline is a state, not an error
                return n, None
        results = list(self.pool.map(one, self.cfg["nodes"]))
        with self.lock:
            if self.gen != gen:
                return
            for n, st in results:
                self._record(n, st)
            self._adopt()

    def _record(self, n, st):
        """Store one poll answer. A missed answer keeps the last good status until
        offline_after_misses polls in a row have failed. Changes are logged."""
        if st is not None:
            if self.status[n] is None or self.misses[n]:
                log("%s online%s" % (n, " again after %d missed polls" % self.misses[n] if self.misses[n] else ""))
            self.status[n], self.misses[n], self.seen[n] = st, 0, self.clock()
            return
        self.misses[n] += 1
        if self.status[n] is None:
            return
        if self.misses[n] >= self.cfg["offline_after_misses"]:
            log("%s offline (%d missed polls)" % (n, self.misses[n]))
            self.status[n] = None
        elif self.misses[n] == 1:
            log("%s slow: no answer within the poll timeout" % n)

    def _adopt(self):
        """After a hubd restart, take over a take the cameras are still recording."""
        with self.lock:
            if self.state != "idle":
                return
            # Fresh answers only: a status kept through missed polls may predate a STOP.
            active = {n: st["take"] for n, st in self.status.items()
                      if st and not self.misses[n] and st.get("mode") in ("armed", "recording") and st.get("take")}
            if not active:
                return
            take_id = max(t["take_id"] for t in active.values())
            names = [n for n in self.cfg["nodes"] if n in active and active[n]["take_id"] == take_id]
            server = next((n for n in names if active[n].get("role") == "server"), names[0])
            wc = [active[n].get("first_frame_wallclock_ns") for n in names]
            wc = [w for w in wc if w]
            self.take = {"take_id": take_id, "nodes": names, "server": server, "started_at": None,
                         "recording_at": min(wc) / 1e9 if wc else self.clock(),
                         "sync_warnings": [], "adopted": True}
            self.state = "recording"
            self.message = "Hub restarted during take %s; it is still recording." % take_id
            self.scratch.start(take_id)         # late: from now on only

    def take_ids(self):
        """Takes recorded here, plus takes that came in on the drive (adopted by hub-ingest)."""
        recorded = {e.get("take_id") for e in self.read_log() if e.get("take_id") and not e.get("aborted")}
        return recorded | set(self.read_ingest_file().get("takes") or {})

    def read_ingest_file(self):
        try:
            with open(os.path.join(self.cfg["ingest_dir"], "state.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def rig_busy(self):
        """A take or live session is running (uploads wait, like ingest)."""
        with self.lock:
            return self.state != "idle" or any((st or {}).get("mode") in ("live", "live-starting")
                                               for st in self.status.values())

    def lamp_inputs(self):
        """What the recording light needs: (rig, hub-ingest state). Cheap; called often."""
        with self.lock:
            rig = {"state": self.state, "take_id": (self.take or {}).get("take_id"),
                   "nodes": dict(self.status)}
        return rig, lamp.read_ingest_state(os.path.join(self.cfg["ingest_dir"], "state.json"))

    def poll_forever(self, stop):
        while not stop.is_set():
            t = time.monotonic()
            self.poll_once()
            self.health = hub_health(self.cfg["state_dir"])
            with self.lock:
                statuses = dict(self.status)
            self.history.maybe_record(self.health, statuses)
            self.check_budget()
            self.calibrator.tick()
            stop.wait(max(0.1, self.cfg["poll_s"] - (time.monotonic() - t)))

    def snapshot(self):
        with self.lock:
            return {"hub_version": VERSION, "state": self.state, "take": self.take, "hub": self.health,
                    "message": self.message, "sync_server": self.cfg["sync_server"],
                    "nodes": {n: {"online": self.status[n] is not None, "status": self.status[n],
                                  "last_seen": self.seen[n], "shooting": self.is_shooting(n),
                                  "slow": self.status[n] is not None and self.misses[n] > 0,
                                  "problems": arm_problems(n, self.status[n], self.cfg["checks"],
                                                           still_is_idle=True)}
                              for n in self.cfg["nodes"]},
                    "phones": self.phones.snapshot(self.health.get("free_gb")),
                    "budget": self.budget, "camera_settings": self.cam_settings,
                    "calibration": self.calibrator.status(), "lamp": self.lamp.status(),
                    "scratch": self.scratch.status(), "names": self.names.all(),
                    "last_take": self.last_take()}

    # -- phones
    def phone_poll(self, body):
        with self.lock:
            rig = {"state": self.state, "take_id": (self.take or {}).get("take_id")}
        return self.phones.poll(body, rig)

    # -- take log
    def read_log(self):
        try:
            with open(self.log_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def append_log(self, entry):
        log = self.read_log() + [entry]
        tmp = self.log_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(log, f, indent=1)
        os.replace(tmp, self.log_path)

    def last_take(self):
        log = self.read_log()
        return log[-1] if log else None

    # -- helpers
    def _parallel(self, names, fn):
        """Run fn(name) on each node in parallel; return ({name: result}, {name: error})."""
        futs = {n: self.pool.submit(fn, n) for n in names}
        ok, err = {}, {}
        for n, f in futs.items():
            try:
                ok[n] = f.result()
            except Exception as e:  # noqa: BLE001
                err[n] = str(e)
        return ok, err

    def _wait_modes(self, names, want, timeout):
        end = self.clock() + timeout
        while True:
            self.poll_once()
            with self.lock:
                left = [n for n in names if (self.status[n] or {}).get("mode") != want]
            if not left or self.clock() >= end:
                return left
            self.sleep(0.3)

    def pick_server(self, names):
        return self.cfg["sync_server"] if self.cfg["sync_server"] in names else names[0]

    # -- record-time limit (budget.py)
    def compute_budget(self, names=None, phone_names=None):
        """How long the current (or next) take can run. Before REC: the online
        cameras and the ready phones; during a take: its cameras and its phones."""
        b = self.cfg["budget"]
        hub_free = self.health.get("free_gb")
        with self.lock:
            take = dict(self.take or {}) if self.state == "recording" else None
            status = dict(self.status)
        elapsed = max(0.0, self.clock() - take["recording_at"]) if take else 0.0
        if names is None:
            names = take["nodes"] if take else [n for n in self.cfg["nodes"] if status[n] is not None]
        if phone_names is None:
            phone_names = set(self.phones.ready())
            if take:
                phone_names |= set(self.phones.phones_in(take["take_id"]))
        start_free = (take or {}).get("node_free_gb", {})
        pis = {}
        for n in names:
            free = (status.get(n) or {}).get("free_gb")
            written = (start_free[n] - free) * 1e9 if free is not None and start_free.get(n) is not None else None
            rate = budget.measured_rate(written, elapsed, b["pi_mbps"] * budget.MBPS)
            pis[n] = (free * 1e9 if free is not None else None, rate)
        ph = {}
        if phone_names:
            up = self.phones.uploaded.get(take["take_id"]) if take else None
            total = budget.measured_rate(up, elapsed, len(phone_names) * b["phone_mbps"] * budget.MBPS)
            ph = {n: total / len(phone_names) for n in phone_names}
        r = budget.limits(hub_free * 1e9 if hub_free is not None else None, b["hub_reserve_gb"] * 1e9,
                          pis, ph, elapsed, b["node_reserve_gb"] * 1e9)
        r.update(cameras=len(pis), phones=len(ph),
                 warn=r["remaining_s"] is not None and r["remaining_s"] <= b["warn_s"])
        drive_free, waiting = self.drive_waiting((take or {}).get("take_id"))
        r["drive"] = budget.drive_fit(drive_free, waiting,
                                      sum(rt for _, rt in pis.values()) + sum(ph.values()),
                                      elapsed, b["drive_reserve_gb"] * 1e9)
        return r

    def drive_waiting(self, current_take=None):
        """(STUDIO drive free bytes or None, bytes of earlier takes not on it yet).
        Waiting footage is estimated from its length at the default rates; refreshed
        at most every 30 s (it reads the take log and the phone manifests)."""
        now = self.clock()
        c = getattr(self, "_drive_cache", None)
        if c and now - c[0] < 30 and c[1] == current_take:
            return c[2], c[3]
        st = self.ingest_state(limit=10 ** 6)
        free = st.get("drive_free_gb")
        b = self.cfg["budget"]
        waiting = 0.0
        takes = st.get("takes", {})
        # Takes ingest has registered (per-camera steps), and newer ones it hasn't
        # reached yet. An older unregistered take is gone from the cameras (deleted
        # before ingest existed) and will never reach the drive.
        known = {tid for tid, t in takes.items()
                 if any(isinstance(v, dict) and "done" in v for v in t.values())}
        newest = max(known, default="")
        for tid, t in takes.items():
            if tid == current_take or t.get("complete") or (tid not in known and tid < newest):
                continue
            for cam, info in ((t.get("recorded") or {}).get("cameras") or {}).items():
                if "written" in ((t.get(cam) or {}).get("done") or []):
                    continue
                secs = (info.get("frames") or 0) / 30.0 or (t["recorded"].get("length_s") or 0)
                waiting += secs * (b["phone_mbps"] if info.get("phone") else b["pi_mbps"]) * budget.MBPS
        self._drive_cache = (now, current_take, free * 1e9 if free is not None else None, waiting)
        return self._drive_cache[2], waiting

    def check_budget(self):
        """Every poll: refresh the estimate; stop the take when it runs out."""
        try:
            self.budget = r = self.compute_budget()
        except Exception as e:  # noqa: BLE001 - an estimate must never break polling
            log("record-time estimate failed: %s" % e)
            return
        with self.lock:
            due = (self.state == "recording" and self.cfg["budget"]["auto_stop"]
                   and r["remaining_s"] is not None and r["remaining_s"] <= 0)
            take_id = (self.take or {}).get("take_id")
        if due:
            log("take %s: storage limit reached (%s), stopping" % (take_id, r["limited_by"]))
            self.spawn(self._auto_stop, take_id, r["limited_by"])

    def _auto_stop(self, take_id, by):
        try:
            self.stop()
        except Refused:
            return          # already stopping or stopped
        with self.lock:
            self.message = ("Take %s stopped automatically: %s was out of recording space."
                            % (take_id, "the hub" if by == "hub" else by))

    # -- per-camera settings kept on the hub, sent with every arm and snapshot (Track B)
    SETTING_RANGES = {"lens_position": (0.0, 15.0), "gain": (1.0, calibrate.MAX_GAIN), "awb_gains": (0.3, 10.0)}

    def set_camera_settings(self, cam, body):
        if cam not in self.cfg["nodes"]:
            raise ValueError("unknown camera %s" % cam)
        new = {}
        for k, v in (body or {}).items():
            if k not in self.SETTING_RANGES:
                raise ValueError("unknown setting %s" % k)
            lo, hi = self.SETTING_RANGES[k]
            vals = v if k == "awb_gains" else [v]
            if k == "awb_gains" and (not isinstance(v, list) or len(v) != 2):
                raise ValueError("awb_gains must be [red, blue]")
            vals = [float(x) for x in vals]
            if not all(lo <= x <= hi for x in vals):
                raise ValueError("%s must be %g to %g" % (k, lo, hi))
            new[k] = [round(x, 3) for x in vals] if k == "awb_gains" else round(vals[0], 3)
        with self.lock:
            self.cam_settings[cam] = dict(self.cam_settings.get(cam, {}), **new)
            tmp = self.cam_settings_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.cam_settings, f, indent=1)
            os.replace(tmp, self.cam_settings_path)
            out = dict(self.cam_settings)
            live = (self.status.get(cam) or {}).get("mode") == "live"
        if live:            # a live stream takes the change at once (spike B-S1: no gap)
            try:
                self.push_live(cam)
            except NodeError as e:
                log("live settings for %s not applied: %s" % (cam, e))
        return out

    LIVE_KEYS = ("gain", "awb_gains", "lens_position")

    def push_live(self, cam, settings=None):
        """Send settings (default: the hub's for this camera) to its running live stream."""
        with self.lock:
            s = {k: v for k, v in (settings or self.cam_settings.get(cam, {})).items() if k in self.LIVE_KEYS}
        if s:
            self.nodes.post(cam, "/live/settings", {"settings": s}, timeout=10)
        return s

    # -- snapshot page (Track B, B1)
    def snap(self, cam, overrides=None):
        """(jpeg, info) from one camera. Idle: a still with the take settings (plus overrides,
        e.g. a focus being tried). Live: one frame from its stream. Never during a take."""
        if cam not in self.cfg["nodes"]:
            raise ValueError("unknown camera %s" % cam)
        with self.lock:
            st = self.status[cam]
        t0 = time.monotonic()
        if st and st.get("mode") in ("live", "live-starting"):
            jpg, source, settings = self.grab_live(cam), "live", {}
        else:
            jpg, meta = self.still(cam, overrides)
            source, settings = "still", meta.get("settings", {})
        info = {"camera": cam, "source": source, "ms": round((time.monotonic() - t0) * 1000), "settings": settings}
        try:
            info["sharpness"] = calib.sharpness(jpg)
        except Exception as e:  # noqa: BLE001 - the picture still helps without the number
            info["sharpness_error"] = str(e)
        return jpg, info

    def still(self, cam, settings=None, auto=False):
        """(jpeg, metadata) from an idle camera: the hub's settings for it plus `settings`,
        or auto-metered. One per camera at a time; REC waits for any in flight."""
        if cam not in self.cfg["nodes"]:
            raise ValueError("unknown camera %s" % cam)
        with self.lock:
            if self.state != "idle":
                raise Refused(["rig is %s: no snapshots during a take" % self.state])
            if self.hold:
                raise Refused(["rig is starting REC or LIVE"])
            if self.status[cam] is None:
                raise Refused(["%s is offline" % cam])
            self.snaps_in_flight += 1
            body = {"settings": dict(self.cam_settings.get(cam, {}), **(settings or {})), "auto": auto}
        try:
            with self.cam_locks[cam], self.taking_still(cam):
                return self.nodes.still(cam, body)
        finally:
            with self.lock:
                self.snaps_in_flight -= 1

    @contextlib.contextmanager
    def taking_still(self, cam):
        with self.lock:
            self.shooting[cam] += 1
        try:
            yield
        finally:
            with self.lock:
                self.shooting[cam] -= 1
                self.shot_at[cam] = self.clock()

    def is_shooting(self, cam):
        """Taking a still now, or just did (kept SHOT_LINGER_S so a 1 s page poll sees it)."""
        return self.shooting[cam] > 0 or self.clock() - self.shot_at[cam] < SHOT_LINGER_S

    def grab_live(self, cam):
        url = "rtsp://%s.local:8554/cam" % cam
        with self.taking_still(cam):
            r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-rtsp_transport", "tcp", "-i", url,
                                "-frames:v", "1", "-q:v", "3", "-f", "image2", "-c:v", "mjpeg", "-"],
                               capture_output=True, timeout=15)
        if r.returncode != 0 or not r.stdout:
            raise NodeError("%s: no frame from the live stream (%s)" % (cam, r.stderr.decode(errors="replace")[-200:]))
        return r.stdout

    def arm_body(self, cam, take_id, role):
        # Every take (and every restart) syncs on its own UDP port: a late multicast packet
        # from an earlier start can't be mistaken for this one. MMSS of the take id: unique
        # within the hour, and a restart always gets a new take id.
        b = {"take_id": take_id, "role": role, "sync_port": 20000 + int(take_id[-4:])}
        with self.lock:
            if self.cam_settings.get(cam):
                b["settings"] = dict(self.cam_settings[cam])
        return b

    def holding_cameras(self):
        """Context: stop new stills (calibration watching, snapshot page) and wait for the
        ones in flight, so REC/LIVE find every camera free."""
        hub = self

        class Hold:
            def __enter__(self):
                with hub.lock:
                    hub.hold += 1
                hub.wait_for_snapshots()

            def __exit__(self, *exc):
                with hub.lock:
                    hub.hold -= 1
        return Hold()

    def wait_for_snapshots(self, timeout=5.0):
        end = time.monotonic() + timeout
        while self.snaps_in_flight and time.monotonic() < end:
            time.sleep(0.05)

    # -- calibration card (Track B; calib.py)
    def measure_card(self, jpeg):
        """Read the card in one picture. Never during a take (settings must not change)."""
        with self.lock:
            if self.state != "idle":
                raise Refused(["rig is %s: the card is ignored during takes" % self.state])
        if not jpeg:
            raise ValueError("no picture (or over 8 MB)")
        if self.card is None:
            self.card = calib.load_card(self.cfg.get("card_json"))
        with self.calib_lock:           # one at a time: OpenCV on a full-HD frame uses ~50 MB
            return calib.measure(jpeg, self.card)

    # -- REC
    def rec(self, names=None):
        if not self.op_lock.acquire(blocking=False):
            raise Refused(["another operation is in progress"])
        try:
            with self.holding_cameras():
                return self._rec(names)
        except (Refused, NodeError) as e:
            if self.state == "idle":    # not a second press during a take
                self.lamp.alert("REC failed: %s" % e)
            raise
        finally:
            self.op_lock.release()

    def _rec(self, names, attempt=1, missing_before=None, restarts=None):
        """names=None (the REC button): every camera that is online; offline ones
        are skipped with a warning. An explicit list is strict: all must be ready.
        Either way, an online camera that isn't ready blocks the take.
        attempt > 1: a restart after a follower started out of sync (resync_limit_us)."""
        with self.lock:
            if self.state != ("starting" if attempt > 1 else "idle"):
                raise Refused(["rig is %s" % self.state])
        self.poll_once()
        missing = []
        with self.lock:
            if names is None:
                names = [n for n in self.cfg["nodes"] if self.status[n] is not None]
                missing = [n for n in self.cfg["nodes"] if self.status[n] is None]
                if not names:
                    raise Refused(["no cameras online"])
            problems = [p for n in names for p in arm_problems(n, self.status[n], self.cfg["checks"])]
            live = [n for n in names if self.status[n]["mode"] == "live"] if not problems else []
        if problems:
            raise Refused(problems)
        bud = self.compute_budget(names)
        if bud["total_s"] is not None and bud["total_s"] < self.cfg["budget"]["min_rec_s"]:
            raise Refused(["only %d s of recording space (%s)" % (bud["total_s"], bud["limited_by"])])
        if missing_before is not None:
            missing = missing_before
        take_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        restarts = list(restarts or [])
        if restarts and take_id <= restarts[-1]["take_id"]:     # same second as the aborted one
            self.sleep(1.0)
            take_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        server = self.pick_server(names)
        clients = [n for n in names if n != server]
        with self.lock:
            self.state = "starting"
            self.take = {"take_id": take_id, "nodes": names, "server": server,
                         "started_at": self.clock(), "missing": missing,
                         "limit_s": bud["total_s"],
                         "calibration": {n: self.calibrator.records.get(n) for n in names}}
            self.message = None
        self.scratch.start(take_id)             # before arming, so it covers the first frames
        try:
            if live:
                _, err = self._parallel(live, lambda n: self.nodes.post(n, "/live/stop", timeout=25))
                if err:
                    raise NodeError("live stop failed: %s" % err)
                left = self._wait_modes(live, "idle", 10)
                if left:
                    raise NodeError("still live: %s" % ", ".join(left))
            # Clients first (they wait for the server's sync broadcast), then the server.
            _, err = self._parallel(clients, lambda n: self.nodes.post(
                n, "/take/arm", self.arm_body(n, take_id, "client")))
            if err:
                raise NodeError("arm failed: %s" % err)
            self.sleep(self.cfg["client_head_start_s"])
            self.nodes.post(server, "/take/arm", self.arm_body(server, take_id, "server"))
            left = self._wait_modes(names, "recording", self.cfg["sync_timeout_s"])
            if left:
                raise NodeError("no sync within %ds: %s" % (self.cfg["sync_timeout_s"], ", ".join(left)))
        except Exception as e:  # noqa: BLE001
            # Abort: stop anything that did start, keep whatever they recorded.
            self._stop_all(names, aborted=str(e))
            raise
        with self.lock:
            diffs = {n: ((self.status[n] or {}).get("take") or {}).get("sync_difference_us") for n in names}
        warn = sync_warnings(diffs, self.cfg["max_sync_difference_us"])
        off = sync_warnings({n: d for n, d in diffs.items() if n != server}, self.cfg["resync_limit_us"])
        if off and attempt <= self.cfg["resync_retries"]:
            # Out of sync: throw this start away and start again (the light stays amber).
            log("take %s: %s; restarting (attempt %d)" % (take_id, "; ".join(off), attempt + 1))
            with self.lock:
                self.state = "starting"
            self._stop_all(names, aborted="restarted: out of sync (%s)" % "; ".join(off), keep_state=True)
            restarts.append({"take_id": take_id, "out_of_sync": off})
            self.sleep(self.cfg["resync_pause_s"])
            return self._rec(names, attempt + 1, missing, restarts)
        with self.lock:
            self.state = "recording"
            self.take["restarts"] = restarts
            self.take["recording_at"] = self.clock()
            # After arming: a camera may have freed space since the reading before REC, so this,
            # not the value before REC, is the baseline for its measured rate.
            self.take["node_free_gb"] = {n: (self.status[n] or {}).get("free_gb") for n in names}
            self.take["sync_warnings"] = warn
            notes = []
            if missing:
                notes.append("Recording %s only: %s offline." % (", ".join(names), ", ".join(missing)))
            if off:
                notes.append("Out of sync after %d tries: %s. Recording anyway; STOP and REC again, "
                             "and check the cameras' Wi-Fi." % (attempt, "; ".join(off)))
            elif warn:
                notes.append("Sync warning: %s. Consider STOP and REC again." % "; ".join(warn))
            if restarts and not off:
                notes.append("Restarted %d× to get every camera in sync." % len(restarts))
            self.message = " ".join(notes) or None
            result = dict(self.take)
        if off:
            self.lamp.alert("out of sync: %s" % "; ".join(off))
        return result

    # -- STOP
    def stop(self):
        if not self.op_lock.acquire(timeout=60):
            raise Refused(["another operation is in progress"])
        try:
            with self.lock:
                if self.state != "recording":
                    raise Refused(["not recording (rig is %s)" % self.state])
                names = self.take["nodes"]
            return self._stop_all(names)
        finally:
            self.op_lock.release()

    def _stop_all(self, names, aborted=None, keep_state=False):
        with self.lock:
            self.state = "starting" if keep_state else "stopping"
            take = dict(self.take or {})
        # Only nodes that are mid-take; a node that never armed has nothing to stop.
        self.poll_once()
        with self.lock:
            active = [n for n in names if (self.status[n] or {}).get("mode") in ("armed", "recording")]
        manifests, errors = self._parallel(active, lambda n: self.nodes.post(n, "/take/stop", timeout=40))
        summary = take_summary(take.get("take_id"), manifests, errors, self.cfg["max_sync_difference_us"])
        cal = dict(take.get("calibration") or {})
        for ph in self.phones.phones_in(take.get("take_id")):
            cal["phone:" + ph] = self.calibrator.records.get("phone:" + ph)
        summary.update(calibration=cal)
        summary.update(phones=self.phones.phones_in(take.get("take_id")),
                       server=take.get("server"), missing=take.get("missing", []),
                       started_at=take.get("started_at"),
                       stopped_at=self.clock(), aborted=aborted)
        sc = self.scratch.stop()
        summary["scratch"] = ({k: sc.get(k) for k in ("device", "rate", "channels", "samples", "problems")}
                              if sc else None)
        summary["restarts"] = take.get("restarts") or []
        self.append_log(summary)
        with self.lock:
            if keep_state:              # a restart: REC goes on, the rig isn't idle
                self.take = None
                return summary
            self.state, self.take = "idle", None
            self.message = ("take aborted: %s" % aborted) if aborted else None
        return summary

    # -- ingest (separate service; files only)
    def ingest_state(self, limit=50):
        """hub-ingest's state, merged with the take log so that takes still only on
        the cameras are listed too (with recorded=..., and no ingest steps yet)."""
        st = self.read_ingest_file() or {"status": "ingest service not running", "takes": {}}
        takes = st.setdefault("takes", {})
        removed = st.get("removed_takes") or {}     # deleted from the drive by the user: not listed
        index = st.pop("drive_index", None) or {}   # what's on the drive (stick-db-plan S2)
        # Takes that came in on the drive have their take-log record in take.json; the hub's
        # own log comes after, so it wins for takes recorded here.
        from_drive = [t["recording"] for t in (index.get("takes") or {}).values() if t.get("recording")]
        for e in from_drive + self.read_log():      # later entries win (adopted duplicates)
            tid = e.get("take_id")
            if not tid or e.get("aborted") or tid in removed:
                continue
            cams = e.get("cameras") or {}
            if not cams and "recorded" in takes.get(tid, {}):
                continue                            # an empty duplicate never hides real data
            wc = [c.get("first_frame_wallclock_ns") for c in cams.values() if c.get("first_frame_wallclock_ns")]
            problems = list(e.get("sync_warnings") or [])
            problems += ["%s: %s" % (n, err) for n, err in sorted((e.get("errors") or {}).items())]
            problems += ["%s exited with code %s" % (n, c.get("exit_code")) for n, c in sorted(cams.items())
                         if not exit_ok(c)]
            # Dropped frames are shown with each camera's frame count, not repeated here.
            takes.setdefault(tid, {})["recorded"] = {
                "problems": problems,
                "started_at": min(wc) / 1e9 if wc else e.get("started_at"),
                "length_s": max([c.get("duration_s") or 0 for c in cams.values()] or [0]),
                "cameras": {n: {"frames": c.get("frames"), "dropped": c.get("dropped_frames") or 0}
                            for n, c in sorted(cams.items())},
            }
            for part, m in self.phones.manifests(tid).items():
                done = bool(m and m.get("complete"))
                takes[tid]["recorded"]["cameras"][part] = {"label": (m or {}).get("label"),
                    "frames": m.get("frames") if done else None, "dropped": (m.get("dropped_frames") or 0) if done else 0,
                    "phone": True, "uploading": not done}
                if done:
                    takes[tid]["recorded"]["length_s"] = max(takes[tid]["recorded"]["length_s"], m.get("duration_s") or 0)
        st["drive_view"] = drive_view(takes, index)
        st["takes"] = dict(sorted(takes.items())[-limit:])
        copied = st.get("audio") or {}
        jobs = st.get("renders") or {}
        syncs = st.get("syncs") or {}
        videos = st.get("videos") or {}
        for tid, t in st["takes"].items():
            a = self.audio.summary(tid, set(copied.get(tid) or {}),
                                   {k.split("/", 1)[1]: j for k, j in jobs.items() if k.startswith(tid + "/")},
                                   {k.split("/", 1)[1]: j for k, j in syncs.items() if k.startswith(tid + "/")},
                                   videos.get(tid) or {})
            for song in a.get("songs") or []:
                self.add_youtube(tid, song, jobs.get("%s/%s:edit" % (tid, song["id"])) or {})
            if a:
                t["audio"] = a
        return st

    def add_youtube(self, take, song, job):
        """The YouTube upload with the song's video render (a fourth step after Write to drive)
        and with every finished video (its link in YouTube Studio)."""
        if song.get("render") is not None:
            song["render"].update(youtube=job.get("youtube"), upload_error=job.get("upload_error"),
                                  upload=self.youtube_upload(job.get("upload")))
        song["videos"] = [dict(v, youtube=self.youtube_upload("%s_%s_video%s" % (take, song["id"], v.get("number"))))
                          for v in song.get("videos") or []]

    # -- playback from the STUDIO drive (read-only mount, managed by hub-ingest)
    def touch_lease(self):
        """Ask hub-ingest to keep the drive mounted read-only (see Ingest.viewing)."""
        p = os.path.join(self.cfg["ingest_dir"], "playback.lease")
        with open(p, "a"):
            pass
        os.utime(p)

    def drive_mounted(self):
        try:
            with open("/proc/mounts") as f:
                return any(line.split()[1] == self.cfg["drive_mount"] for line in f)
        except OSError:
            return False

    def take_folder(self, take_id):
        """The take's folder on the STUDIO drive (mounted or not)."""
        return os.path.join(self.cfg["drive_mount"], "takes",
                            "%s-%s-%s_%s" % (take_id[:4], take_id[4:6], take_id[6:8], take_id))

    def wait_for_drive(self, wait_s=8.0):
        """Ask hub-ingest to mount the drive read-only and wait for it. False if it doesn't come."""
        self.touch_lease()
        deadline = time.monotonic() + wait_s
        while not self.drive_mounted():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.25)
        return True

    def read_take_document(self, take_id):
        """The take's take.json (written by ingest) from the drive."""
        if not self.wait_for_drive():
            raise editor_projects.ProjectError("the STUDIO drive isn't plugged in")
        try:
            with open(os.path.join(self.take_folder(take_id), "take.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            raise editor_projects.ProjectError("take %s isn't on the drive yet (ingest)" % take_id) from None

    def read_drive_edit(self, take_id, song_id):
        """The working cut in the take's folder on the drive (copied there by hub-ingest), or None."""
        if not self.drive_mounted():
            return None                              # read_take_document has just waited for it
        path = os.path.join(self.take_folder(take_id), "edits", song_id, "working-edit.json")
        try:
            with open(path) as f:
                edit = json.load(f)
        except (OSError, ValueError):
            return None
        return edit if isinstance(edit, dict) else None

    def find_quick_edit(self, take_id, song_id, version):
        """The newest quick-mix edit (render.py) of this song version on the drive, or None."""
        folder = os.path.join(self.take_folder(take_id), "edits", song_id)
        prefix = "%s-v%d-quick" % (song_id, version)
        try:
            names = [n for n in os.listdir(folder) if n.startswith(prefix) and n.endswith(".json")]
        except OSError:
            return None
        if not names:
            return None
        newest = max(names, key=lambda n: os.path.getmtime(os.path.join(folder, n)))
        try:
            with open(os.path.join(folder, newest)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def media_file(self, take_id, rel, wait_s=8.0):
        """(path, None) or (None, http_code). rel: "cam1.mov" or "edits/<song>/<name>.mp4"
        (already checked by the route's pattern). Waits for hub-ingest to mount the drive."""
        self.touch_lease()
        if not rel.endswith((".mov", ".mp4")):
            rel += ".mov"
        p = os.path.join(self.cfg["drive_mount"], "takes",
                         "%s-%s-%s_%s" % (take_id[:4], take_id[4:6], take_id[6:8], take_id), rel)
        deadline = time.monotonic() + wait_s
        while not self.drive_mounted():
            if time.monotonic() > deadline:
                return None, 503
            time.sleep(0.25)
        return (p, None) if os.path.isfile(p) else (None, 404)

    def ingest_command(self, cmd, **fields):
        d = os.path.join(self.cfg["ingest_dir"], "commands")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "%d-%s.json" % (time.time_ns(), cmd))
        with open(p + ".tmp", "w") as f:
            json.dump(dict(fields, cmd=cmd), f)
        os.replace(p + ".tmp", p)
        return dict(fields, queued=cmd)

    def film_overlays(self):
        """The film overlays added with hub/tools/add_overlay.py, for the editor's Effects."""
        folder = os.path.join(self.cfg["state_dir"], "overlays")
        out = []
        for overlay_id in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
            try:
                with open(os.path.join(folder, overlay_id, "overlay.json")) as f:
                    info = json.load(f)
            except (OSError, ValueError):
                continue
            info["preview_url"] = "/media-overlay/%s/preview.mp4" % overlay_id
            out.append(info)
        return {"overlays": out}

    def youtube_status(self):
        """Is the hub signed in to YouTube (mac/youtube_login.py, copied by the deploy)?"""
        try:
            with open(self.cfg["youtube_credentials_path"]) as f:
                signed_in = bool(json.load(f).get("refresh_token"))
        except (OSError, ValueError):
            signed_in = False
        return {"signed_in": signed_in}

    def youtube_upload(self, key):
        """hub-upload's record of one upload, as the editor shows it, or None."""
        if not key or "/" in key:
            return None
        try:
            with open(os.path.join(self.cfg["youtube_spool_dir"], key + ".json")) as f:
                record = json.load(f)
        except (OSError, ValueError):
            return None
        return {k: record.get(k) for k in ("status", "progress", "url", "studio_url", "note", "error", "video_number")}

    def request_edit_render(self, take, song, version, youtube=False):
        """The editor's edit as the finished video (hub-ingest's edit_render.py), when idle.
        youtube: then upload it (private) to the channel, when the hub is signed in."""
        path = self.editor.working_copy_path(take, song, version)
        if not os.path.exists(path):
            older = self.editor._older_per_version_copy(take, song)
            if not older:
                raise ValueError("nothing to render yet: make a cut in the editor first")
            shutil.copyfile(older, path)                 # edits saved per bounce version before 2026-11-17
        if not self.audio.version_info(take, song, version).get("synced"):
            raise ValueError("this song isn't synced to the take yet")
        youtube = youtube is True and self.youtube_status()["signed_in"]
        try:
            song_name = self.editor._song_and_version(take, song, version)[0].get("name") or song
        except (ValueError, OSError, KeyError):
            song_name = song
        return self.ingest_command("render", take=take, song=song, version=version, kind="edit",
                                   youtube=youtube, song_name=song_name, take_name=self.names.take(take))

    def edit_render_status(self, take, song, version):
        """The newest render job of this song's edit, from hub-ingest's state (or {})."""
        try:
            with open(os.path.join(self.cfg["ingest_dir"], "state.json")) as f:
                jobs = json.load(f).get("renders") or {}
        except (OSError, ValueError):
            return {}
        job = jobs.get("%s/%s:edit" % (take, song)) or {}
        status = {k: job.get(k) for k in ("status", "progress", "eta_s", "error", "file", "status_note", "length_s",
                                          "render_s", "version", "video_number", "youtube", "upload_error")}
        status["upload"] = self.youtube_upload(job.get("upload"))
        return status

    def sync_job(self, take, song, version):
        """hub-ingest's automatic sync job for this bounce version, or None."""
        return ((self.read_ingest_file() or {}).get("syncs") or {}).get("%s/%s/v%d" % (take, song, version))

    def request_sync(self, take, song, version):
        """hub-ingest syncs the bounce to the take from the hub mic (audiosync.py)."""
        v = self.audio.version_info(take, song, int(version))
        if not v.get("complete"):
            raise ValueError("that version isn't finished uploading")
        return self.ingest_command("sync", take=take, song=song, version=int(version))

    def request_render(self, take, song, version):
        """Quick mix (Phase 4 A4): hub-ingest renders it when the rig is idle."""
        v = self.audio.version_info(take, song, int(version))
        if not v.get("complete"):
            raise ValueError("that version isn't finished uploading")
        if not v.get("synced"):
            raise ValueError("this song isn't synced to the take yet")
        return self.ingest_command("render", take=take, song=song, version=int(version))

    # -- LIVE
    def live(self, on, names=None):
        if not self.op_lock.acquire(blocking=False):
            raise Refused(["another operation is in progress"])
        try:
            with self.lock:
                if self.state != "idle":
                    raise Refused(["rig is %s" % self.state])
            names = names or list(self.cfg["nodes"])
            path = "/live/start" if on else "/live/stop"
            with self.holding_cameras():
                ok, err = self._parallel(names, lambda n: self.nodes.post(n, path, timeout=25))
            if on:
                # MediaMTX starts from mediamtx.yml: give each stream the hub's settings
                # (calibration, focus), which it otherwise forgets on every restart.
                _, err2 = self._parallel(sorted(ok), self.push_live)
                err.update({n: "settings: %s" % e for n, e in err2.items()})
            self.poll_once()
            return {"ok": sorted(ok), "errors": err}
        finally:
            self.op_lock.release()


# ---------------------------------------------------------------- HTTP

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json",
                ".css": "text/css", ".svg": "image/svg+xml", ".ico": "image/x-icon",
                ".woff2": "font/woff2", ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8",   # editor fonts and their licences
                ".png": "image/png"}                                           # the brand frame


class Handler(BaseHTTPRequestHandler):
    hub = None
    server_version = "hubd/" + VERSION

    def log_message(self, fmt, *args):
        if not self.path.startswith(self.QUIET):
            sys.stderr.write("%s %s\n" % (self.command, fmt % args))

    def _send(self, code, body, ctype="application/json", headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    # A camera's .mov, or a rendered video: /media/<take>/edits/<song>/<name>.mp4
    MEDIA = re.compile(r"^/media/(\d{8}-\d{6})/((?:[a-z0-9](?:[a-z0-9-]{0,40}[a-z0-9])?\.mov)"
                       r"|(?:edits/[a-z0-9-]{1,41}/[a-z0-9-]{1,60}\.mp4))$")
    P_CHUNK = re.compile(r"^/api/phone/chunk/([^/]+)/([^/]+)/(\d{1,6})\.(h264|json)$")
    P_HAVE = re.compile(r"^/api/phone/have/([^/]+)/([^/]+)$")
    P_FINISH = re.compile(r"^/api/phone/finish/([^/]+)/([^/]+)$")
    P_LOG = re.compile(r"^/api/phone/log/([^/]+)$")
    P_SNAP = re.compile(r"^/api/snap/(cam\d+)\.jpg$")
    P_PHONE_SNAP = re.compile(r"^/api/snap/phone/([^/]+)\.jpg$")
    P_PHONE_SNAP_PUT = re.compile(r"^/api/phone/snap/([^/]+)$")
    P_CAM_SETTINGS = re.compile(r"^/api/camera/(cam\d+)/settings$")
    P_PHONE_CALIB = re.compile(r"^/api/calib/phone/([^/]+)$")
    P_PHONE_MOUNT = re.compile(r"^/api/phone/([^/]+)/mount$")
    P_AUDIO = re.compile(r"^/api/audio/(\d{8}-\d{6})$")
    P_EDITOR = re.compile(r"^/api/editor/(\d{8}-\d{6})/([a-z0-9-]{1,41})/v(\d{1,4})$")
    P_EDITOR_RENDER = re.compile(r"^/api/editor/(\d{8}-\d{6})/([a-z0-9-]{1,41})/v(\d{1,4})/render$")
    P_EDITOR_SYNC = re.compile(r"^/api/editor/(\d{8}-\d{6})/([a-z0-9-]{1,41})/v(\d{1,4})/sync$")
    P_EDITOR_WAVEFORM = re.compile(r"^/api/editor/(\d{8}-\d{6})/([a-z0-9-]{1,41})/v(\d{1,4})/waveform$")
    # Strip thumbnails (hub-ingest's thumbnails.py): index.json and sheet-NNN.jpg per camera.
    # Film overlays (hub/tools/add_overlay.py): /media-overlay/<id>/clip.mp4 or preview.mp4
    MEDIA_OVERLAY = re.compile(r"^/media-overlay/([a-z0-9][a-z0-9-]{0,40})/(clip|preview)\.mp4$")
    MEDIA_PROXY = re.compile(r"^/media-proxy/(\d{8}-\d{6})/([a-z0-9][a-z0-9-]{0,40})\.mp4$")
    MEDIA_THUMBS = re.compile(r"^/media-thumbs/(\d{8}-\d{6})/([a-z0-9][a-z0-9-]{0,40})/(index\.json|sheet-\d{3}\.jpg)$")
    P_AUDIO_SONGS = re.compile(r"^/api/audio/(\d{8}-\d{6})/songs$")
    P_AUDIO_VERSIONS = re.compile(r"^/api/audio/(\d{8}-\d{6})/([a-z0-9-]+)/versions$")
    P_AUDIO_PIECE = re.compile(r"^/api/audio/(\d{8}-\d{6})/([a-z0-9-]+)/v(\d+)\?offset=(\d+)$")
    P_AUDIO_FINISH = re.compile(r"^/api/audio/(\d{8}-\d{6})/([a-z0-9-]+)/v(\d+)/finish$")
    P_AUDIO_CHOOSE = re.compile(r"^/api/audio/(\d{8}-\d{6})/([a-z0-9-]+)/choose$")
    QUIET = ("/api/status", "/api/time", "/api/phone/poll", "/api/phone/chunk", "/api/phone/log", "/api/calib/measure",
             "/api/snap/", "/api/phone/snap/")

    def do_HEAD(self):
        if self.MEDIA.match(self.path.split("?")[0]):
            return self._media(head=True)
        if self.MEDIA_AUDIO.match(self.path.split("?")[0]):
            return self._media_audio(head=True)
        if self.MEDIA_PROXY.match(self.path.split("?")[0]):
            return self._media_proxy(head=True)
        if self.MEDIA_OVERLAY.match(self.path.split("?")[0]):
            return self._media_overlay(head=True)
        self.send_response(405)
        self.end_headers()

    # A song version's bounce, from the hub's own audio folder (no drive needed).
    MEDIA_AUDIO = re.compile(r"^/media-audio/(\d{8}-\d{6})/([a-z0-9-]{1,41})/v(\d{1,4})$")
    AUDIO_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4",
                   ".aif": "audio/aiff", ".aiff": "audio/aiff", ".ogg": "audio/ogg"}

    def _media_overlay(self, head=False):
        overlay_id, kind = self.MEDIA_OVERLAY.match(self.path.split("?")[0]).groups()
        path = os.path.join(self.hub.cfg["state_dir"], "overlays", overlay_id, kind + ".mp4")
        if not os.path.isfile(path):
            return self._send(404, {"error": "no such overlay"})
        return self._serve_file(path, "video/mp4", head)

    def _media_proxy(self, head=False):
        """An editing proxy (hub-ingest's proxies.py), from the hub's SD card."""
        take_id, camera_id = self.MEDIA_PROXY.match(self.path.split("?")[0]).groups()
        path = os.path.join(self.hub.cfg["state_dir"], "proxies", take_id, camera_id + ".mp4")
        if not os.path.isfile(path):
            return self._send(404, {"error": "no editing proxy yet"})
        return self._serve_file(path, "video/mp4", head)

    def _media_audio(self, head=False):
        take_id, song_id, version = self.MEDIA_AUDIO.match(self.path.split("?")[0]).groups()
        try:
            info = self.hub.audio.version_info(take_id, song_id, int(version))
        except ValueError as e:
            return self._send(404, {"error": str(e)})
        if not info.get("complete"):
            return self._send(404, {"error": "that version isn't finished uploading"})
        path = os.path.join(self.hub.audio.take_dir(take_id), song_id, "v%s" % version, info["filename"])
        content_type = self.AUDIO_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        return self._serve_file(path, content_type, head)

    def _media(self, head=False):
        """Serve a take's .mov from the drive, with Range support (seeking)."""
        take_id, rel = self.MEDIA.match(self.path.split("?")[0]).groups()
        path, err = self.hub.media_file(take_id, rel)
        if err:
            msg = {503: "STUDIO drive not available", 404: "not on the drive"}[err]
            return self._send(err, {"error": msg})
        # H.264 in .mov; browsers play it as mp4. Long reads keep the drive mounted.
        return self._serve_file(path, "video/mp4", head, keep_drive_alive=True)

    def _serve_file(self, path, content_type, head=False, keep_drive_alive=False):
        """Send a file with HTTP Range support (seeking in players and WebCodecs readers)."""
        size = os.path.getsize(path)
        start, end, code = 0, size - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if head:
            return
        touched = time.monotonic()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    b = f.read(min(1 << 18, left))
                    if not b:
                        break
                    self.wfile.write(b)
                    left -= len(b)
                    if keep_drive_alive and time.monotonic() - touched > 30:   # long reads keep the mount alive
                        self.hub.touch_lease()
                        touched = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass          # viewer seeked/closed, or the drive was ejected mid-read

    def do_GET(self):
        h = self.hub
        if self.MEDIA.match(self.path.split("?")[0]):
            return self._media()
        if self.MEDIA_AUDIO.match(self.path.split("?")[0]):
            return self._media_audio()
        if self.MEDIA_PROXY.match(self.path.split("?")[0]):
            return self._media_proxy()
        if self.MEDIA_OVERLAY.match(self.path.split("?")[0]):
            return self._media_overlay()
        if self.path.split("?")[0] == "/api/youtube":
            return self._send(200, h.youtube_status())
        if self.path.split("?")[0] == "/api/overlays":
            return self._send(200, h.film_overlays())
        m = self.MEDIA_THUMBS.match(self.path.split("?")[0])
        if m:
            path = os.path.join(h.cfg["state_dir"], "thumbs", *m.groups())
            if not os.path.isfile(path):
                return self._send(404, {"error": "no thumbnails yet"})
            with open(path, "rb") as f:
                return self._send(200, f.read(), "application/json" if path.endswith(".json") else "image/jpeg")
        m = self.P_EDITOR_RENDER.match(self.path.split("?")[0])
        if m:
            return self._send(200, h.edit_render_status(m.group(1), m.group(2), int(m.group(3))))
        m = self.P_EDITOR_WAVEFORM.match(self.path.split("?")[0])
        if m:
            try:
                return self._send(200, h.editor.waveform(m.group(1), m.group(2), int(m.group(3))))
            except (ValueError, subprocess.CalledProcessError) as e:
                return self._send(400, {"error": str(e)})
        m = self.P_EDITOR.match(self.path.split("?")[0])
        if m:
            try:
                mode = "sync" if "mode=sync" in self.path else "edit"         # Sync mode: place the bounce by hand
                return self._send(200, h.editor.project(m.group(1), m.group(2), int(m.group(3)), mode))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        for app in self.STATIC_APPS:              # the hub page, the editor and what they share
            if self.path.split("?")[0] in ("/" + app, "/" + app + "/") or self.path.startswith("/" + app + "/"):
                return self._app_static(app)
        if self.path.split("?")[0] == "/api/status":
            if "watch=1" in self.path:
                h.calibrator.page_open()      # a page is open: watch for the card
            return self._send(200, h.snapshot())
        if self.path == "/api/history":                 # sparklines: the last hour, one point per 10 s
            return self._send(200, h.history.as_dict())
        if self.path == "/api/takes":
            return self._send(200, h.read_log())
        if self.path == "/api/ingest":
            return self._send(200, h.ingest_state())
        if self.path == "/api/lamp":
            return self._send(200, h.lamp.status())
        m = self.P_AUDIO.match(self.path)
        if m:
            try:
                return self._send(200, h.audio.list(m.group(1)))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if self.path == "/api/time":
            return self._send(200, {"hub_ns": time.time_ns()})
        m = self.P_HAVE.match(self.path)
        if m:
            try:
                return self._send(200, h.phones.have(*m.groups()))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        path, _, query = self.path.partition("?")
        m = self.P_SNAP.match(path)
        if m:
            try:
                q = urllib.parse.parse_qs(query)
                over = {"lens_position": float(q["lens"][0])} if "lens" in q else None
                jpg, info = h.snap(m.group(1), over)
                return self._send(200, jpg, "image/jpeg", {"X-Snap-Info": json.dumps(info)})
            except Refused as e:
                return self._send(409, {"error": str(e)})
            except NodeError as e:
                return self._send(502, {"error": str(e)})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        m = self.P_PHONE_SNAP.match(path)
        if m:
            got = h.phones.get_snap(m.group(1))
            if not got:
                return self._send(404, {"error": "no picture from this phone yet"})
            return self._send(200, got[0], "image/jpeg", {"X-Snap-Info": json.dumps({"age_s": round(time.time() - got[1], 1)})})
        if path in ("/", "/index.html", "/snap"): # the hub page (static/hub, Preact like the editor); /snap opens its focus view
            return self._app_static("hub", "index.html")
        name = {"/phone": "phone.html"}.get(path, path.lstrip("/"))
        if "/" in name or name.startswith("."):
            return self._send(404, {"error": "not found"})
        p = os.path.join(h.cfg["static_dir"], name)
        ext = os.path.splitext(name)[1]
        if ext in STATIC_TYPES and os.path.isfile(p):
            with open(p, "rb") as f:
                return self._send(200, f.read(), STATIC_TYPES[ext])
        self._send(404, {"error": "not found"})

    # Folders under static/ served as apps at /<name>/: their pages, modules, styles and fonts.
    STATIC_APPS = ("hub", "editor", "shared")

    def _app_static(self, app, relative=None):
        """A file of an app folder (static/<app>/<path>), nothing outside it."""
        if relative is None:
            relative = self.path.split("?")[0][len("/" + app):].lstrip("/") or "index.html"
        root = os.path.realpath(os.path.join(self.hub.cfg["static_dir"], app))
        path = os.path.realpath(os.path.join(root, relative))
        content_type = STATIC_TYPES.get(os.path.splitext(path)[1])
        if not path.startswith(root + os.sep) or not content_type or not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        with open(path, "rb") as f:
            return self._send(200, f.read(), content_type)

    def do_PUT(self):
        m = self.P_EDITOR_SYNC.match(self.path)
        if m:                                      # Sync mode's Save: {"bounce_start_ns"} (by hand)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                return self._send(200, self.hub.editor.save_sync(m.group(1), m.group(2), int(m.group(3)),
                                                                 body.get("bounce_start_ns")))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        m = self.P_EDITOR.match(self.path)
        if m:
            try:
                edit = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                return self._send(200, self.hub.editor.save_edit(m.group(1), m.group(2), int(m.group(3)), edit))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        m = self.P_AUDIO_PIECE.match(self.path)
        if m:
            take, song, v, off = m.groups()
            try:
                n = int(self.headers.get("Content-Length") or 0)
                return self._send(200, self.hub.audio.put(take, song, int(v), int(off), self.rfile.read, n))
            except audio.Busy as e:
                self.close_connection = True        # the body wasn't read
                return self._send(409, {"error": str(e), "busy": True})
            except ValueError as e:
                self.close_connection = True
                return self._send(400, {"error": str(e)})
            except OSError as e:
                return self._send(507, {"error": "hub storage: %s" % e})
        m = self.P_CHUNK.match(self.path)
        if not m:
            return self._send(404, {"error": "not found"})
        take, part, seq, ext = m.groups()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return self._send(200, self.hub.phones.put_chunk(take, part, int(seq), ext, self.rfile.read, n))
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except OSError as e:
            return self._send(507, {"error": "hub storage: %s" % e})

    def do_POST(self):
        h = self.hub
        try:
            m = self.P_PHONE_SNAP_PUT.match(self.path)
            if m:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 4 << 20:
                    raise ValueError("picture too large")
                return self._send(200, h.phones.put_snap(m.group(1), self.rfile.read(n)))
            if self.path == "/api/calib/measure":
                return self._send(200, h.measure_card(self.rfile.read(int(self.headers.get("Content-Length") or 0))
                                                      if int(self.headers.get("Content-Length") or 0) <= 8 << 20 else b""))
            b = self._body()
            if self.path == "/api/phone/poll":
                return self._send(200, h.phone_poll(b))
            m = self.P_FINISH.match(self.path)
            if m:
                return self._send(200, h.phones.finish(m.group(1), m.group(2), b))
            m = self.P_LOG.match(self.path)
            if m:
                h.phones.log(m.group(1), b)
                return self._send(200, {"ok": True})
            if self.path == "/api/snap/phones":
                h.phones.want_snaps()
                return self._send(200, {"ok": True})
            m = self.P_CAM_SETTINGS.match(self.path)
            if m:
                return self._send(200, h.set_camera_settings(m.group(1), b))
            m = self.P_PHONE_CALIB.match(self.path)
            if m:
                return self._send(200, h.calibrator.phone_report(m.group(1), b))
            m = self.P_PHONE_MOUNT.match(self.path)
            if m:
                return self._send(200, h.phones.set_mount(m.group(1), b.get("mount")))
            if self.path == "/api/render":
                return self._send(200, h.request_render(b.get("take"), b.get("song"), b.get("version")))
            m = self.P_AUDIO_SONGS.match(self.path)
            if m:
                return self._send(200, h.audio.new_song(m.group(1), b.get("name")))
            m = self.P_AUDIO_VERSIONS.match(self.path)
            if m:
                return self._send(200, h.audio.new_version(m.group(1), m.group(2), b.get("filename"), b.get("bytes")))
            m = self.P_AUDIO_FINISH.match(self.path)
            if m:
                v = h.audio.finish(m.group(1), m.group(2), int(m.group(3)))
                if v.get("complete"):
                    h.request_sync(m.group(1), m.group(2), int(m.group(3)))    # automatic sync (Track A)
                return self._send(200, v)
            m = self.P_EDITOR_RENDER.match(self.path)
            if m:                                  # "Make video" in the editor: the finished video
                return self._send(200, h.request_edit_render(m.group(1), m.group(2), int(m.group(3)),
                                                             youtube=b.get("youtube") is True))
            if self.path == "/api/sync":           # {"take", "song", "version"}: sync again
                return self._send(200, h.request_sync(b.get("take"), b.get("song"), int(b.get("version") or 0)))
            m = self.P_AUDIO_CHOOSE.match(self.path)
            if m:
                v = b.get("version")
                return self._send(200, h.audio.choose(m.group(1), m.group(2), int(v) if v is not None else None))
            if self.path == "/api/names":          # {"kind": "cameras" | "takes", "id", "name"}
                return self._send(200, h.names.set(b.get("kind"), b.get("id"), b.get("name")))
            if self.path == "/api/rec":
                return self._send(200, h.rec(b.get("nodes")))
            if self.path == "/api/stop":
                return self._send(200, h.stop())
            if self.path == "/api/live":
                return self._send(200, h.live(bool(b.get("on")), b.get("nodes")))
            if self.path == "/api/ingest/start":
                return self._send(200, h.ingest_command("start"))
            if self.path == "/api/drive/eject":
                return self._send(200, h.ingest_command("eject"))
            if self.path == "/api/lamp/preview":       # {"look": "recording", "seconds": 5}
                return self._send(200, h.lamp.preview(b.get("look"), b.get("seconds", 5)))
        except Refused as e:
            return self._send(409, {"error": str(e), "reasons": e.reasons})
        except NodeError as e:
            return self._send(502, {"error": str(e)})
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "not found"})


class DualStackServer(ThreadingHTTPServer):
    """Listens on IPv6 and IPv4 at once.

    mDNS advertises the hub's IPv6 link-local address too, and some clients
    (Android Chrome) use it without falling back to IPv4.
    """

    address_family = socket.AF_INET6
    request_queue_size = 64  # default 5 overflows when a phone opens many sockets
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def make_server(bind, port):
    if bind in ("", "::"):
        return DualStackServer(("::", port), Handler)
    srv = ThreadingHTTPServer((bind, port), Handler)
    srv.daemon_threads = True
    return srv


def main():
    cfg = load_config(os.environ.get("HUBD_CONFIG", "/etc/hubd/config.json"))
    hub = Hub(cfg)
    Handler.hub = hub
    stop = threading.Event()
    threading.Thread(target=hub.poll_forever, args=(stop,), daemon=True).start()
    threading.Thread(target=hub.lamp.run, args=(stop,), daemon=True).start()
    srv = make_server(cfg["bind"], cfg["port"])

    def shutdown(*_):
        stop.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    sys.stderr.write("hubd %s on :%d, nodes %s\n" % (VERSION, cfg["port"], ", ".join(cfg["nodes"])))
    srv.serve_forever()


if __name__ == "__main__":
    main()
