#!/usr/bin/env python3
"""hubd: studio hub control service (Phase 2).

Polls every camnode, runs REC/STOP/LIVE across the rig, serves the web UI.
Standard library only. Config: /etc/hubd/config.json (rendered by Ansible).
"""
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
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.2.0"
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
    "drive_mount": "/mnt/studio",       # hub-ingest owns mounting; hubd only reads
    "static_dir": os.path.join(HERE, "static"),
    "client_head_start_s": 2.0,
    "sync_timeout_s": 20.0,
    # A camera counts as offline only after this many missed polls in a row (2 s apart).
    # One slow answer (e.g. Wi-Fi busy with ingest pulls) just marks it "slow".
    "offline_after_misses": 3,
    "checks": {"max_offset_ms": 5.0, "max_temp_c": 70.0, "min_free_hours": 1.0},
    # A client can report "Sync achieved" while starting late (S2: up to 969 ms).
    "max_sync_difference_us": 1000,
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


def hub_health(state_dir, sysfs="/sys", vcgencmd="vcgencmd"):
    """The hub's own temperature, fan, clock, throttling, load and SD space.
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
    return {"temp_c": round(t / 1000, 1) if t is not None else None, "fan_rpm": fan,
            "cpu_mhz": mhz // 1000 if mhz else None, "throttled": throttled,
            "load": round(os.getloadavg()[0], 2), "free_gb": free_gb}


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path and os.path.exists(path):
        with open(path) as f:
            user = json.load(f)
        checks = dict(cfg["checks"], **user.pop("checks", {}))
        cfg.update(user)
        cfg["checks"] = checks
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

    def post(self, node, path, body=None, timeout=10):
        req = urllib.request.Request(self._url(node, path), method="POST",
                                     data=json.dumps(body or {}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get("error", str(e))
            except Exception:  # noqa: BLE001
                msg = str(e)
            raise NodeError("%s: %s" % (node, msg)) from None
        except OSError as e:
            raise NodeError("%s: %s" % (node, e)) from None


class NodeError(Exception):
    pass


class Refused(Exception):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def log(msg):
    """One line to the journal (stderr)."""
    sys.stderr.write(msg + "\n")


def arm_problems(name, st, checks):
    """Reasons a node may not start a take. st is its last /status or None."""
    if st is None:
        return ["%s offline" % name]
    p = []
    if st.get("mode") not in ("idle", "live"):   # switching/armed/recording/stopping
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
                      and all(c["exit_code"] == 0 and not c["dropped_frames"] for c in cams.values())),
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

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value):
        self._state = value
        self.gen += 1

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

    def poll_forever(self, stop):
        while not stop.is_set():
            t = time.monotonic()
            self.poll_once()
            self.health = hub_health(self.cfg["state_dir"])
            stop.wait(max(0.1, self.cfg["poll_s"] - (time.monotonic() - t)))

    def snapshot(self):
        with self.lock:
            return {"hub_version": VERSION, "state": self.state, "take": self.take, "hub": self.health,
                    "message": self.message, "sync_server": self.cfg["sync_server"],
                    "nodes": {n: {"online": self.status[n] is not None, "status": self.status[n],
                                  "last_seen": self.seen[n],
                                  "slow": self.status[n] is not None and self.misses[n] > 0,
                                  "problems": arm_problems(n, self.status[n], self.cfg["checks"])}
                              for n in self.cfg["nodes"]},
                    "last_take": self.last_take()}

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

    # -- REC
    def rec(self, names=None):
        if not self.op_lock.acquire(blocking=False):
            raise Refused(["another operation is in progress"])
        try:
            return self._rec(names)
        finally:
            self.op_lock.release()

    def _rec(self, names):
        """names=None (the REC button): every camera that is online; offline ones
        are skipped with a warning. An explicit list is strict: all must be ready.
        Either way, an online camera that isn't ready blocks the take."""
        with self.lock:
            if self.state != "idle":
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
        take_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
        server = self.pick_server(names)
        clients = [n for n in names if n != server]
        with self.lock:
            self.state = "starting"
            self.take = {"take_id": take_id, "nodes": names, "server": server,
                         "started_at": self.clock(), "missing": missing}
            self.message = None
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
                n, "/take/arm", {"take_id": take_id, "role": "client"}))
            if err:
                raise NodeError("arm failed: %s" % err)
            self.sleep(self.cfg["client_head_start_s"])
            self.nodes.post(server, "/take/arm", {"take_id": take_id, "role": "server"})
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
            self.state = "recording"
            self.take["recording_at"] = self.clock()
            self.take["sync_warnings"] = warn
            notes = []
            if missing:
                notes.append("Recording %s only: %s offline." % (", ".join(names), ", ".join(missing)))
            if warn:
                notes.append("Sync warning: %s. Consider STOP and REC again." % "; ".join(warn))
            self.message = " ".join(notes) or None
            return dict(self.take)

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

    def _stop_all(self, names, aborted=None):
        with self.lock:
            self.state = "stopping"
            take = dict(self.take or {})
        # Only nodes that are mid-take; a node that never armed has nothing to stop.
        self.poll_once()
        with self.lock:
            active = [n for n in names if (self.status[n] or {}).get("mode") in ("armed", "recording")]
        manifests, errors = self._parallel(active, lambda n: self.nodes.post(n, "/take/stop", timeout=40))
        summary = take_summary(take.get("take_id"), manifests, errors, self.cfg["max_sync_difference_us"])
        summary.update(server=take.get("server"), missing=take.get("missing", []),
                       started_at=take.get("started_at"),
                       stopped_at=self.clock(), aborted=aborted)
        self.append_log(summary)
        with self.lock:
            self.state, self.take = "idle", None
            self.message = ("take aborted: %s" % aborted) if aborted else None
        return summary

    # -- ingest (separate service; files only)
    def ingest_state(self, limit=20):
        """hub-ingest's state, merged with the take log so that takes still only on
        the cameras are listed too (with recorded=..., and no ingest steps yet)."""
        try:
            with open(os.path.join(self.cfg["ingest_dir"], "state.json")) as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {"status": "ingest service not running", "takes": {}}
        takes = st.setdefault("takes", {})
        for e in self.read_log():                   # later entries win (adopted duplicates)
            tid = e.get("take_id")
            if not tid or e.get("aborted"):
                continue
            cams = e.get("cameras") or {}
            if not cams and "recorded" in takes.get(tid, {}):
                continue                            # an empty duplicate never hides real data
            wc = [c.get("first_frame_wallclock_ns") for c in cams.values() if c.get("first_frame_wallclock_ns")]
            problems = list(e.get("sync_warnings") or [])
            problems += ["%s: %s" % (n, err) for n, err in sorted((e.get("errors") or {}).items())]
            problems += ["%s exited with code %s" % (n, c.get("exit_code")) for n, c in sorted(cams.items())
                         if c.get("exit_code") not in (0, None)]
            if e.get("all_clean") is False and not problems:
                problems.append("not clean: check the take log")
            takes.setdefault(tid, {})["recorded"] = {
                "problems": problems,
                "started_at": min(wc) / 1e9 if wc else e.get("started_at"),
                "length_s": max([c.get("duration_s") or 0 for c in cams.values()] or [0]),
                "cameras": {n: {"frames": c.get("frames"), "dropped": c.get("dropped_frames") or 0}
                            for n, c in sorted(cams.items())},
            }
        st["takes"] = dict(sorted(takes.items())[-limit:])
        return st

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

    def media_file(self, take_id, cam, wait_s=8.0):
        """(path, None) or (None, http_code). Waits for hub-ingest to mount the drive."""
        self.touch_lease()
        p = os.path.join(self.cfg["drive_mount"], "takes",
                         "%s-%s-%s_%s" % (take_id[:4], take_id[4:6], take_id[6:8], take_id), cam + ".mov")
        deadline = time.monotonic() + wait_s
        while not self.drive_mounted():
            if time.monotonic() > deadline:
                return None, 503
            time.sleep(0.25)
        return (p, None) if os.path.isfile(p) else (None, 404)

    def ingest_command(self, cmd):
        d = os.path.join(self.cfg["ingest_dir"], "commands")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "%d-%s.json" % (time.time_ns(), cmd))
        with open(p + ".tmp", "w") as f:
            json.dump({"cmd": cmd}, f)
        os.replace(p + ".tmp", p)
        return {"queued": cmd}

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
            ok, err = self._parallel(names, lambda n: self.nodes.post(n, path, timeout=25))
            self.poll_once()
            return {"ok": sorted(ok), "errors": err}
        finally:
            self.op_lock.release()


# ---------------------------------------------------------------- HTTP

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                ".css": "text/css", ".svg": "image/svg+xml", ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    hub = None
    server_version = "hubd/" + VERSION

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/status"):
            sys.stderr.write("%s %s\n" % (self.command, fmt % args))

    def _send(self, code, body, ctype="application/json"):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    MEDIA = re.compile(r"^/media/(\d{8}-\d{6})/(cam\d+)\.mov$")

    def do_HEAD(self):
        if self.MEDIA.match(self.path.split("?")[0]):
            return self._media(head=True)
        self.send_response(405)
        self.end_headers()

    def _media(self, head=False):
        """Serve a take's .mov from the drive, with Range support (seeking)."""
        take_id, cam = self.MEDIA.match(self.path.split("?")[0]).groups()
        path, err = self.hub.media_file(take_id, cam)
        if err:
            msg = {503: "STUDIO drive not available", 404: "not on the drive"}[err]
            return self._send(err, {"error": msg})
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
        self.send_header("Content-Type", "video/mp4")      # H.264 in .mov; browsers play it as mp4
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
                    if time.monotonic() - touched > 30:     # long reads keep the mount alive
                        self.hub.touch_lease()
                        touched = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass          # viewer seeked/closed, or the drive was ejected mid-read

    def do_GET(self):
        h = self.hub
        if self.MEDIA.match(self.path.split("?")[0]):
            return self._media()
        if self.path == "/api/status":
            return self._send(200, h.snapshot())
        if self.path == "/api/takes":
            return self._send(200, h.read_log())
        if self.path == "/api/ingest":
            return self._send(200, h.ingest_state())
        name = "index.html" if self.path in ("/", "/index.html") else self.path.lstrip("/")
        if "/" in name or name.startswith("."):
            return self._send(404, {"error": "not found"})
        p = os.path.join(h.cfg["static_dir"], name)
        ext = os.path.splitext(name)[1]
        if ext in STATIC_TYPES and os.path.isfile(p):
            with open(p, "rb") as f:
                return self._send(200, f.read(), STATIC_TYPES[ext])
        self._send(404, {"error": "not found"})

    def do_POST(self):
        h = self.hub
        try:
            b = self._body()
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
    srv = make_server(cfg["bind"], cfg["port"])

    def shutdown(*_):
        stop.set()
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    sys.stderr.write("hubd %s on :%d, nodes %s\n" % (VERSION, cfg["port"], ", ".join(cfg["nodes"])))
    srv.serve_forever()


if __name__ == "__main__":
    main()
