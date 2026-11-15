#!/usr/bin/env python3
"""hubd: studio hub control service (Phase 2).

Polls every camnode, runs REC/STOP/LIVE across the rig, serves the web UI.
Standard library only. Config: /etc/hubd/config.json (rendered by Ansible).
"""
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "0.1.0"
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
    "static_dir": os.path.join(HERE, "static"),
    "client_head_start_s": 2.0,
    "sync_timeout_s": 20.0,
    "checks": {"max_offset_ms": 5.0, "max_temp_c": 70.0, "min_free_hours": 1.0},
    # A client can report "Sync achieved" while starting late (S2: up to 969 ms).
    "max_sync_difference_us": 1000,
}

# vcgencmd get_throttled: bits 0-3 are "now" (under-voltage, freq capped,
# throttled, soft temp limit). Bits 16-19 are "has happened since boot".
THROTTLED_NOW_MASK = 0xF


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
        self.state = "idle"                 # idle | starting | recording | stopping
        self.take = None
        self.message = None
        self.pool = ThreadPoolExecutor(max_workers=max(4, 2 * len(cfg["nodes"])))
        os.makedirs(cfg["state_dir"], exist_ok=True)
        self.log_path = os.path.join(cfg["state_dir"], "takes.json")

    # -- polling
    def poll_once(self):
        def one(n):
            try:
                return n, self.nodes.get(n, "/status")
            except Exception:  # noqa: BLE001 - offline is a state, not an error
                return n, None
        for n, st in self.pool.map(one, self.cfg["nodes"]):
            with self.lock:
                self.status[n] = st
                if st is not None:
                    self.seen[n] = self.clock()
        self._adopt()

    def _adopt(self):
        """After a hubd restart, take over a take the cameras are still recording."""
        with self.lock:
            if self.state != "idle":
                return
            active = {n: st["take"] for n, st in self.status.items()
                      if st and st.get("mode") in ("armed", "recording") and st.get("take")}
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
            stop.wait(max(0.1, self.cfg["poll_s"] - (time.monotonic() - t)))

    def snapshot(self):
        with self.lock:
            return {"hub_version": VERSION, "state": self.state, "take": self.take,
                    "message": self.message, "sync_server": self.cfg["sync_server"],
                    "nodes": {n: {"online": self.status[n] is not None, "status": self.status[n],
                                  "last_seen": self.seen[n],
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
            return self._rec(names or list(self.cfg["nodes"]))
        finally:
            self.op_lock.release()

    def _rec(self, names):
        with self.lock:
            if self.state != "idle":
                raise Refused(["rig is %s" % self.state])
        self.poll_once()
        with self.lock:
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
                         "started_at": self.clock()}
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
            self.message = ("Sync warning: %s. Consider STOP and REC again." % "; ".join(warn)) if warn else None
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
        summary.update(server=take.get("server"), started_at=take.get("started_at"),
                       stopped_at=self.clock(), aborted=aborted)
        self.append_log(summary)
        with self.lock:
            self.state, self.take = "idle", None
            self.message = ("take aborted: %s" % aborted) if aborted else None
        return summary

    # -- ingest (separate service; files only)
    def ingest_state(self):
        try:
            with open(os.path.join(self.cfg["ingest_dir"], "state.json")) as f:
                st = json.load(f)
        except (OSError, ValueError):
            return {"status": "ingest service not running", "takes": {}}
        recent = dict(sorted(st.get("takes", {}).items())[-10:])
        st["takes"] = recent
        return st

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

    def do_GET(self):
        h = self.hub
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
