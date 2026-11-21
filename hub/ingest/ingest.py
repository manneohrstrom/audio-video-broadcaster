#!/usr/bin/env python3
"""hub-ingest: pull takes from the cameras, conform them to exact 30 fps .mov with
time-of-day timecode, verify, and write them to the STUDIO drive (Phase 3).

Runs as its own service so nothing it does can disturb a take. Standard library only;
external tools: rsync (restricted read-only key), mkvmerge, ffmpeg/ffprobe, mount.

Rules (CLAUDE.md, docs/phase3-plan.md):
- Works only while the rig is idle; REC or LIVE pauses it at once
  (rsync is killed and resumes from partial files; ffmpeg is SIGSTOPped).
- Sync first: frames are placed by their real timestamps onto an exact 30.000 fps
  grid anchored to time of day (Path B); gaps and the 30.011 fps rate become repeats.
  With video_timing "real" (editor plan M-E1) it rewraps with -c copy instead: every
  frame keeps its real time, a gap becomes one long frame, and the render conforms.
- Writes only to a drive labelled STUDIO, to *.part names, then fsync + rename.
- Never deletes from the drive. Marks each camera's take verified on that camera as
  soon as its own .mov is verified on the drive (not held back by other cameras); the
  camera deletes it later, and only when it needs the space. take.json is still written
  last; the last camera written is marked only after it, so a failed take.json leaves
  the take pending and it is retried.

Talks to hubd via files in state_dir: state.json (read by hubd/UI) and commands/*.json.
"""
import contextlib
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render  # noqa: E402
import thumbnails  # noqa: E402
import proxies  # noqa: E402
import edit_render  # noqa: E402
import title_render  # noqa: E402
import youtube_upload  # noqa: E402
import drive_records  # noqa: E402
import motion  # noqa: E402

VERSION = "0.30.1"
FPS = 30
TIMESCALE = 90000            # mov track and movie timescale for rewrapped (real-timing) files
TIME_TOLERANCE_MS = 1.0      # rewrap verify: each frame's time against the manifest (editor plan E1)

# ETA model: seconds of work per second of video, per step. Starting values from the
# rig (2026-11-15: conform about 1x real time); learned as takes go through.
DEFAULT_RATES = {"pulled": 1.0, "checked": 0.01, "conformed": 1.0, "verified": 0.25,
                 "written": 0.05, "marked": 0.0}
STEP_OVERHEAD_S = 2.0
# Convert and verify give the same answer every time on the same files: after this many
# failures in a row a camera-take is given up on (kept, shown as needing attention, not
# tried again) instead of being converted and verified every round (user, 2026-11-17: a
# take whose end was lost in a camera reboot failed verify over and over).
GIVE_UP_AFTER = 3
GIVE_UP_STEPS = ("conformed", "verified")


RSYNC_TEMP_RE = re.compile(r"^\.(.+)\.[A-Za-z0-9]{6}$")    # ".video.h264.zEmXtj" -> "video.h264"


def pulled_bytes(folder):
    """How much of a camera's files rsync has in `folder`, for the pull's progress.

    rsync writes each file to a hidden temporary (".video.h264.zEmXtj") and renames it at
    the end. On a resumed pull (a pull stopped when the rig got busy: --partial kept what
    had arrived as video.h264) rsync builds a whole new temporary from that partial copy
    plus the rest, so the folder holds both. Counting both reached 100 % at about a third
    of the way through a 20 min take (2026-11-17). So where a temporary exists, it counts
    instead of the file it will replace."""
    finished, temporary = {}, {}
    for entry in os.scandir(folder):
        if not entry.is_file() or entry.name == "ts.mkv":
            continue
        match = RSYNC_TEMP_RE.match(entry.name)
        if match:
            temporary[match.group(1)] = entry.stat().st_size
        else:
            finished[entry.name] = entry.stat().st_size
    return sum(temporary.get(name, finished.get(name, 0)) for name in set(finished) | set(temporary))


def ffmpeg_progress(path, duration_s):
    """Fraction done from an ffmpeg -progress file (last out_time_us), or None."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("ascii", "replace")
    except OSError:
        return None
    vals = re.findall(r"out_time_us=(\d+)", tail)
    if not vals or not duration_s:
        return None
    return max(0.0, min(1.0, int(vals[-1]) / 1e6 / duration_s))


DEFAULT_CONFIG = {
    "nodes": ["cam1", "cam2", "cam3", "cam4"],
    "node_port": 8080,
    "hub_api": "http://127.0.0.1",
    "ssh_key": "/home/studio/.ssh/id_ingest",
    "state_dir": "/var/lib/hubd/ingest",
    "drive_label": "STUDIO",
    "mount_point": "/mnt/studio",
    "idle_minutes": 30,
    "poll_s": 2.0,
    "playback_idle_s": 300,     # read-only mount kept this long after the last playback request
    "x264_preset": "veryfast",
    "x264_crf": "16",
    # "conform": re-encode onto an exact 30.000 fps grid (Phase 3, as built).
    # "real": -c copy, every frame at its real capture time (docs/editor-plan.md, M-E1);
    # the render conforms the final cut instead. Default since spike E1 passed (2026-11-16).
    "video_timing": "real",
    # Phone cameras (docs/phone-plan.md): hubd stages their takes here, already in the
    # camera layout, so a phone's "pull" is a hard link and its "mark" deletes the staging copy.
    "phones_dir": "/var/lib/hubd/phones",
    # Bounce uploads and hub mic recordings staged by hubd (hub/hubd/audio.py, scratch.py),
    # per take, in the layout they get under STUDIO/takes/<take folder>/audio/.
    "audio_dir": "/var/lib/hubd/audio",
    "quick_shot_s": 10.0,       # quick mix (render.py): a new camera this often
    # Automatic bounce sync (audiosync.py): settings passed through, e.g. {"mic_latency_s": 0.074}.
    "sync": {},
    # Strip thumbnails for the editor's timeline (thumbnails.py), on the hub's SD card.
    "thumbs_dir": "/var/lib/hubd/thumbs",
    # Editing proxies for the editor's video window (proxies.py), on the hub's SD card.
    "proxies_dir": "/var/lib/hubd/proxies",
    # hubd's own state folder: the editor's waveforms and edits of a removed take go too.
    "hubd_state_dir": "/var/lib/hubd",
    "removed_check_s": 600,     # how often to look at the drive: takes deleted, added (index)
    # The editor's working cut goes to the drive this long after its last change (stick-db-plan S4).
    "working_edit_delay_s": 120,
    # The editor's looks (filters, effects, adjustments), shared with the editor's preview.
    "looks_path": "/opt/hubd/static/editor/looks.json",
    # The border and logo the editor's "Border & logo" switch puts on top (hub/tools/make_brand_frame.py).
    "brand_dir": "/opt/hubd/static/editor/brand",
    # Outfit, for the video's title sequence (title_render.py); the editor's preview uses the same file.
    "fonts_dir": "/opt/hubd/static/shared/vendor/fonts",
    # Finished videos the editor sends to YouTube wait here for hub-upload (youtube_upload.py).
    "youtube_spool_dir": "/var/lib/hubd/youtube/uploads",
}
TAKE_RE = re.compile(r"^\d{8}-\d{6}$")
HERE = os.path.dirname(os.path.abspath(__file__))


def failure(error):
    """A failed thumbnail or proxy: tried again after the next hub-ingest update, not before."""
    return {"error": str(error)[-300:], "version": VERSION}


def failed_in_this_version(failed, key):
    entry = failed.get(key)
    return isinstance(entry, dict) and entry.get("version") == VERSION


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    return cfg


# ------------------------------------------------------------------ timing maths

def grid_origin(wc_ns, tz_localtime=time.localtime):
    """(timecode, origin_ns, residual_ns): the 30 fps time-of-day grid instant at or
    before the first frame. Every camera of a take lands on the same grid."""
    sec = wc_ns // 1_000_000_000
    frac = wc_ns - sec * 1_000_000_000
    ff = frac * FPS // 1_000_000_000
    origin = sec * 1_000_000_000 + ff * 1_000_000_000 // FPS
    lt = tz_localtime(sec)
    return "%02d:%02d:%02d:%02d" % (lt.tm_hour, lt.tm_min, lt.tm_sec, ff), origin, wc_ns - origin


def expected_frames(residual_ns, last_pts_ms, period_ms=1000 / 30.0107):
    """Frames the conform should produce: grid slots from origin to the end of the last frame."""
    end_s = residual_ns / 1e9 + last_pts_ms / 1000.0 + period_ms / 1000.0
    return int(round(end_s * FPS))


def rough_sections(gaps, first_wc_ns=None, window_s=2.0, min_missing=3):
    """Stretches of a clip to avoid in an edit (docs/phone-plan.md P4): any window of
    window_s seconds missing at least min_missing frames; overlapping windows merge.
    gaps: the manifest's [{"at_s", "gap_ms", "missing"}]. Times are seconds from the
    clip's first frame, plus hub wall clock (ns) when first_wc_ns is known."""
    ev = sorted((g["at_s"], g["at_s"] + g["gap_ms"] / 1000.0, g["missing"]) for g in gaps or [] if g.get("missing"))
    spans = []
    for i, (t0, _, _) in enumerate(ev):
        group = [e for e in ev[i:] if e[0] <= t0 + window_s]
        if sum(e[2] for e in group) >= min_missing:
            spans.append([t0, max(e[1] for e in group)])
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    for a, b in merged:
        sec = {"start_s": round(a, 3), "end_s": round(b, 3),
               "missing": sum(e[2] for e in ev if a <= e[0] <= b)}
        if first_wc_ns:
            sec["start_wallclock_ns"] = first_wc_ns + int(a * 1e9)
            sec["end_wallclock_ns"] = first_wc_ns + int(b * 1e9)
        out.append(sec)
    return out


def read_pts(path):
    with open(path) as f:
        return [float(line) for line in f if line.strip() and not line.startswith("#")]


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


# ------------------------------------------------------------------ process control

class Paused(Exception):
    """The rig became busy; the current step will be retried later."""


class Runner:
    """Runs external commands; pauses/kills them when the rig gets busy. Fakeable."""

    def __init__(self, busy, poll=0.5, on_pause=None):
        self.busy, self.poll = busy, poll
        self.on_pause = on_pause or (lambda paused: None)   # status for the UI

    def run(self, cmd, pause="kill", nice=19, timeout=None, on_poll=None):
        """pause='kill' (rsync: resumable) or 'stop' (ffmpeg: SIGSTOP/SIGCONT).
        on_poll() is called about every poll interval while it runs (progress)."""
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             preexec_fn=lambda: os.nice(nice))
        stopped = False
        start = time.monotonic()
        while True:
            try:
                out, err = p.communicate(timeout=self.poll)
                if p.returncode:
                    raise RuntimeError("%s failed (%s): %s" % (cmd[0], p.returncode, err.strip()[-500:]))
                return out
            except subprocess.TimeoutExpired:
                pass
            if on_poll:
                try:
                    on_poll()
                except Exception:  # noqa: BLE001 - progress is cosmetic
                    pass
            if timeout and time.monotonic() - start > timeout:
                p.kill()
                p.wait()
                raise RuntimeError("%s timed out" % cmd[0])
            b = self.busy()
            if b and pause == "kill":
                p.kill()
                p.wait()
                raise Paused()
            if b and not stopped:
                p.send_signal(signal.SIGSTOP)
                stopped = True
                self.on_pause(True)
            elif not b and stopped:
                p.send_signal(signal.SIGCONT)
                stopped = False
                self.on_pause(False)


# ------------------------------------------------------------------ drive

class Drive:
    """The STUDIO drive: found by label, checked on plug-in. Mounted read-write only while
    writing, read-only while someone plays a file back in the hub UI, otherwise unmounted.
    A read-only mount never marks exFAT dirty, so pulling the drive then is harmless."""

    def __init__(self, cfg, sh=None):
        self.label, self.mp = cfg["drive_label"], cfg["mount_point"]
        self.sh = sh or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True))
        self.dev_path = "/dev/disk/by-label/" + self.label
        self.checked_dev = None
        self.dirty = False

    def present(self):
        return os.path.exists(self.dev_path)

    def filesystem_id(self):
        """The drive's filesystem UUID (from /dev/disk/by-uuid), or None. Tells this drive
        apart from another one with the same label (e.g. the coming 1 TB drive)."""
        device = self.device()
        try:
            for name in os.listdir("/dev/disk/by-uuid"):
                if os.path.realpath(os.path.join("/dev/disk/by-uuid", name)) == device:
                    return name
        except OSError:
            pass
        return None

    def device(self):
        return os.path.realpath(self.dev_path)

    def mode(self):
        """'rw', 'ro' or None (not mounted)."""
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if parts[1] == self.mp:
                        return "ro" if "ro" in parts[3].split(",") else "rw"
        except OSError:
            pass
        return None

    def mounted(self):
        return self.mode() is not None

    def plug_id(self):
        """Changes on every plug-in: the device node is recreated, with a new ctime."""
        dev = self.device()
        try:
            return dev, os.stat(dev).st_ctime_ns
        except OSError:
            return dev, None

    def check(self):
        """fsck.exfat -n once per plug-in (read-only check). Returns True if clean."""
        pid = self.plug_id()
        if self.checked_dev == pid:
            return not self.dirty
        r = self.sh(["sudo", "-n", "fsck.exfat", "-n", pid[0]])
        self.checked_dev, self.dirty = pid, r.returncode != 0
        return not self.dirty

    def mount(self, rw=True):
        want, have = ("rw" if rw else "ro"), self.mode()
        if have == want:
            return
        if have:
            if have == "rw":
                subprocess.run(["sync"])
            r = self.sh(["sudo", "-n", "mount", "-o", "remount," + want, self.mp])
        else:
            r = self.sh(["sudo", "-n", "mount", "-t", "exfat", "-o",
                         "%s,uid=%d,gid=%d,umask=022,noatime" % (want, os.getuid(), os.getgid()),
                         self.device(), self.mp])
        if r.returncode:
            raise RuntimeError("mount (%s) failed: %s" % (want, r.stderr.strip()))

    def unmount(self):
        if self.mounted():
            subprocess.run(["sync"])
            r = self.sh(["sudo", "-n", "umount", self.mp])
            if r.returncode:
                raise RuntimeError("umount failed: %s" % r.stderr.strip())

    def eject(self):
        self.unmount()
        if self.present():
            dev = re.sub(r"\d+$", "", self.device())          # /dev/sda1 -> /dev/sda
            self.sh(["sudo", "-n", "udisksctl", "power-off", "-b", dev])
        self.checked_dev = None

    def free_gb(self):
        """Free space on the drive, or None when it isn't mounted (the bare mount
        point is on the hub's SD card, so its free space would be misleading)."""
        if not self.mounted():
            return None
        try:
            return round(shutil.disk_usage(self.mp).free / 1e9, 1)
        except OSError:
            return None


# ------------------------------------------------------------------ ingest

class Ingest:
    STEPS = ("pulled", "checked", "conformed", "verified", "written", "marked")

    def __init__(self, cfg, runner=None, drive=None, http=None, clock=time.time):
        self.cfg, self.clock = cfg, clock
        self.sd = cfg["state_dir"]
        self.lock = threading.RLock()
        self.work = os.path.join(self.sd, "work")
        self.cmd_dir = os.path.join(self.sd, "commands")
        for d in (self.sd, self.work, self.cmd_dir):
            os.makedirs(d, exist_ok=True)
        self.http = http or HttpJson()
        self.runner = runner or Runner(
            self.rig_busy, on_pause=lambda paused: self.save(status="paused: rig busy" if paused else "ingesting"))
        self.drive = drive or Drive(cfg)
        self.drive_lock = threading.RLock()   # mount changes: ingest writes vs. the keeper thread
        self.writing_n = 0
        self.rendering = False                # a render reads the drive: keep it mounted read-only
        self.lease = os.path.join(self.sd, "playback.lease")   # touched by hubd per media request
        self.state = self._load()
        self.last_removed_check = 0.0
        # Nothing is running yet: drop step markers left by a previous run (restart, crash).
        for cams in self.state.get("takes", {}).values():
            for cs in cams.values():
                if isinstance(cs, dict):
                    cs.update(step=None, progress=None, step_started=None)
        self.state["current"] = None
        self.state["activity"] = None
        for job in (self.state.get("renders") or {}).values():      # interrupted by a restart
            if job.get("status") in ("rendering", "checking", "writing", "paused"):
                job.update(status="queued", progress=None)
        self.last_busy = self.state.get("last_busy", 0)
        self.manual = False
        self.last_free = self.state.get("drive_free_gb")

    def drive_free(self):
        """The drive's free space. It is mounted only while writing or playing back, so
        keep the last reading while the same drive stays plugged in; forget it on
        eject or unplug."""
        if not self.drive.present() or self.state.get("drive") == "ejected":
            self.last_free = None
        else:
            f = self.drive.free_gb()
            if f is not None:
                self.last_free = f
        return self.last_free

    # -- persistent state (also what the UI shows)
    def _load(self):
        try:
            with open(os.path.join(self.sd, "state.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"takes": {}}

    def save(self, **status):
        with self.lock:            # pull threads save concurrently
            self.state.update(status, version=VERSION, updated=self.clock(), last_busy=self.last_busy)
            self.state["eta_s"] = self.eta()
            self._saved_at = time.monotonic()
            tmp = os.path.join(self.sd, "state.json.tmp")
            with open(tmp, "w") as f:
                json.dump(self.state, f, indent=1)
            os.replace(tmp, os.path.join(self.sd, "state.json"))

    def cam_state(self, take_id, cam):
        with self.lock:
            return self.state["takes"].setdefault(take_id, {}).setdefault(cam, {"done": []})

    def record_failure(self, cs, step, error):
        """A camera-take's step failed. Convert and verify fail the same way every time on
        the same files: GIVE_UP_AFTER of those in a row and it's given up on."""
        failures = cs.setdefault("failures", {})
        if step in GIVE_UP_STEPS:
            failures[step] = failures.get(step, 0) + 1
        message = str(error)
        if failures.get(step, 0) >= GIVE_UP_AFTER:
            cs["gave_up"] = True
            message = "gave up after %d tries: %s" % (failures[step], message)
        cs.update(error=message, step=None, progress=None)

    def mark(self, take_id, cam, step, **info):
        cs = self.cam_state(take_id, cam)
        (cs.get("failures") or {}).pop(step, None)        # it worked: start counting again
        if step not in cs["done"]:
            cs["done"].append(step)
        self._learn(cs, step)
        cs.update(info, error=None, step=None, progress=None, step_started=None)
        self.save()

    # -- progress and ETA (for the UI)
    def doing(self, kind=None, **fields):
        """What hub-ingest is working on besides the camera files themselves, shown on the
        archive's take cards (user, 2026-11-21: every background job visible): kind is
        proxy, thumbnails, motion, beats or audio, with take, camera, song, index and count
        as they apply. None: nothing (camera ingest, renders and syncs have their own records).
        Each change also brings the cameras' "prep" up to date (the item before is done)."""
        with self.lock:
            self.state["activity"] = (dict(fields, kind=kind, phase=fields.get("phase"), started=self.clock(),
                                           progress=None, eta_s=None) if kind else None)
        self.update_prep()
        self.save()

    def done_doing(self):
        if self.state.get("activity"):
            self.doing(None)

    def doing_progress(self, fraction, phase=None):
        """The current activity's progress (0-1) and time left; saved at most every 2 s."""
        with self.lock:                # called from the encoder's progress thread too
            act = self.state.get("activity")
            if not act:
                return
            if phase:
                act.update(phase=phase, progress=None, eta_s=None)
            else:
                act["progress"] = round(fraction, 3)
                elapsed = self.clock() - act["started"]
                act["eta_s"] = round(elapsed * (1 - fraction) / fraction) if fraction > 0.02 and elapsed > 5 else None
        if phase or time.monotonic() - getattr(self, "_saved_at", 0) >= 2:
            self.save()

    def begin(self, take_id, cam, step):
        cs = self.cam_state(take_id, cam)
        cs.update(step=step, progress=None, step_started=self.clock(), error=None)
        self.save(current=take_id, step="%s %s" % (cam, step))

    def progress(self, take_id, cam, frac):
        """Fraction of the current step done. Saved at most every 2 s."""
        if frac is None:
            return
        cs = self.cam_state(take_id, cam)
        cs["progress"] = round(frac, 3)
        if time.monotonic() - getattr(self, "_saved_at", 0) >= 2:
            self.save()

    def rates(self):
        r = dict(DEFAULT_RATES)
        r.update(self.state.get("rates") or {})
        return r

    def _learn(self, cs, step):
        """Update the step's seconds-per-video-second from this run, unless the rig was
        busy meanwhile (paused time would spoil it) or the take is too short to tell."""
        t0, dur = cs.get("step_started"), cs.get("duration_s") or 0
        if cs.get("step") != step or not t0 or dur < 20 or self.last_busy > t0:
            return
        k = max(0.0, self.clock() - t0 - STEP_OVERHEAD_S) / dur
        rates = self.state.setdefault("rates", {})
        old = rates.get(step, DEFAULT_RATES[step])
        rates[step] = round(0.6 * old + 0.4 * k, 4)

    def cam_eta(self, cs):
        """Seconds left for one camera-take, or None if it has stopped on an error."""
        if cs.get("error") and not cs.get("step"):
            return None
        rates, dur, done = self.rates(), cs.get("duration_s") or 0, cs.get("done", [])
        left = 0.0
        for step in self.STEPS:
            if step in done:
                continue
            est = rates[step] * dur + STEP_OVERHEAD_S
            if step == cs.get("step") and cs.get("step_started"):
                el = max(0.0, self.clock() - cs["step_started"])
                p = cs.get("progress")
                est = el * (1 - p) / p if p and p > 0.03 and el > 5 else max(est - el, 1.0)
            left += est
        return left

    def eta(self):
        total, any_ = 0.0, False
        for t, cams in self.state.get("takes", {}).items():
            for c, cs in cams.items():
                if not isinstance(cs, dict) or "marked" in cs.get("done", []):
                    continue
                e = self.cam_eta(cs)
                cs["eta_s"] = None if e is None else round(e)
                if e is not None:
                    total, any_ = total + e, True
        return round(total) if any_ else None

    def register(self, todo):
        """Put every pending camera-take in the state, so the UI can list the queue."""
        for take_id, cams in todo.items():
            for cam, m in cams.items():
                cs = self.cam_state(take_id, cam)
                cs["duration_s"] = m.get("duration_s") or (m.get("frames") or 0) / 30.0
                cs["frames_cam"] = m.get("frames")
                cs["dropped"] = m.get("dropped_frames")
                if m.get("label"):
                    cs["label"] = m["label"]
                cs["rough"] = rough_sections(m.get("gaps"), m.get("first_frame_wallclock_ns"))
                files = m.get("files") or {}
                cs["src_bytes"] = sum(files.get(n, 0) for n in ("video.h264", "video.pts")) or None

    # -- rig
    def rig_status(self):
        try:
            return self.http.get(self.cfg["hub_api"] + "/api/status")
        except Exception:  # noqa: BLE001 - hubd down: be safe, treat as busy
            return None

    def rig_busy(self):
        st = self.rig_status()
        busy = st is None or st.get("state") != "idle" or any(
            # "snapshot": a still for focus help or the calibration watcher (every 15 s while a hub
            # page is open). It doesn't touch the files ingest pulls, so it mustn't pause ingest.
            (n.get("status") or {}).get("mode") not in (None, "idle", "snapshot") for n in st.get("nodes", {}).values())
        if busy:
            self.last_busy = self.clock()
        return busy

    def should_run(self):
        if self.rig_busy():
            return False, "rig busy"
        if not self.drive.present():
            return False, "no %s drive" % self.cfg["drive_label"]
        if not self.drive.check():
            return False, "drive needs repair (fsck)"
        idle_for = self.clock() - self.last_busy
        if not self.manual and idle_for < self.cfg["idle_minutes"] * 60:
            return False, "waiting for %d min idle" % self.cfg["idle_minutes"]
        return True, "ingesting"

    # -- drive mounting: rw only while writing, ro while someone plays back, else unmounted
    def viewing(self):
        try:
            return self.clock() - os.path.getmtime(self.lease) < self.cfg["playback_idle_s"]
        except OSError:
            return False

    def settle_drive(self):
        """Outside a write: read-only if a playback lease is fresh, otherwise unmounted."""
        with self.drive_lock:
            if self.writing_n:
                return
            if ((self.viewing() or self.rendering) and self.state.get("drive") != "ejected"
                    and self.drive.present() and self.drive.check()):
                self.drive.mount(rw=False)
            else:
                self.drive.unmount()

    @contextlib.contextmanager
    def writing(self):
        with self.drive_lock:
            # Re-check here, not only at the start of a round: the drive may have been
            # pulled and plugged back in (possibly damaged) while a long conform ran.
            if not self.drive.present():
                raise RuntimeError("no %s drive" % self.cfg["drive_label"])
            if not self.drive.check():
                raise RuntimeError("drive needs repair (fsck): not writing to it")
            self.writing_n += 1
            try:
                self.drive.mount(rw=True)
            except Exception:
                self.writing_n -= 1
                raise
        try:
            yield
        finally:
            with self.drive_lock:
                self.writing_n -= 1
                try:
                    self.settle_drive()
                except RuntimeError as e:   # e.g. busy; the keeper retries
                    sys.stderr.write("settle drive: %s\n" % e)

    def keep_drive(self, stop, every=1.0):
        """Thread: follows playback leases even while a long conform runs."""
        last = None
        while not stop.is_set():
            try:
                self.settle_drive()
                err = None
            except Exception as e:  # noqa: BLE001
                err = str(e)
            if err != last and err:
                sys.stderr.write("drive keeper: %s\n" % err)
            last = err
            stop.wait(every)

    # -- commands from hubd
    def take_commands(self):
        out = []
        for name in sorted(os.listdir(self.cmd_dir)):
            if name.endswith(".tmp"):
                continue                      # hubd is still writing it
            p = os.path.join(self.cmd_dir, name)
            try:
                with open(p) as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                pass
            os.remove(p)
        return out

    def eject_requested(self):
        """An Eject is waiting (checked during a render, which then gives way)."""
        try:
            return any(n.endswith("-eject.json") for n in os.listdir(self.cmd_dir))
        except OSError:
            return False

    def handle_commands(self):
        for doc in self.take_commands():
            c = doc.get("cmd")
            if c == "render":
                self.queue_render(doc)
            elif c == "sync":
                self.queue_sync(doc)
            elif c == "delete_take":
                self.queue_delete(doc)
            elif c == "thumbs":              # the editor opened a take without thumbnails: don't wait 30 min
                self.manual = True
                take = (self.state.get("takes") or {}).get(doc.get("take") or "")
                if take is not None and take.get("adopted"):
                    take["caches_wanted"] = True     # a take that came in on the drive: make its caches now
            elif c == "start":
                self.manual = True
            elif c == "eject":
                with self.drive_lock:
                    try:
                        os.remove(self.lease)
                    except OSError:
                        pass
                    self.drive.eject()
                self.manual = False
                self.save(drive="ejected")

    # -- discovery
    def node_url(self, cam, path):
        return "http://%s.local:%d%s" % (cam, self.cfg["node_port"], path)

    def pending(self):
        """{take_id: {cam: manifest}} for takes on cameras that are not verified yet."""
        out = {}
        for cam in self.cfg["nodes"]:
            try:
                takes = self.http.get(self.node_url(cam, "/takes"))
            except Exception:  # noqa: BLE001 - camera offline: skip it this round
                continue
            for m in takes:
                if m.get("verified"):
                    continue
                if self.deleted_on_hub_page(m["take_id"]):
                    self.delete_on_camera(m["take_id"], cam)     # left there: it was offline at the delete
                    continue
                cs = self.state["takes"].get(m["take_id"], {}).get(cam, {})
                if "marked" in cs.get("done", []) or cs.get("gave_up"):
                    continue
                out.setdefault(m["take_id"], {})[cam] = m
        for take_id, part, m in self.phone_takes():
            if "marked" not in self.state["takes"].get(take_id, {}).get(part, {}).get("done", []):
                out.setdefault(take_id, {})[part] = m
        return dict(sorted(out.items()))

    def phone_takes(self):
        """(take_id, part, manifest) for every complete phone recording staged by hubd.
        Incomplete ones (pieces still on the phone) wait until the phone sends the rest."""
        root = self.cfg.get("phones_dir")
        if not root or not os.path.isdir(root):
            return []
        out = []
        for take_id in sorted(os.listdir(root)):
            if not TAKE_RE.match(take_id):
                continue
            for part in sorted(os.listdir(os.path.join(root, take_id))):
                try:
                    with open(os.path.join(root, take_id, part, "manifest.json")) as f:
                        m = json.load(f)
                except (OSError, ValueError):
                    continue
                if m.get("source") == "phone" and m.get("complete") and m.get("frames"):
                    out.append((take_id, part, m))
        return out

    def phone_src(self, take_id, cam):
        """The staging folder if this camera-take is a phone's, else None."""
        root = self.cfg.get("phones_dir")
        d = os.path.join(root, take_id, cam) if root else None
        return d if d and os.path.isfile(os.path.join(d, "manifest.json")) and not cam.startswith("cam") else None

    # -- steps
    def cam_dir(self, take_id, cam):
        d = os.path.join(self.work, take_id, cam)
        os.makedirs(d, exist_ok=True)
        return d

    def pull(self, take_id, cam):
        src = self.phone_src(take_id, cam)
        if src:
            return self.pull_phone(take_id, cam, src)
        ssh = "ssh -i %s -o BatchMode=yes -o ConnectTimeout=10" % self.cfg["ssh_key"]
        # rrsync on the camera roots paths at /var/lib/camnode/takes.
        d = self.cam_dir(take_id, cam)
        want = self.cam_state(take_id, cam).get("src_bytes")

        def poll():
            if want:
                self.progress(take_id, cam, min(1.0, pulled_bytes(d) / want))
        self.runner.run(["rsync", "-a", "--partial", "--timeout=60", "-e", ssh,
                         "--include=video.h264", "--include=video.pts", "--include=manifest.json",
                         "--exclude=*", "%s.local:%s/" % (cam, take_id), d + "/"],
                        pause="kill", on_poll=poll)
        self.mark(take_id, cam, "pulled")

    def pull_phone(self, take_id, cam, src):
        """Already on the hub: hard-link into the work folder (same filesystem, no copy)."""
        d = self.cam_dir(take_id, cam)
        for n in ("video.h264", "video.pts", "manifest.json"):
            dst = os.path.join(d, n)
            if os.path.exists(dst):
                os.remove(dst)
            try:
                os.link(os.path.join(src, n), dst)
            except OSError:
                shutil.copy2(os.path.join(src, n), dst)
        self.mark(take_id, cam, "pulled")

    def check_source(self, take_id, cam, man):
        if man.get("source") == "phone":
            # No transfer to check: the hub joined these files itself. Check they agree.
            n = len(read_pts(os.path.join(self.cam_dir(take_id, cam), "video.pts")))
            if n != man.get("frames"):
                raise RuntimeError("phone take has %d frame times, manifest says %s" % (n, man.get("frames")))
            return self.mark(take_id, cam, "checked")
        want = (man.get("sha256") or {}).get("video.h264")
        if not want:
            raise RuntimeError("camera has not finished hashing yet")
        got = sha256_file(os.path.join(self.cam_dir(take_id, cam), "video.h264"))
        if got != want:
            # Corrupt or partial copy: drop it so the next pull starts clean.
            os.remove(os.path.join(self.cam_dir(take_id, cam), "video.h264"))
            self.cam_state(take_id, cam)["done"] = []
            raise RuntimeError("sha256 mismatch after pull; will re-pull")
        self.mark(take_id, cam, "checked")

    def out_name(self, take_id, cam):
        return os.path.join(self.work, take_id, "%s.mov" % cam)

    def conform(self, take_id, cam, man):
        if self.cfg.get("video_timing") == "real":
            return self.rewrap(take_id, cam, man)
        d = self.cam_dir(take_id, cam)
        tc, origin, residual = grid_origin(man["first_frame_wallclock_ns"])
        mkv = os.path.join(d, "ts.mkv")
        self.runner.run(["mkvmerge", "-q", "-o", mkv, "--timestamps", "0:" + os.path.join(d, "video.pts"),
                         os.path.join(d, "video.h264")], pause="stop")
        out = self.out_name(take_id, cam)
        # The residual (first frame's offset from its grid slot) goes on the input with
        # -itsoffset, not as setpts in the filter: on phone footage (uneven frame spacing,
        # nominal rate guessed at 30.49) setpts left ~5% of frames with duplicate times and
        # ffmpeg dropped them (2026-11-15, Pixel 9a: 584 of 612). Pi output is unchanged
        # (bit-identical on take 20261115-231111). mkvmerge's timestamps start at 0.
        vf = "fps=fps=%d:start_time=0:round=near" % FPS
        prog = os.path.join(d, "progress.txt")
        dur = man.get("duration_s") or 0
        self.runner.run(["ffmpeg", "-nostdin", "-v", "error", "-progress", prog, "-y",
                         "-itsoffset", "%.6f" % (residual / 1e9), "-i", mkv, "-map", "0:v", "-vf", vf,
                         "-c:v", "libx264", "-preset", self.cfg["x264_preset"], "-crf", self.cfg["x264_crf"],
                         "-pix_fmt", "yuv420p", "-r", str(FPS), "-video_track_timescale", "30000",
                         "-timecode", tc, "-metadata", "title=%s %s" % (take_id, man.get("label") or cam),
                         "-metadata", "comment=studio-multicam %s; conformed to 30.000 fps" % VERSION,
                         "-movflags", "+faststart", "-f", "mov", out + ".tmp"], pause="stop",
                        on_poll=lambda: self.progress(take_id, cam, ffmpeg_progress(prog, dur)))
        os.replace(out + ".tmp", out)
        os.remove(mkv)
        self.mark(take_id, cam, "conformed", timecode=tc, timing="conform")

    def rewrap(self, take_id, cam, man):
        """-c copy into .mov with every frame at its real time: video.pts (µs precision)
        via mkvmerge at a 1 µs timestamp scale, then the first frame's offset from its
        30 fps timecode slot (residual) as -itsoffset. The mov keeps it as an edit list,
        which is in the movie timescale, so that is 90 kHz too (the default 1000 rounds
        to whole ms). Spike E1 (2026-11-16): every frame within 6 µs, gaps included."""
        d = self.cam_dir(take_id, cam)
        tc, origin, residual = grid_origin(man["first_frame_wallclock_ns"])
        mkv = os.path.join(d, "ts.mkv")
        self.runner.run(["mkvmerge", "-q", "-o", mkv, "--timestamp-scale", "1000",
                         "--timestamps", "0:" + os.path.join(d, "video.pts"),
                         os.path.join(d, "video.h264")], pause="stop")
        out = self.out_name(take_id, cam)
        self.runner.run(["ffmpeg", "-nostdin", "-v", "error", "-y",
                         "-itsoffset", "%.6f" % (residual / 1e9), "-i", mkv, "-map", "0:v", "-c", "copy",
                         "-video_track_timescale", str(TIMESCALE), "-movie_timescale", str(TIMESCALE),
                         "-timecode", tc, "-metadata", "title=%s %s" % (take_id, man.get("label") or cam),
                         "-metadata", "comment=studio-multicam %s; real frame times (copy)" % VERSION,
                         "-movflags", "+faststart", "-f", "mov", out + ".tmp"], pause="stop")
        os.replace(out + ".tmp", out)
        os.remove(mkv)
        self.mark(take_id, cam, "conformed", timecode=tc, timing="real")

    def verify(self, take_id, cam, man):
        if self.cam_state(take_id, cam).get("timing") == "real":
            return self.verify_real(take_id, cam, man)
        d = self.cam_dir(take_id, cam)
        out = self.out_name(take_id, cam)
        tc, origin, residual = grid_origin(man["first_frame_wallclock_ns"])
        pts = read_pts(os.path.join(d, "video.pts"))
        # A phone's frame period is its own (measured), not the Pi sensor's.
        fps_in = man.get("fps_measured") if man.get("source") == "phone" else None
        want = expected_frames(residual, pts[-1], 1000 / fps_in) if fps_in else expected_frames(residual, pts[-1])
        info = json.loads(self.runner.run(
            ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
             "stream=nb_read_packets,avg_frame_rate:stream_tags=timecode", "-of", "json", out], pause="stop"))
        st = info["streams"][0]
        got = int(st["nb_read_packets"])
        problems = []
        # Phones: uneven frame spacing makes the last frame's end fuzzier, so allow ±2
        # (still catches real loss); Pi cameras stay at ±1.
        if abs(got - want) > (2 if man.get("source") == "phone" else 1):
            problems.append("frames %d, expected %d" % (got, want))
        if st.get("avg_frame_rate") != "30/1":
            problems.append("frame rate %s" % st.get("avg_frame_rate"))
        if (st.get("tags") or {}).get("timecode") != tc:
            problems.append("timecode %s, expected %s" % ((st.get("tags") or {}).get("timecode"), tc))
        # Full decode: catches a damaged encode.
        prog = os.path.join(d, "progress.txt")
        dur = man.get("duration_s") or 0
        self.runner.run(["ffmpeg", "-nostdin", "-v", "error", "-progress", prog, "-i", out, "-f", "null", "-"],
                        pause="stop", on_poll=lambda: self.progress(take_id, cam, ffmpeg_progress(prog, dur)))
        if problems:
            raise RuntimeError("verify failed: " + "; ".join(problems))
        repeated = got - len(pts)
        self.mark(take_id, cam, "verified", frames_out=got, frames_in=len(pts), repeated=repeated,
                  dropped=man.get("dropped_frames"), sha256=sha256_file(out))

    def verify_real(self, take_id, cam, man):
        """A rewrapped camera: exactly the source's frames, each at its real time
        (video.pts plus the residual) within TIME_TOLERANCE_MS, the take's timecode,
        and a full decode."""
        d = self.cam_dir(take_id, cam)
        out = self.out_name(take_id, cam)
        tc, origin, residual = grid_origin(man["first_frame_wallclock_ns"])
        pts = read_pts(os.path.join(d, "video.pts"))
        info = json.loads(self.runner.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream_tags=timecode",
             "-of", "json", out], pause="stop"))
        got = sorted(float(t) * 1000 for t in self.runner.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
             "-of", "csv=p=0", out], pause="stop").split())
        problems = []
        if len(got) != len(pts):
            problems.append("frames %d, expected %d" % (len(got), len(pts)))
        off = residual / 1e6
        err = max((abs(g - (p + off)) for g, p in zip(got, pts)), default=0.0)
        if err > TIME_TOLERANCE_MS:
            problems.append("frame times off by up to %.3f ms" % err)
        st = (info.get("streams") or [{}])[0]
        if (st.get("tags") or {}).get("timecode") != tc:
            problems.append("timecode %s, expected %s" % ((st.get("tags") or {}).get("timecode"), tc))
        prog = os.path.join(d, "progress.txt")
        dur = man.get("duration_s") or 0
        self.runner.run(["ffmpeg", "-nostdin", "-v", "error", "-progress", prog, "-i", out, "-f", "null", "-"],
                        pause="stop", on_poll=lambda: self.progress(take_id, cam, ffmpeg_progress(prog, dur)))
        if problems:
            raise RuntimeError("verify failed: " + "; ".join(problems))
        self.mark(take_id, cam, "verified", frames_out=len(got), frames_in=len(pts), repeated=0,
                  max_time_err_ms=round(err, 4), dropped=man.get("dropped_frames"), sha256=sha256_file(out))

    # -- audio (Phase 4 Track A): bounce uploads and the hub mic, staged by hubd
    def audio_pending(self):
        """{take_id: [(relpath, size, mtime_ns)]}: finished files under audio_dir not on the
        drive yet, or changed since (songs.json when a version is added or chosen).
        Only takes ingest knows (an aborted take has no folder to go into)."""
        root, out = self.cfg.get("audio_dir"), {}
        if not root or not os.path.isdir(root):
            return out
        done = self.state.setdefault("audio", {})
        for take_id in sorted(os.listdir(root)):
            if take_id not in self.state["takes"]:
                continue
            base = os.path.join(root, take_id)
            for d, _, files in os.walk(base):
                for fn in sorted(files):
                    if fn.endswith((".part", ".tmp")):
                        continue
                    p = os.path.join(d, fn)
                    st = os.stat(p)
                    rel = os.path.relpath(p, base)
                    have = done.get(take_id, {}).get(rel)
                    if not have or (have["size"], have["mtime_ns"]) != (st.st_size, st.st_mtime_ns):
                        out.setdefault(take_id, []).append((rel, st.st_size, st.st_mtime_ns))
        return out

    def copy_audio(self, todo):
        need = sum(size for items in todo.values() for _, size, _ in items) / 1e9 + 0.1
        with self.writing():
            free = self.drive.free_gb()
            if free is not None and free < need:
                raise RuntimeError("drive full: %.1f GB free, need %.1f for audio" % (free, need))
            count, index = sum(len(items) for items in todo.values()), 0
            for take_id, items in todo.items():
                for rel, size, mtime_ns in items:
                    index += 1
                    self.doing("audio", take=take_id, file=os.path.basename(rel), index=index, count=count)
                    self._copy_audio_file(take_id, rel, size, mtime_ns)
        self.state["drive_free_gb"] = self.drive_free()

    def _copy_audio_file(self, take_id, rel, size, mtime_ns):
        src = os.path.join(self.cfg["audio_dir"], take_id, rel)
        dst = os.path.join(self.take_folder(take_id), "audio", rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        sha = self.copy_verified(src, dst)
        self.state["audio"].setdefault(take_id, {})[rel] = {
            "size": size, "mtime_ns": mtime_ns, "sha256": sha, "path": dst}
        self.save()

    # -- takes deleted from the drive by the user
    def note_drive_of_takes(self, drive_id):
        """Remember which drive each ingested take is on (only takes whose folder is there)."""
        for take_id, take in (self.state.get("takes") or {}).items():
            if take.get("complete") and not take.get("drive_id") and os.path.isdir(self.take_folder(take_id)):
                take["drive_id"] = drive_id

    def forget_removed_takes(self):
        """Takes whose folder the user deleted from their drive (e.g. on the Mac) are removed
        from the hub: from the page, the queues and the hub's own copies (thumbnails,
        proxies, waveforms, edits, staged audio). Only takes known to be on the drive that is
        plugged in now are judged, so plugging in another drive removes nothing.
        The cameras' copies are verified, so ingest never pulls them again; the cameras
        delete them by themselves when they need the space."""
        drive_id = self.drive.filesystem_id()
        if not drive_id or not self.drive.mounted():
            return []
        self.note_drive_of_takes(drive_id)
        removed = []
        for take_id, take in sorted((self.state.get("takes") or {}).items()):
            if take.get("drive_id") == drive_id and not os.path.isdir(self.take_folder(take_id)):
                removed.append(take_id)
        for take_id in removed:
            self.forget_take(take_id, "deleted from the drive")
        if removed:
            self.save()
        return removed

    def forget_take(self, take_id, why):
        if not TAKE_RE.match(take_id):
            return
        take = (self.state.get("takes") or {}).pop(take_id, None) or {}
        self.state.setdefault("removed_takes", {})[take_id] = {
            "at": self.clock(), "why": why, "drive_id": take.get("drive_id")}
        for jobs in (self.state.get("renders") or {}, self.state.get("syncs") or {}):
            for key in [k for k in jobs if k.startswith(take_id + "/")]:
                del jobs[key]
        (self.state.get("audio") or {}).pop(take_id, None)
        (self.state.get("videos") or {}).pop(take_id, None)
        ((self.state.get("drive_index") or {}).get("takes") or {}).pop(take_id, None)
        for kind in ("thumbs", "proxies", "beats", "motion"):        # failures keyed "<take>/<camera or song>"
            failed = self.state.get(kind + "_failed") or {}
            for key in [k for k in failed if k.startswith(take_id + "/")]:
                del failed[key]
        hubd_state = self.cfg["hubd_state_dir"]
        folders = [os.path.join(self.cfg["thumbs_dir"], take_id), os.path.join(self.cfg["proxies_dir"], take_id),
                   os.path.join(hubd_state, "waveforms", take_id), os.path.join(hubd_state, "edits", take_id),
                   os.path.join(self.cfg["audio_dir"], take_id), os.path.join(self.work, take_id)]
        if self.cfg.get("phones_dir"):
            folders.append(os.path.join(self.cfg["phones_dir"], take_id))   # left only if ingest stopped half way
        for folder in folders:
            shutil.rmtree(folder, ignore_errors=True)
        self.forget_finished_uploads(take_id)
        sys.stderr.write("take %s removed: %s\n" % (take_id, why))

    def forget_finished_uploads(self, take_id):
        """hub-upload's records of the take's finished (or failed) YouTube uploads. The videos
        stay on the channel; unfinished uploads are left alone (hubd refuses to delete a take
        while one is still going)."""
        spool = self.cfg.get("youtube_spool_dir")
        for name in sorted(os.listdir(spool)) if spool and os.path.isdir(spool) else []:
            if not (name.startswith(take_id + "_") and name.endswith(".json")):
                continue
            record = youtube_upload.read_record(spool, name[:-len(".json")])
            if record and record.get("status") in youtube_upload.WAITING:
                continue
            for leftover in (name, name[:-len(".json")] + ".mp4"):
                try:
                    os.remove(os.path.join(spool, leftover))
                except OSError:
                    pass

    # -- takes deleted on the hub page ("delete take")
    def queue_delete(self, doc):
        """hubd has checked the take can go (ingested, on this drive, nothing running for it).
        The delete itself waits for a quiet rig (run_deletes)."""
        take = (self.state.get("takes") or {}).get(doc.get("take") or "")
        if take is not None and TAKE_RE.match(doc["take"]):
            take.update(deleting=True, delete_error=None)
            self.save()

    def run_deletes(self):
        """Delete the takes asked for: their folder on the drive, everything the hub keeps for
        them (forget_take) and the copies left on the cameras (a take whose ingest failed is
        still there). Waits while the rig is busy, and for the drive when part of the take is
        on it."""
        doomed = sorted(t for t, take in (self.state.get("takes") or {}).items() if take.get("deleting"))
        if not doomed or self.rig_busy():
            return []
        drive_in = self.drive.present() and self.state.get("drive") != "ejected"
        deleted = []
        for take_id in doomed:
            if not drive_in and self.on_drive(take_id):
                continue
            try:
                self.delete_take(take_id)
                deleted.append(take_id)
            except (RuntimeError, OSError) as e:
                self.state["takes"][take_id].update(deleting=False, delete_error=str(e)[-300:])
                sys.stderr.write("delete %s: %s\n" % (take_id, e))
        self.save()
        return deleted

    def on_drive(self, take_id):
        """Is part of the take on the STUDIO drive (so its delete needs the drive)?"""
        take = self.state["takes"][take_id]
        return bool(take.get("drive_id") or take.get("adopted")
                    or take_id in ((self.state.get("drive_index") or {}).get("takes") or {})
                    or any("written" in (c.get("done") or []) for c in take.values() if isinstance(c, dict)))

    def delete_take(self, take_id):
        take = self.state["takes"][take_id]
        if self.drive.present() and self.state.get("drive") != "ejected":
            drive_id = self.drive.filesystem_id()
            if take.get("drive_id") and take["drive_id"] != drive_id:
                raise RuntimeError("the take is on another drive")
            folder = self.take_folder(take_id)
            # Renamed first, so a drive pulled half way leaves no half take that looks real
            # (index_drive only reads folders named like takes); a leftover goes on the next try.
            doomed = os.path.join(os.path.dirname(folder), ".deleting-" + os.path.basename(folder))
            with self.writing():
                if os.path.isdir(folder):
                    os.replace(folder, doomed)
                if os.path.isdir(doomed):
                    shutil.rmtree(doomed)
        self.forget_take(take_id, "deleted on the hub page")
        for cam in self.cfg["nodes"]:
            self.delete_on_camera(take_id, cam)

    def deleted_on_hub_page(self, take_id):
        return (self.state.get("removed_takes") or {}).get(take_id, {}).get("why") == "deleted on the hub page"

    def delete_on_camera(self, take_id, cam):
        """Ask a camera to delete its copy of a take deleted on the hub page. An offline camera
        stays in the record's left_on and is asked again when ingest next finds the take on it
        (pending)."""
        record = self.state["removed_takes"][take_id]
        left = set(record.get("left_on") or [])
        try:
            self.http.post(self.node_url(cam, "/takes/%s/delete" % take_id), {})
            left.discard(cam)
        except Exception as e:  # noqa: BLE001 - camera offline (or an old camnode): try again later
            left.add(cam)
            sys.stderr.write("delete %s on %s: %s\n" % (take_id, cam, e))
        record["left_on"] = sorted(left)

    def look_at_drive(self):
        """While the rig is idle, every removed_check_s and whenever a different drive is
        plugged in: mount read-only if needed, forget takes deleted from the drive, index
        what's on it and adopt takes this hub doesn't know (stick-db-plan S2, S5)."""
        index = self.state.get("drive_index") or {}
        present = self.drive.present() and self.state.get("drive") != "ejected"
        if index and index.get("present") != present:
            index["present"] = present              # the page greys the takes out while it's away
            self.save()
        if self.rig_busy() or not present:
            return
        drive_id = self.drive.filesystem_id()
        due = self.clock() - self.last_removed_check >= self.cfg["removed_check_s"]
        if not due and drive_id and drive_id == index.get("drive_id"):
            return
        self.last_removed_check = self.clock()
        try:
            with self.reading_drive():
                self.forget_removed_takes()
                self.index_drive(drive_id)
                self.adopt_drive_takes()
        except RuntimeError:
            pass

    def index_drive(self, drive_id):
        if not drive_id or not self.drive.mounted():
            return
        index = drive_records.index_drive(self.drive.mp, drive_id, self.clock())
        index["present"] = True
        self.state["drive_index"] = index
        self.save()

    def adopt_drive_takes(self):
        """Takes on the drive this hub doesn't know (the drive comes from another hub, or the
        SD card was set up again) join the list as ingested. Their bounces and syncs are
        copied back to the SD staging area, so sync and render work as for any take; their
        thumbnails and proxies wait until the editor opens them (wants_caches)."""
        index = self.state.get("drive_index") or {}
        takes = self.state.setdefault("takes", {})
        adopted = []
        for take_id, described in sorted((index.get("takes") or {}).items()):
            if take_id in takes or not TAKE_RE.match(take_id):
                continue
            takes[take_id] = drive_records.adopted_take_state(described, index["drive_id"])
            (self.state.get("removed_takes") or {}).pop(take_id, None)     # it's back
            if described["videos"]:
                self.state.setdefault("videos", {})[take_id] = described["videos"]
            self.restore_audio(take_id)
            adopted.append(take_id)
        if adopted:
            sys.stderr.write("adopted from the drive: %s\n" % ", ".join(adopted))
            self.save()
        return adopted

    def restore_audio(self, take_id):
        """Copy the take's audio/ from the drive to the SD staging area, unless the hub has it.
        Recorded as already on the drive, so audio_pending doesn't copy it back."""
        source = os.path.join(self.take_folder(take_id), "audio")
        staged = os.path.join(self.cfg["audio_dir"], take_id)
        if not os.path.isdir(source) or os.path.exists(staged):
            return
        shutil.copytree(source, staged + ".part", copy_function=shutil.copy2)
        os.replace(staged + ".part", staged)
        copied = self.state.setdefault("audio", {}).setdefault(take_id, {})
        for folder, _, files in os.walk(staged):
            for name in files:
                path = os.path.join(folder, name)
                info = os.stat(path)
                rel = os.path.relpath(path, staged)
                copied[rel] = {"size": info.st_size, "mtime_ns": info.st_mtime_ns,
                               "path": os.path.join(source, rel)}

    def reindex_take(self, take_id):
        """Bring one take's index entry up to date (just written), if the index is this drive's."""
        index = self.state.get("drive_index") or {}
        if index.get("drive_id") and index.get("drive_id") == self.drive.filesystem_id():
            described = drive_records.describe_take(self.take_folder(take_id))
            if described:
                index.setdefault("takes", {})[take_id] = described

    @staticmethod
    def wants_caches(take):
        """Thumbnails and proxies: for every take ingested here; for a take adopted from the
        drive only once the editor has opened it (an old drive may hold many takes)."""
        return not take.get("adopted") or take.get("caches_wanted")

    # -- the take's lasting records on the drive (stick-db-plan S1, S4)
    def take_log(self):
        return drive_records.read_take_log(os.path.join(self.cfg["hubd_state_dir"], "takes.json"))

    def drive_records_pending(self):
        """What to write to the drive's take folders: ([take_id] whose take.json lacks the
        current take-log record, [(take_id, song, path)] working cuts newer than the drive's).
        Only takes in the index of the drive that's plugged in."""
        index = self.state.get("drive_index") or {}
        if not index.get("present") or index.get("drive_id") != self.drive.filesystem_id():
            return [], []
        indexed = index.get("takes") or {}
        log = self.take_log()
        records = [take_id for take_id, described in sorted(indexed.items())
                   if (recording := drive_records.recording_for(log, take_id))
                   and drive_records.digest(recording) != drive_records.digest(described.get("recording"))]
        edits = []
        edits_root = os.path.join(self.cfg["hubd_state_dir"], "edits")
        for take_id, described in sorted(indexed.items()):
            folder = os.path.join(edits_root, take_id)
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for name in names:
                if not name.endswith(".json"):
                    continue
                path = os.path.join(folder, name)
                song = name[:-len(".json")]
                if self.clock() - os.path.getmtime(path) < self.cfg["working_edit_delay_s"]:
                    continue                     # still being edited: wait for a pause
                edit = drive_records.read_json(path)
                if isinstance(edit, dict) and drive_records.digest(edit) != (described.get("working_edits") or {}).get(song):
                    edits.append((take_id, song, path))
        return records, edits

    def update_drive_records(self):
        """Write pending records and working cuts. Small files: needs only a quiet rig and
        the drive, not ingest's idle wait, like syncs."""
        if self.rig_busy() or not self.drive.present() or self.state.get("drive") == "ejected" \
                or not self.drive.check():
            return 0
        records, edits = self.drive_records_pending()
        if not records and not edits:
            return 0
        indexed = self.state["drive_index"]["takes"]
        log = self.take_log()
        with self.writing():
            for take_id in records:
                recording = drive_records.recording_for(log, take_id)
                path = os.path.join(self.take_folder(take_id), "take.json")
                doc = drive_records.read_json(path)
                if not isinstance(doc, dict):
                    continue
                doc["recording"] = recording
                self.write_json_on_drive(path, doc)
                indexed[take_id]["recording"] = recording
            for take_id, song, source in edits:
                edit = drive_records.read_json(source)
                folder = os.path.join(self.take_folder(take_id), "edits", song)
                os.makedirs(folder, exist_ok=True)
                self.write_json_on_drive(os.path.join(folder, drive_records.WORKING_EDIT), edit)
                indexed[take_id].setdefault("working_edits", {})[song] = drive_records.digest(edit)
        self.save()
        return len(records) + len(edits)

    @staticmethod
    def write_json_on_drive(path, value):
        part = path + ".part"
        with open(part, "w") as f:
            json.dump(value, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, path)

    # -- strip thumbnails for the editor (thumbnails.py)
    @contextlib.contextmanager
    def reading_drive(self):
        """Keep the drive mounted (read-only unless a write is going on) while reading it."""
        with self.drive_lock:
            if not self.drive.present() or not self.drive.check():
                raise RuntimeError("STUDIO drive not available")
            self.rendering = True                 # the drive keeper leaves it mounted meanwhile
            if not self.writing_n:
                self.drive.mount(rw=False)
        try:
            yield
        finally:
            with self.drive_lock:
                self.rendering = False

    def thumbnails_pending(self):
        """(take, camera) pairs of ingested takes that have no thumbnails yet."""
        failed = self.state.get("thumbs_failed") or {}
        out = []
        for take_id, take in sorted((self.state.get("takes") or {}).items()):
            if not take.get("complete") or not self.wants_caches(take):
                continue
            for camera_id in sorted(k for k, v in take.items() if isinstance(v, dict) and "frames_cam" in v):
                key = "%s/%s" % (take_id, camera_id)
                out_dir = os.path.join(self.cfg["thumbs_dir"], take_id, camera_id)
                if not failed_in_this_version(failed, key) and not thumbnails.have_thumbnails(out_dir):
                    out.append((take_id, camera_id))
        return out

    def make_thumbnails(self, pending):
        """Make thumbnails for each pending camera; pauses (raises) when the rig gets busy."""
        with self.reading_drive():
            for index, (take_id, camera_id) in enumerate(pending):
                if self.rig_busy():
                    raise Paused()
                self.doing("thumbnails", take=take_id, camera=camera_id, index=index + 1, count=len(pending))
                mov = os.path.join(self.take_folder(take_id), camera_id + ".mov")
                out_dir = os.path.join(self.cfg["thumbs_dir"], take_id, camera_id)
                try:
                    thumbnails.make_thumbnails(mov, out_dir)
                except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as e:
                    self.state.setdefault("thumbs_failed", {})["%s/%s" % (take_id, camera_id)] = failure(e)
                self.save()

    # -- editing proxies for the editor (proxies.py)
    def proxies_pending(self):
        """(take, camera) pairs of ingested takes without an editing proxy yet."""
        failed = self.state.get("proxies_failed") or {}
        out = []
        # Only takes the editor can open (a synced song), newest first (user, 2026-11-16:
        # only the latest take gets edited; no proxies for old takes).
        for take_id, take in sorted((self.state.get("takes") or {}).items(), reverse=True):
            if not take.get("complete") or not self.wants_caches(take) or not self.has_synced_song(take_id):
                continue
            for camera_id in sorted(k for k, v in take.items() if isinstance(v, dict) and "frames_cam" in v):
                path = os.path.join(self.cfg["proxies_dir"], take_id, camera_id + ".mp4")
                if not failed_in_this_version(failed, "%s/%s" % (take_id, camera_id)) and not os.path.exists(path):
                    out.append((take_id, camera_id))
        return out

    def has_synced_song(self, take_id):
        take_audio = os.path.join(self.cfg["audio_dir"], take_id)
        try:
            songs = [d for d in os.listdir(take_audio) if os.path.isdir(os.path.join(take_audio, d))]
        except OSError:
            return False
        for song in songs:
            song_dir = os.path.join(take_audio, song)
            for version in os.listdir(song_dir):
                if os.path.exists(os.path.join(song_dir, version, "sync.json")):
                    return True
        return False

    def make_proxies(self, pending):
        """Encode each pending proxy. The encoder is suspended while the rig is busy (a take
        or live session gets the CPU) and stopped by an Eject (it's made again later)."""
        def rig_is_busy():
            busy = self.rig_busy()
            self.save(status="paused: rig busy" if busy else "ingesting")
            return busy
        with self.reading_drive():
            for index, (take_id, camera_id) in enumerate(pending):
                source = os.path.join(self.take_folder(take_id), camera_id + ".mov")
                destination = os.path.join(self.cfg["proxies_dir"], take_id, camera_id + ".mp4")
                self.state["step"] = "proxy %s %s" % (take_id, camera_id)
                self.doing("proxy", take=take_id, camera=camera_id, index=index + 1, count=len(pending), phase="encoding")
                camera = (self.state.get("takes") or {}).get(take_id, {}).get(camera_id) or {}
                try:
                    proxies.make_proxy(source, destination, should_pause=rig_is_busy, should_stop=self.eject_requested,
                                       frames=camera.get("frames_out") or camera.get("frames_cam"), progress=self.doing_progress,
                                       checking=lambda: self.doing_progress(None, phase="checking"))
                except InterruptedError:
                    return
                except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as e:
                    self.state.setdefault("proxies_failed", {})["%s/%s" % (take_id, camera_id)] = failure(e)
                self.save()

    # -- analysis for the automatic cut (docs/editor-plan.md section 3): beats.py, motion.py
    def beats_pending(self):
        """(key, bounce path, beats.json path) for every staged bounce version not analysed yet.
        Beats depend only on the bounce, so every uploaded version is analysed, synced or not."""
        failed = self.state.get("beats_failed") or {}
        out = []
        audio_dir = self.cfg["audio_dir"]
        for take_id in sorted(os.listdir(audio_dir)) if os.path.isdir(audio_dir) else []:
            if not TAKE_RE.match(take_id):
                continue
            take_audio = os.path.join(audio_dir, take_id)
            for song in sorted(os.listdir(take_audio)):
                song_dir = os.path.join(take_audio, song)
                if song == "scratch" or not os.path.isdir(song_dir):
                    continue
                for version in sorted(os.listdir(song_dir)):
                    version_dir = os.path.join(song_dir, version)
                    key = "%s/%s/%s" % (take_id, song, version)
                    if os.path.exists(os.path.join(version_dir, "beats.json")) or failed_in_this_version(failed, key):
                        continue
                    try:
                        with open(os.path.join(version_dir, "version.json")) as f:
                            bounce = os.path.join(version_dir, json.load(f)["filename"])
                    except (OSError, ValueError, KeyError, TypeError):
                        continue                  # still uploading, or not a version folder
                    if os.path.exists(bounce):
                        out.append((key, bounce, os.path.join(version_dir, "beats.json")))
        return out

    def run_beat_analyses(self, pending, popen=subprocess.Popen, poll=0.5):
        """Analyse each pending bounce, one at a time, niced, in a separate process (it holds
        the decoded song in memory; hub-ingest itself stays small). Stops when the rig gets busy."""
        for index, (key, bounce, out_path) in enumerate(pending):
            if self.rig_busy():
                return
            take_id, song, version = key.split("/")
            self.state["step"] = "beats %s" % key
            self.doing("beats", take=take_id, song=song, version=version, index=index + 1, count=len(pending))
            command = ["nice", "-n", "19", sys.executable, os.path.join(HERE, "beats.py"), bounce, out_path]
            process = popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            while process.poll() is None:
                time.sleep(poll)
                if self.rig_busy():
                    process.kill()
                    process.wait()
                    return                        # tried again when the rig is quiet
            _, error = process.communicate()
            if process.returncode != 0:
                self.state.setdefault("beats_failed", {})[key] = failure(error or "beats.py failed")
            self.save(step=None)

    def motion_pending(self):
        """(take, camera) pairs with an editing proxy but no movement readings yet."""
        failed = self.state.get("motion_failed") or {}
        out = []
        for take_id, camera_id in self.proxied_cameras():
            key = "%s/%s" % (take_id, camera_id)
            if not failed_in_this_version(failed, key) and not os.path.exists(
                    motion.motion_path(self.cfg["proxies_dir"], take_id, camera_id)):
                out.append((take_id, camera_id))
        return out

    def proxied_cameras(self):
        proxies_dir = self.cfg["proxies_dir"]
        for take_id in sorted(os.listdir(proxies_dir)) if os.path.isdir(proxies_dir) else []:
            take_dir = os.path.join(proxies_dir, take_id)
            if TAKE_RE.match(take_id) and os.path.isdir(take_dir):
                for name in sorted(os.listdir(take_dir)):
                    if name.endswith(".mp4"):
                        yield take_id, name[:-len(".mp4")]

    def measure_motion(self, pending):
        """Movement readings from each pending proxy's keyframes (cheap: a few seconds per
        camera-hour). Stops when the rig gets busy."""
        for index, (take_id, camera_id) in enumerate(pending):
            if self.rig_busy():
                raise Paused()
            proxy = os.path.join(self.cfg["proxies_dir"], take_id, camera_id + ".mp4")
            self.state["step"] = "motion %s %s" % (take_id, camera_id)
            self.doing("motion", take=take_id, camera=camera_id, index=index + 1, count=len(pending))
            try:
                motion.write_json(motion.motion_path(self.cfg["proxies_dir"], take_id, camera_id),
                                  motion.measure(proxy))
            except (OSError, ValueError, subprocess.CalledProcessError) as e:
                self.state.setdefault("motion_failed", {})["%s/%s" % (take_id, camera_id)] = failure(e)
            self.save(step=None)

    # -- the editor's preparation per camera, for the archive's step chips
    def update_prep(self):
        """Where each camera of an ingested take stands with its thumbnails, preview copy and
        movement readings, as "prep" in its take record (the steps after Mark on camera):
        done, failed (with the error), waiting (made when the hub is idle) or later (a preview
        copy waits for a synced song; an adopted take's caches for the editor to open it)."""
        failed = {kind: self.state.get(kind + "_failed") or {} for kind in ("thumbs", "proxies", "motion")}

        def step(done, failures, key, ready):
            if done:
                return {"state": "done"}
            if failed_in_this_version(failures, key):
                return {"state": "failed", "error": failures[key].get("error")}
            return {"state": "waiting" if ready else "later"}

        for take_id, take in (self.state.get("takes") or {}).items():
            if not take.get("complete"):
                continue
            wanted = bool(self.wants_caches(take))
            synced = None
            for camera_id, cs in take.items():
                if not (isinstance(cs, dict) and "frames_cam" in cs):
                    continue
                key = "%s/%s" % (take_id, camera_id)
                if synced is None:
                    synced = self.has_synced_song(take_id)
                has_proxy = os.path.exists(os.path.join(self.cfg["proxies_dir"], take_id, camera_id + ".mp4"))
                proxy = step(has_proxy, failed["proxies"], key, wanted and synced)
                cs["prep"] = {
                    "thumbnails": step(thumbnails.have_thumbnails(os.path.join(self.cfg["thumbs_dir"], take_id, camera_id)),
                                       failed["thumbs"], key, wanted),
                    "proxy": proxy,
                    "motion": step(os.path.exists(motion.motion_path(self.cfg["proxies_dir"], take_id, camera_id)),
                                   failed["motion"], key, proxy["state"] in ("done", "waiting")),
                }

    # -- automatic bounce sync (Phase 4 Track A, audiosync.py)
    def queue_sync(self, doc):
        take, song, v = doc.get("take"), doc.get("song"), doc.get("version")
        if not (TAKE_RE.match(take or "") and isinstance(v, int) and re.match(r"^[a-z0-9-]+$", song or "")):
            return
        key = "%s/%s/v%d" % (take, song, v)
        jobs = self.state.setdefault("syncs", {})
        if (jobs.get(key) or {}).get("status") in ("queued", "syncing"):
            return
        jobs[key] = {"take": take, "song": song, "version": v, "status": "queued", "queued_at": self.clock()}
        self.save()

    def run_syncs(self, popen=subprocess.Popen, poll=1.0):
        """Queued syncs, one at a time, niced, in a separate process. Doesn't need the drive
        (the audio is staged on the hub). Stops and goes back to the queue if the rig gets busy."""
        for key, job in sorted((self.state.get("syncs") or {}).items()):
            if job["status"] != "queued":
                continue
            if self.rig_busy():
                return
            job.update(status="syncing", started=self.clock(), error=None)
            self.save()
            cmd = ["nice", "-n", "19", sys.executable, os.path.join(HERE, "audiosync.py"),
                   os.path.join(self.cfg["audio_dir"], job["take"]), job["song"], str(job["version"]),
                   json.dumps(self.cfg.get("sync") or {})]
            p = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            while p.poll() is None:
                time.sleep(poll)
                if self.rig_busy():
                    p.kill()
                    p.wait()
                    job.update(status="queued", note="waits: a take or live session started")
                    self.save()
                    return
            out, err = p.communicate()
            try:
                res = json.loads(out.strip().splitlines()[-1])
            except (ValueError, IndexError):
                job.update(status="error", error=(err or out or "no output")[-400:], finished=self.clock())
                self.save()
                continue
            if res.get("failed"):
                job.update(status="needs sync", reason=res.get("reason"), finished=self.clock())
            else:
                job.update(status="synced", anchor=res.get("anchor"), confidence=res.get("confidence"),
                           finished=self.clock())
            job.pop("note", None)
            self.save()

    def syncs_ready(self):
        return any(j["status"] == "queued" for j in (self.state.get("syncs") or {}).values())

    # -- quick mix renders (Phase 4 A4, render.py)
    def queue_render(self, doc):
        """A quick mix ("render"), or the editor's edit (kind "edit": the finished video)."""
        take, song, v = doc.get("take"), doc.get("song"), doc.get("version")
        if not (TAKE_RE.match(take or "") and isinstance(v, int) and re.match(r"^[a-z0-9-]+$", song or "")):
            return
        kind = "edit" if doc.get("kind") == "edit" else "quick"
        # The editor's videos: one job per song (its edit belongs to the song); `version`
        # is the bounce version that goes into the video.
        key = "%s/%s:edit" % (take, song) if kind == "edit" else "%s/%s/v%d" % (take, song, v)
        jobs = self.state.setdefault("renders", {})
        if (jobs.get(key) or {}).get("status") in ("queued", "rendering", "checking", "writing"):
            return
        jobs[key] = {"take": take, "song": song, "version": v, "kind": kind, "status": "queued",
                     "queued_at": self.clock(), "progress": None, "error": None,
                     # The editor's "Upload to YouTube": the finished video goes on to hub-upload.
                     "youtube": kind == "edit" and doc.get("youtube") is True,
                     "song_name": str(doc.get("song_name") or song)[:200], "upload": None,
                     "take_name": str(doc.get("take_name") or "")[:200] or None}     # named on the hub page
        self.manual = True                    # asked for by the user: don't wait for 30 min idle
        self.save()

    def renders_ready(self):
        """A queued render whose take is on the drive (others wait for its ingest)."""
        return any(j["status"] == "queued" and (self.state["takes"].get(j["take"]) or {}).get("complete")
                   for j in (self.state.get("renders") or {}).values())

    def run_renders(self):
        for key, job in sorted((self.state.get("renders") or {}).items()):
            if job["status"] != "queued":
                continue
            t = self.state["takes"].get(job["take"]) or {}
            if not t.get("complete"):
                job["status_note"] = "waiting for the take's own ingest"
                continue
            try:
                if job.get("kind") == "edit":
                    self.render_edit_job(job)
                else:
                    self.render_job(job)
            except Paused:
                job["status"] = "queued"
                raise
            except render.Aborted:
                job.update(status="queued", progress=None, status_note="stopped for Eject; starts again later")
                self.save()
                return
            except Exception as e:  # noqa: BLE001 - one bad render must not stop ingest
                job.update(status="error", error=str(e)[-500:], progress=None)
                self.save()

    def _audio_src(self, job):
        d = os.path.join(self.cfg["audio_dir"], job["take"], job["song"], "v%d" % job["version"])
        try:
            with open(os.path.join(d, "version.json")) as f:
                ver = json.load(f)
            with open(os.path.join(d, "sync.json")) as f:
                sync = json.load(f)
        except (OSError, ValueError) as e:
            raise RuntimeError("song not uploaded or not synced: %s" % e) from None
        return d, ver, sync

    def render_job(self, job):
        d, ver, sync = self._audio_src(job)
        bounce = os.path.join(d, sync.get("file") or ver["filename"])
        out_dir = os.path.join(self.work, "renders", job["take"])
        os.makedirs(out_dir, exist_ok=True)
        base = "%s-v%d-quick" % (job["song"], job["version"])
        tmp = os.path.join(out_dir, base + ".tmp.mp4")
        job.update(status="rendering", progress=0.0, error=None, status_note=None, started=self.clock())
        self.save(current_render=job["take"])
        with self.drive_lock:
            if not self.drive.present() or not self.drive.check():
                raise RuntimeError("STUDIO drive not available")
            self.rendering = True
            if not self.writing_n:
                self.drive.mount(rw=False)
        try:
            folder = self.take_folder(job["take"])
            with open(os.path.join(folder, "take.json")) as f:
                doc = json.load(f)
            edit = render.quick_cut(doc, int(sync["bounce_start_ns"]), ver["duration_s"], ver.get("song") or job["song"],
                                    job["version"], "audio/%s/v%d/%s" % (job["song"], job["version"], os.path.basename(bounce)),
                                    shot_s=self.cfg["quick_shot_s"])
            edit["sync"] = {k: sync.get(k) for k in ("anchor", "confidence", "drift_ppm")}
            segs, astart, frames = render.plan(edit, doc)
            job.update(frames=frames, shots=len(segs), length_s=round(frames / render.FPS, 1))
            t0, last = time.monotonic(), [0.0]

            def progress(done):
                job["progress"] = round(done / frames, 3)
                el = time.monotonic() - t0
                job["eta_s"] = round(el / done * (frames - done)) if done else None
                if time.monotonic() - last[0] > 2:
                    last[0] = time.monotonic()
                    self.save()

            def busy():
                b = self.rig_busy()
                if b != (job["status"] == "paused"):
                    job["status"] = "paused" if b else "rendering"
                    self.save(status="paused: rig busy" if b else "ingesting")
                return b
            render.render(segs, folder, bounce, astart, frames, tmp, busy, should_abort=self.eject_requested,
                          progress=progress)
            job.update(status="checking", progress=1.0, eta_s=None)
            self.save()
            problems, info = render.check(tmp, segs, frames, folder)
        finally:
            with self.drive_lock:
                self.rendering = False
        job["checked"] = info
        if problems:
            raise RuntimeError("render check failed: " + "; ".join(problems))
        edit_tmp = os.path.join(out_dir, base + ".json")
        with open(edit_tmp, "w") as f:
            json.dump(edit, f, indent=1)
        job["status"] = "writing"
        self.save()
        with self.writing():
            dst_dir = os.path.join(self.take_folder(job["take"]), "edits", job["song"])
            os.makedirs(dst_dir, exist_ok=True)
            name, n = base, 1
            while os.path.exists(os.path.join(dst_dir, name + ".mp4")):   # never replace a file on the drive
                n += 1
                name = "%s-%d" % (base, n)
            mp4 = os.path.join(dst_dir, name + ".mp4")
            self.copy_verified(tmp, mp4)
            self.copy_verified(edit_tmp, os.path.join(dst_dir, name + ".json"))
            size = os.path.getsize(mp4)        # here: the drive is unmounted after the with block
        os.remove(tmp)
        os.remove(edit_tmp)
        job.update(status="done", path=mp4, file=os.path.relpath(mp4, self.take_folder(job["take"])),
                   finished=self.clock(), bytes=size)
        self.state["drive_free_gb"] = self.drive_free()
        self.save(current_render=None)

    def render_edit_job(self, job):
        """The editor's edit as the finished video: <take>/edits/<song>/<song>-v<N>.mp4 plus
        the edit itself (.json) on the drive; never replaces a file (-2, -3, ...)."""
        audio_dir, version, sync = self._audio_src(job)
        bounce = os.path.join(audio_dir, sync.get("file") or version["filename"])
        edit_path = os.path.join(self.cfg["hubd_state_dir"], "edits", job["take"], job["song"] + ".json")
        with open(edit_path) as f:
            edit = json.load(f)
        edit["song_version"] = job["version"]                     # the bounce in this video
        edit["bounce_start_ns"] = int(sync["bounce_start_ns"])
        with open(self.cfg["looks_path"]) as f:
            looks = json.load(f)
        out_dir = os.path.join(self.work, "renders", job["take"])
        os.makedirs(out_dir, exist_ok=True)
        base = "%s-video" % job["song"]
        tmp = os.path.join(out_dir, base + ".tmp.mp4")
        job.update(status="rendering", progress=0.0, error=None, status_note=None, started=self.clock())
        self.save(current_render=job["take"])
        started = time.monotonic()
        with self.reading_drive():
            folder = self.take_folder(job["take"])
            with open(os.path.join(folder, "take.json")) as f:
                take_doc = json.load(f)

            def progress(done, total):
                job["progress"] = round(done / total, 3) if total else None
                elapsed = time.monotonic() - started
                job["eta_s"] = round(elapsed / done * (total - done)) if done else None
                if done % 150 == 0:
                    self.save()

            def busy():
                is_busy = self.rig_busy()
                if is_busy != (job["status"] == "paused"):
                    job["status"] = "paused" if is_busy else "rendering"
                    self.save(status="paused: rig busy" if is_busy else "ingesting")
                return is_busy
            brand_dir = self.cfg["brand_dir"] if edit.get("branding") else None
            for name in (edit_render.BRAND_BORDER, edit_render.BRAND_LOGO, edit_render.BRAND_LOGO_BOX) if brand_dir else ():
                if not os.path.exists(os.path.join(brand_dir, name)):
                    raise RuntimeError("the border and logo image is missing: %s" % name)
            if title_render.is_shown(edit.get("title")):
                if not os.path.exists(os.path.join(self.cfg["fonts_dir"], title_render.FONT)):
                    raise RuntimeError("the title's font is missing: %s" % title_render.FONT)
            try:            # the bounce's bars, for the beat pulse (beats.json, next to the bounce)
                with open(os.path.join(os.path.dirname(bounce), "beats.json")) as f:
                    downbeats_s = json.load(f).get("downbeats_s") or []
            except (OSError, ValueError):
                downbeats_s = []
            renderer = edit_render.EditRenderer(edit, take_doc, folder, bounce, edit.get("look"), looks, out_dir,
                                                busy=busy, should_abort=self.eject_requested, progress=progress,
                                                brand_dir=brand_dir,
                                                brand_colour=edit.get("branding_colour") or "#ffffff",
                                                downbeats_s=downbeats_s, fonts_dir=self.cfg["fonts_dir"])
            job.update(frames=renderer.total_frames, shots=len(renderer.shots),
                       length_s=round(renderer.total_frames / render.FPS, 1))
            renderer.run(tmp)
        job.update(status="checking", progress=1.0, eta_s=None)
        self.save()
        info = render.probe_output(tmp)
        problems = []
        if info["frames"] != renderer.total_frames:
            problems.append("%d frames, expected %d" % (info["frames"], renderer.total_frames))
        if info["rate"] != "30/1":
            problems.append("frame rate %s" % info["rate"])
        job["checked"] = info
        if problems:
            raise RuntimeError("render check failed: " + "; ".join(problems))
        job["status"] = "writing"
        self.save()
        edit_copy = os.path.join(out_dir, base + ".json")
        with open(edit_copy, "w") as f:
            json.dump(edit, f, indent=1)
        with self.writing():
            # Video versions per song: <song>-video1.mp4, -video2.mp4, ... (never replaced),
            # each with its edit (.json, which names the bounce version inside).
            destination_dir = os.path.join(self.take_folder(job["take"]), "edits", job["song"])
            os.makedirs(destination_dir, exist_ok=True)
            number = 1
            while os.path.exists(os.path.join(destination_dir, "%s%d.mp4" % (base, number))):
                number += 1
            name = "%s%d" % (base, number)
            mp4 = os.path.join(destination_dir, name + ".mp4")
            self.copy_verified(tmp, mp4)
            self.copy_verified(edit_copy, os.path.join(destination_dir, name + ".json"))
            size = os.path.getsize(mp4)
        os.remove(edit_copy)
        upload = None
        if job.get("youtube"):
            # Our copy on the SD card goes to hub-upload; the one on the drive is the master.
            upload = "%s_%s_video%d" % (job["take"], job["song"], number)
            try:
                youtube_upload.spool_video(
                    self.cfg["youtube_spool_dir"], tmp, upload,
                    youtube_upload.video_metadata(job.get("song_name") or job["song"], edit.get("title"), job["take"],
                                                  job.get("take_name")),
                    {"take": job["take"], "song": job["song"], "video_number": number,
                     "file": os.path.relpath(mp4, self.take_folder(job["take"]))}, clock=self.clock)
            except OSError as e:          # the video is safe on the drive; only the upload is lost
                upload = None
                job["upload_error"] = "couldn't queue the YouTube upload: %s" % e
        if os.path.exists(tmp):
            os.remove(tmp)
        job.update(status="done", path=mp4, file=os.path.relpath(mp4, self.take_folder(job["take"])),
                   finished=self.clock(), bytes=size, render_s=round(time.monotonic() - started), video_number=number,
                   upload=upload)
        videos = self.state.setdefault("videos", {}).setdefault(job["take"], {}).setdefault(job["song"], [])
        videos.append({"number": number, "file": job["file"], "bounce_version": job["version"],
                       "made_at": self.clock(), "length_s": job.get("length_s"), "bytes": size})
        self.state["drive_free_gb"] = self.drive_free()
        self.save(current_render=None)

    def copy_verified(self, src, dst):
        """Copy to dst.part, fsync, compare checksums, rename. Pauses (raises) if the rig gets busy."""
        part, h, checked = dst + ".part", hashlib.sha256(), time.monotonic()
        with open(src, "rb") as fi, open(part, "wb") as fo:
            for b in iter(lambda: fi.read(1 << 22), b""):
                if time.monotonic() - checked > 2:
                    checked = time.monotonic()
                    if self.rig_busy():
                        raise Paused()
                h.update(b)
                fo.write(b)
            fo.flush()
            os.fsync(fo.fileno())
        if sha256_file(part) != h.hexdigest():
            os.remove(part)
            raise RuntimeError("copy of %s did not verify" % os.path.basename(dst))
        os.replace(part, dst)
        return h.hexdigest()

    def take_folder(self, take_id):
        day = "%s-%s-%s" % (take_id[0:4], take_id[4:6], take_id[6:8])
        return os.path.join(self.drive.mp, "takes", "%s_%s" % (day, take_id))

    def write(self, take_id, cam):
        with self.writing():
            self._write(take_id, cam)

    def _write(self, take_id, cam):
        folder = self.take_folder(take_id)
        os.makedirs(folder, exist_ok=True)
        src, dst = self.out_name(take_id, cam), os.path.join(folder, "%s.mov" % cam)
        size = os.path.getsize(src)
        need = size / 1e9 + 0.5
        free = self.drive.free_gb()
        if free is not None and free < need:
            raise RuntimeError("drive full: %.1f GB free, need %.1f" % (free, need))
        part = dst + ".part"
        checked, copied = time.monotonic(), 0
        with open(src, "rb") as fi, open(part, "wb") as fo:
            for b in iter(lambda: fi.read(1 << 22), b""):
                if time.monotonic() - checked > 2:
                    checked = time.monotonic()
                    if self.rig_busy():
                        raise Paused()       # .part stays; next attempt overwrites it
                    self.progress(take_id, cam, copied / size if size else None)
                fo.write(b)
                copied += len(b)
            fo.flush()
            os.fsync(fo.fileno())
        if sha256_file(part) != self.cam_state(take_id, cam)["sha256"]:
            os.remove(part)
            raise RuntimeError("copy to drive did not verify")
        os.replace(part, dst)
        self.state["drive_free_gb"] = self.drive_free()
        self.mark(take_id, cam, "written", path=dst, mov_bytes=size)

    def saved_manifest(self, take_id, cam):
        return os.path.join(self.work, take_id, "%s.manifest.json" % cam)

    def manifests(self, take_id, cams):
        """Manifests of every camera in the take: pending ones from the camera, ones
        marked in an earlier round from the copy kept on the hub. A camera with no kept
        copy is already in take.json from an earlier round (the merge keeps it)."""
        out = {}
        for c in self.take_cams(take_id, cams):
            if c in cams:
                out[c] = cams[c]
                continue
            try:
                with open(self.saved_manifest(take_id, c)) as f:
                    out[c] = json.load(f)
            except FileNotFoundError:
                pass
        return out

    def take_cams(self, take_id, cams):
        """Cameras in this take: pending this round, plus any written in an earlier one."""
        known = {c for c, cs in self.state["takes"].get(take_id, {}).items()
                 if isinstance(cs, dict) and "written" in cs.get("done", [])}
        return set(cams) | known

    def write_take_json(self, take_id, cams):
        """take.json on the drive; merged, so a camera ingested later adds to it."""
        folder = self.take_folder(take_id)
        path = os.path.join(folder, "take.json")
        try:
            with open(path) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            doc = {"cameras": {}, "manifests": {}}
        doc.update(take_id=take_id, ingest_version=VERSION, fps=FPS)
        recording = drive_records.recording_for(self.take_log(), take_id)
        if recording:
            doc["recording"] = recording
        for c, m in cams.items():
            doc["cameras"][c] = {k: v for k, v in self.cam_state(take_id, c).items() if k != "done"}
            doc["manifests"][c] = m
        part = os.path.join(folder, "take.json.part")
        with open(part, "w") as f:
            json.dump(doc, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, path)

    def mark_on_camera(self, take_id, cam, man):
        cs = self.cam_state(take_id, cam)
        # Once marked, the camera drops out of pending(): keep its manifest for take.json.
        path = self.saved_manifest(take_id, cam)
        with open(path + ".tmp", "w") as f:
            json.dump(man, f)
        os.replace(path + ".tmp", path)
        src = self.phone_src(take_id, cam)
        if src:
            # Safe on the drive: free the hub's SD (the staging copy is the hub's own, not the drive).
            shutil.rmtree(src, ignore_errors=True)
            try:
                os.rmdir(os.path.dirname(src))       # the take folder, once no phone part is left
            except OSError:
                pass
        else:
            self.http.post(self.node_url(cam, "/takes/%s/verified" % take_id),
                           {"mov_sha256": cs["sha256"], "path": cs["path"]})
        self.mark(take_id, cam, "marked")
        shutil.rmtree(os.path.join(self.work, take_id, cam), ignore_errors=True)
        try:
            os.remove(self.out_name(take_id, cam))
        except OSError:
            pass

    # -- one take
    def ingest_take(self, take_id, cams):
        todo = [c for c in sorted(cams) if "marked" not in self.cam_state(take_id, c)["done"]]
        self.save(current=take_id)
        # Pull every camera in parallel (Wi-Fi airtime is the limit, S3).
        errs = {}

        def pull(c):
            try:
                if "pulled" not in self.cam_state(take_id, c)["done"]:
                    self.begin(take_id, c, "pulled")
                    self.pull(take_id, c)
            except Exception as e:  # noqa: BLE001
                errs[c] = e
        ts = [threading.Thread(target=pull, args=(c,)) for c in todo]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        if any(isinstance(e, Paused) for e in errs.values()):
            raise Paused()
        for c, e in errs.items():
            self.cam_state(take_id, c).update(error="pull: %s" % e, step=None, progress=None)
        self.save()
        for c in todo:
            if c in errs:
                continue
            m = cams[c]
            cs = self.cam_state(take_id, c)
            try:
                for step, fn in (("checked", lambda: self.check_source(take_id, c, m)),
                                 ("conformed", lambda: self.conform(take_id, c, m)),
                                 ("verified", lambda: self.verify(take_id, c, m)),
                                 ("written", lambda: self.write(take_id, c))):
                    if step not in cs["done"]:
                        self.begin(take_id, c, step)
                        fn()
                # Safe on the drive: let the camera free its copy now, unless this is the
                # last camera written (then it waits for take.json, so a failure there
                # leaves the take pending).
                others = self.take_cams(take_id, cams) - {c}
                if "marked" not in cs["done"] and any(
                        "written" not in self.cam_state(take_id, o)["done"] for o in others):
                    self.begin(take_id, c, "marked")
                    self.mark_on_camera(take_id, c, m)
            except Paused:
                raise
            except Exception as e:  # noqa: BLE001 - record, move on to the next camera
                self.record_failure(cs, cs.get("step"), e)
                self.save()
        members = self.take_cams(take_id, cams)
        if members and all("written" in self.cam_state(take_id, c)["done"] for c in members):
            mans = self.manifests(take_id, cams)
            with self.writing():
                self.write_take_json(take_id, mans)
                self.reindex_take(take_id)
            for c in sorted(mans):
                if "marked" not in self.cam_state(take_id, c)["done"]:
                    self.begin(take_id, c, "marked")
                    self.mark_on_camera(take_id, c, mans[c])
            self.state["takes"][take_id]["complete"] = True
            shutil.rmtree(os.path.join(self.work, take_id), ignore_errors=True)
        self.save(current=None, step=None)

    # -- main loop
    def tick(self):
        """One scheduling round. Returns a short status string."""
        self.handle_commands()
        if self.state.get("drive") == "ejected" and self.drive.present():
            self.state["drive"] = None           # plugged back in
        self.state["drive_free_gb"] = self.drive_free()
        self.run_deletes()                  # quick: a quiet rig and the drive are enough
        if self.syncs_ready():              # needs neither the drive nor 30 min idle, only a quiet rig
            self.run_syncs()
        beats_work = self.beats_pending()   # the same: the bounces are staged on the hub
        if beats_work and not self.rig_busy():
            try:
                self.run_beat_analyses(beats_work)
            finally:
                self.done_doing()
        self.look_at_drive()
        try:
            self.update_drive_records()     # small files: a quiet rig and the drive are enough
        except (RuntimeError, OSError) as e:
            sys.stderr.write("drive records: %s\n" % e)
        self.update_prep()                  # a song synced since: its preview copies are waiting now
        ok, why = self.should_run()
        if not ok:
            self.settle_drive()
            self.save(status=why)
            return why
        try:
            todo = self.pending()
            self.register(todo)
            audio = self.audio_pending()
            renders = self.renders_ready()
            thumbs = self.thumbnails_pending()
            proxy_work = self.proxies_pending()
            motion_work = self.motion_pending()
            if not todo and not audio and not renders and not thumbs and not proxy_work and not motion_work:
                self.manual = False
                self.settle_drive()
                self.save(status="idle: nothing to ingest")
                return "nothing to ingest"
            for take_id, cams in todo.items():
                self.save(status="ingesting")
                self.ingest_take(take_id, cams)
            audio = self.audio_pending()        # a take registered just now may have audio
            if audio:
                self.save(status="ingesting", step="audio")
                self.copy_audio(audio)
                self.save(step=None)
            if renders or self.renders_ready():   # a take ingested just now may have one waiting
                self.save(status="ingesting", step="render")
                self.run_renders()
                self.save(step=None)
            thumbs = self.thumbnails_pending()     # takes ingested just now too
            if thumbs:
                self.save(status="ingesting", step="thumbnails")
                self.make_thumbnails(thumbs)
                self.save(step=None)
            proxy_work = self.proxies_pending()
            if proxy_work:
                self.save(status="ingesting", step="proxies")
                self.make_proxies(proxy_work)
                self.save(step=None)
            motion_work = self.motion_pending()     # proxies made just now too
            if motion_work:
                self.save(status="ingesting", step="motion")
                self.measure_motion(motion_work)
                self.save(step=None)
            return "ingested"
        except Paused:
            self.save(status="paused: rig busy")
            return "paused"
        finally:
            self.done_doing()
            if not self.state.get("current"):
                try:
                    self.settle_drive()
                except RuntimeError:
                    pass


class HttpJson:
    def get(self, url, timeout=10):
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)

    def post(self, url, body, timeout=10):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)


def main():
    cfg = load_config(os.environ.get("INGEST_CONFIG", "/etc/hubd/ingest.json"))
    ing = Ingest(cfg)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    sys.stderr.write("hub-ingest %s, nodes %s\n" % (VERSION, ", ".join(cfg["nodes"])))
    threading.Thread(target=ing.keep_drive, args=(stop,), daemon=True).start()
    last = None
    while not stop.is_set():
        try:
            s = ing.tick()
        except Exception as e:  # noqa: BLE001 - never die; report and retry
            s = "error: %s" % e
            ing.save(status=s)
        if s != last:
            sys.stderr.write(s + "\n")
            last = s
        stop.wait(cfg["poll_s"])


if __name__ == "__main__":
    main()
