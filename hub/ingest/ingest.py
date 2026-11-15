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
- Writes only to a drive labelled STUDIO, to *.part names, then fsync + rename.
- Never deletes from the drive. Marks a take verified on the camera; the camera
  deletes it later, and only when it needs the space.

Talks to hubd via files in state_dir: state.json (read by hubd/UI) and commands/*.json.
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

VERSION = "0.2.0"
FPS = 30

# ETA model: seconds of work per second of video, per step. Starting values from the
# rig (2026-11-15: conform about 1x real time); learned as takes go through.
DEFAULT_RATES = {"pulled": 1.0, "checked": 0.01, "conformed": 1.0, "verified": 0.25,
                 "written": 0.05, "marked": 0.0}
STEP_OVERHEAD_S = 2.0


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
    "x264_preset": "veryfast",
    "x264_crf": "16",
}


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


def read_pts(path):
    with open(path) as f:
        return [float(l) for l in f if l.strip() and not l.startswith("#")]


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
                p.kill(); p.wait()
                raise RuntimeError("%s timed out" % cmd[0])
            b = self.busy()
            if b and pause == "kill":
                p.kill(); p.wait()
                raise Paused()
            if b and not stopped:
                p.send_signal(signal.SIGSTOP); stopped = True
                self.on_pause(True)
            elif not b and stopped:
                p.send_signal(signal.SIGCONT); stopped = False
                self.on_pause(False)


# ------------------------------------------------------------------ drive

class Drive:
    """The STUDIO drive: found by label, checked on plug-in, mounted only while writing."""

    def __init__(self, cfg, sh=None):
        self.label, self.mp = cfg["drive_label"], cfg["mount_point"]
        self.sh = sh or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True))
        self.dev_path = "/dev/disk/by-label/" + self.label
        self.checked_dev = None
        self.dirty = False

    def present(self):
        return os.path.exists(self.dev_path)

    def device(self):
        return os.path.realpath(self.dev_path)

    def mounted(self):
        try:
            with open("/proc/mounts") as f:
                return any(l.split()[1] == self.mp for l in f)
        except OSError:
            return False

    def check(self):
        """fsck.exfat -n once per plug-in (read-only check). Returns True if clean."""
        dev = self.device()
        if self.checked_dev == dev:
            return not self.dirty
        r = self.sh(["sudo", "-n", "fsck.exfat", "-n", dev])
        self.checked_dev, self.dirty = dev, r.returncode != 0
        return not self.dirty

    def mount(self):
        if self.mounted():
            return
        r = self.sh(["sudo", "-n", "mount", "-t", "exfat", "-o",
                     "uid=%d,gid=%d,umask=022,noatime" % (os.getuid(), os.getgid()), self.device(), self.mp])
        if r.returncode:
            raise RuntimeError("mount failed: %s" % r.stderr.strip())

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
        self.state = self._load()
        self.last_busy = self.state.get("last_busy", 0)
        self.manual = False

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

    def mark(self, take_id, cam, step, **info):
        cs = self.cam_state(take_id, cam)
        if step not in cs["done"]:
            cs["done"].append(step)
        self._learn(cs, step)
        cs.update(info, error=None, step=None, progress=None, step_started=None)
        self.save()

    # -- progress and ETA (for the UI)
    def begin(self, take_id, cam, step):
        cs = self.cam_state(take_id, cam)
        cs.update(step=step, progress=None, step_started=self.clock())
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
            (n.get("status") or {}).get("mode") not in (None, "idle") for n in st.get("nodes", {}).values())
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

    # -- commands from hubd
    def take_commands(self):
        out = []
        for name in sorted(os.listdir(self.cmd_dir)):
            p = os.path.join(self.cmd_dir, name)
            try:
                with open(p) as f:
                    out.append(json.load(f).get("cmd"))
            except (OSError, ValueError):
                pass
            os.remove(p)
        return out

    def handle_commands(self):
        for c in self.take_commands():
            if c == "start":
                self.manual = True
            elif c == "eject":
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
                cs = self.state["takes"].get(m["take_id"], {}).get(cam, {})
                if "marked" in cs.get("done", []):
                    continue
                out.setdefault(m["take_id"], {})[cam] = m
        return dict(sorted(out.items()))

    # -- steps
    def cam_dir(self, take_id, cam):
        d = os.path.join(self.work, take_id, cam)
        os.makedirs(d, exist_ok=True)
        return d

    def pull(self, take_id, cam):
        ssh = "ssh -i %s -o BatchMode=yes -o ConnectTimeout=10" % self.cfg["ssh_key"]
        # rrsync on the camera roots paths at /var/lib/camnode/takes.
        d = self.cam_dir(take_id, cam)
        want = self.cam_state(take_id, cam).get("src_bytes")

        def poll():
            if want:   # rsync's temporary (dot) files count too
                got = sum(e.stat().st_size for e in os.scandir(d) if e.is_file() and e.name != "ts.mkv")
                self.progress(take_id, cam, min(1.0, got / want))
        self.runner.run(["rsync", "-a", "--partial", "--timeout=60", "-e", ssh,
                         "--include=video.h264", "--include=video.pts", "--include=manifest.json",
                         "--exclude=*", "%s.local:%s/" % (cam, take_id), d + "/"],
                        pause="kill", on_poll=poll)
        self.mark(take_id, cam, "pulled")

    def check_source(self, take_id, cam, man):
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
        d = self.cam_dir(take_id, cam)
        tc, origin, residual = grid_origin(man["first_frame_wallclock_ns"])
        mkv = os.path.join(d, "ts.mkv")
        self.runner.run(["mkvmerge", "-q", "-o", mkv, "--timestamps", "0:" + os.path.join(d, "video.pts"),
                         os.path.join(d, "video.h264")], pause="stop")
        out = self.out_name(take_id, cam)
        vf = "setpts=PTS-STARTPTS+%f/TB,fps=fps=%d:start_time=0:round=near" % (residual / 1e9, FPS)
        prog = os.path.join(d, "progress.txt")
        dur = man.get("duration_s") or 0
        self.runner.run(["ffmpeg", "-nostdin", "-v", "error", "-progress", prog, "-y", "-i", mkv, "-map", "0:v", "-vf", vf,
                         "-c:v", "libx264", "-preset", self.cfg["x264_preset"], "-crf", self.cfg["x264_crf"],
                         "-pix_fmt", "yuv420p", "-r", str(FPS), "-video_track_timescale", "30000",
                         "-timecode", tc, "-metadata", "title=%s %s" % (take_id, cam),
                         "-metadata", "comment=studio-multicam %s; conformed to 30.000 fps" % VERSION,
                         "-movflags", "+faststart", "-f", "mov", out + ".tmp"], pause="stop",
                        on_poll=lambda: self.progress(take_id, cam, ffmpeg_progress(prog, dur)))
        os.replace(out + ".tmp", out)
        os.remove(mkv)
        self.mark(take_id, cam, "conformed", timecode=tc)

    def verify(self, take_id, cam, man):
        d = self.cam_dir(take_id, cam)
        out = self.out_name(take_id, cam)
        tc, origin, residual = grid_origin(man["first_frame_wallclock_ns"])
        pts = read_pts(os.path.join(d, "video.pts"))
        want = expected_frames(residual, pts[-1])
        info = json.loads(self.runner.run(
            ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
             "stream=nb_read_packets,avg_frame_rate:stream_tags=timecode", "-of", "json", out], pause="stop"))
        st = info["streams"][0]
        got = int(st["nb_read_packets"])
        problems = []
        if abs(got - want) > 1:
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

    def take_folder(self, take_id):
        day = "%s-%s-%s" % (take_id[0:4], take_id[4:6], take_id[6:8])
        return os.path.join(self.drive.mp, "takes", "%s_%s" % (day, take_id))

    def write(self, take_id, cam):
        self.drive.mount()
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
        self.state["drive_free_gb"] = self.drive.free_gb()
        self.mark(take_id, cam, "written", path=dst)

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
        for c, m in cams.items():
            doc["cameras"][c] = {k: v for k, v in self.cam_state(take_id, c).items() if k != "done"}
            doc["manifests"][c] = m
        part = os.path.join(folder, "take.json.part")
        with open(part, "w") as f:
            json.dump(doc, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part, path)

    def mark_on_camera(self, take_id, cam):
        cs = self.cam_state(take_id, cam)
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
            except Paused:
                raise
            except Exception as e:  # noqa: BLE001 - record, move on to the next camera
                cs.update(error=str(e), step=None, progress=None)
                self.save()
        written = {c: cams[c] for c in cams if "written" in self.cam_state(take_id, c)["done"]}
        if written and len(written) == len(cams):
            self.drive.mount()
            self.write_take_json(take_id, cams)
            for c in cams:
                if "marked" not in self.cam_state(take_id, c)["done"]:
                    self.begin(take_id, c, "marked")
                    self.mark_on_camera(take_id, c)
            self.state["takes"][take_id]["complete"] = True
        self.save(current=None, step=None)

    # -- main loop
    def tick(self):
        """One scheduling round. Returns a short status string."""
        self.handle_commands()
        if self.state.get("drive") == "ejected" and self.drive.present():
            self.state["drive"] = None           # plugged back in
        ok, why = self.should_run()
        if not ok:
            if self.drive.mounted():
                self.drive.unmount()            # mounted only while writing
            self.save(status=why, drive_free_gb=None)
            return why
        try:
            todo = self.pending()
            self.register(todo)
            if not todo:
                self.manual = False
                self.drive.unmount()
                self.save(status="idle: nothing to ingest")
                return "nothing to ingest"
            for take_id, cams in todo.items():
                self.save(status="ingesting")
                self.ingest_take(take_id, cams)
            return "ingested"
        except Paused:
            self.save(status="paused: rig busy")
            return "paused"
        finally:
            if not self.state.get("current"):
                try:
                    self.drive.unmount()
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
