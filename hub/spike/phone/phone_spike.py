#!/usr/bin/env python3
"""Phone camera spike server (docs/phone-spike-plan.md).

Standalone, standard library only; hubd is not touched. Run by hand on the hub:

    python3 phone_spike.py [--port 8090] [--data /var/lib/hubd/phone-spike] [--hub http://localhost]

Phones open http://studio-hub.local:8090/ ; the control page is /control.
With --hub (default http://localhost), it follows hubd: when the rig starts a take,
every connected phone gets REC with the same take id, and STOP when the rig stops.
"""
import argparse
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
NOMINAL_MS = 1000 / 30.0
NAME = re.compile(r"^phone\d{1,2}$")
TAKE = re.compile(r"^\d{8}-\d{6}$")


def log(msg):
    sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


def take_id_now(clock=time.time):
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(clock()))


# ------------------------------------------------------------ take assembly

def fit_clock(samples):
    """Map the page clock (performance.now(), ms) to hub wall clock (ns).

    samples: [{"perf_ms": midpoint of the ping, "hub_ns": hub time, "rtt_ms": round trip}].
    Uses the lowest-RTT half of the samples (the best ones). With two or more
    distinct times, fits offset and rate (drift); with one, offset only.
    Returns (fn, info)."""
    good = sorted((s for s in samples if s.get("rtt_ms") is not None), key=lambda s: s["rtt_ms"])
    if not good:
        raise ValueError("no clock samples")
    good = good[:max(1, (len(good) + 1) // 2)]
    xs = [s["perf_ms"] for s in good]
    ys = [s["hub_ns"] / 1e6 - s["perf_ms"] for s in good]       # offset in ms, per sample
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 60000 ** 2 else 0.0
    def fn(perf_ms):
        return int(round((perf_ms + my + slope * (perf_ms - mx)) * 1e6))
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    info = {"samples": len(samples), "used": n, "drift_ppm": round(slope * 1e6, 2),
            "best_rtt_ms": round(good[0]["rtt_ms"], 2),
            "max_rtt_used_ms": round(good[-1]["rtt_ms"], 2),
            "resid_ms_max": round(max(abs(r) for r in resid), 3)}
    return fn, info


def find_gaps(pts_ms, nominal=NOMINAL_MS):
    gaps = []
    for i in range(1, len(pts_ms)):
        d = pts_ms[i] - pts_ms[i - 1]
        if d > 1.5 * nominal:
            gaps.append({"after_frame": i - 1, "at_s": round(pts_ms[i - 1] / 1000, 3),
                         "gap_ms": round(d, 3), "missing": int(round(d / nominal)) - 1})
    return gaps


def median(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def assemble(d, meta):
    """Join chunk files in d/chunks into video.h264 + video.pts + manifest.json.

    Each chunk is <seq>.h264 plus <seq>.json: {"frames": [{"ts_us", "arr_ms", "key"}, ...]}.
    meta: what the page sent at finish (clock samples, settings, phone info)."""
    cdir = os.path.join(d, "chunks")
    seqs = sorted(int(f[:-5]) for f in os.listdir(cdir) if f.endswith(".json")) if os.path.isdir(cdir) else []
    missing = [s for s in range(seqs[-1] + 1) if s not in seqs] if seqs else []
    frames = []
    tmp = os.path.join(d, "video.h264.part")
    with open(tmp, "wb") as out:
        for s in seqs:
            with open(os.path.join(cdir, "%06d.json" % s)) as f:
                frames += json.load(f)["frames"]
            with open(os.path.join(cdir, "%06d.h264" % s), "rb") as f:
                while True:
                    b = f.read(1 << 20)
                    if not b:
                        break
                    out.write(b)
    os.replace(tmp, os.path.join(d, "video.h264"))

    man = {"schema": 1, "camera": meta.get("phone"), "take_id": meta.get("take_id"),
           "source": "phone-spike-webcodecs", "chunks": len(seqs), "missing_chunks": missing,
           "frames": len(frames), "settings": meta.get("settings"), "capabilities_digest": meta.get("caps_digest"),
           "phone": meta.get("info"), "encoder": meta.get("encoder"), "page_stats": meta.get("stats")}
    if not frames:
        man.update(first_frame_wallclock_ns=None, duration_s=0, gaps=[], dropped_frames=0)
        _write_json(os.path.join(d, "manifest.json"), man)
        return man

    ts0 = frames[0]["ts_us"]
    pts = [(f["ts_us"] - ts0) / 1000.0 for f in frames]
    with open(os.path.join(d, "video.pts.part"), "w") as f:
        f.write("# timecode format v2\n")
        for t in pts:
            f.write("%.3f\n" % t)
    os.replace(os.path.join(d, "video.pts.part"), os.path.join(d, "video.pts"))

    # Which clock is VideoFrame.timestamp on? If it's on the performance.now()
    # timeline, arrival - timestamp is a small positive latency.
    lat = [f["arr_ms"] - f["ts_us"] / 1000.0 for f in frames]
    lat_med = median(lat)
    if 0 <= lat_med < 1000:
        base = "performance"
    elif max(lat) - min(lat) < 100:
        base = "camera-monotonic"        # own clock, constant offset to performance.now()
    else:
        base = "unknown"
    # rate of the frame clock against the page clock (ppm), from the first and last ~30 frames
    k = min(30, len(frames) // 4) or 1
    span = frames[-1]["ts_us"] / 1000.0 - ts0 / 1000.0
    ts_drift_ppm = round((median(lat[-k:]) - median(lat[:k])) / span * 1e6, 1) if span > 10000 else None
    clock_fn, clock_info = fit_clock(meta.get("clock") or [])
    if base == "performance":
        first_perf = ts0 / 1000.0
    else:  # best guess: earliest arrival, less the smallest observed latency
        first_perf = ts0 / 1000.0 + min(lat)
    gaps = find_gaps(pts)
    dur = pts[-1] / 1000.0
    man.update({
        "first_frame_wallclock_ns": clock_fn(first_perf),
        "first_frame_arrival_wallclock_ns": clock_fn(frames[0]["arr_ms"]),
        "last_frame_wallclock_ns": clock_fn(first_perf + pts[-1]),
        "timestamp_base": base,
        "frame_clock_vs_page_ppm": ts_drift_ppm,
        "arrival_latency_ms": {"median": round(lat_med, 2), "min": round(min(lat), 2), "max": round(max(lat), 2)},
        "clock_fit": clock_info,
        "duration_s": round(dur, 3),
        "fps_measured": round((len(pts) - 1) / dur, 4) if dur > 0 else None,
        "keyframes": sum(1 for f in frames if f.get("key")),
        "gaps": gaps,
        "dropped_frames": sum(g["missing"] for g in gaps),
        "files": {n: os.path.getsize(os.path.join(d, n)) for n in ("video.h264", "video.pts")},
    })
    _write_json(os.path.join(d, "manifest.json"), man)
    return man


def _write_json(path, obj):
    with open(path + ".part", "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(path + ".part", path)


# ------------------------------------------------------------ phones and commands

class Rig:
    def __init__(self, data, clock=time.time):
        self.data, self.clock = data, clock
        self.lock = threading.Lock()
        self.phones = {}          # name -> {"seen", "status", "cmd"}
        self.cmd_id = 0
        self.hub_take = None      # take id hubd is recording, if following
        os.makedirs(data, exist_ok=True)

    def poll(self, name, status):
        with self.lock:
            p = self.phones.setdefault(name, {"cmd": None})
            p["seen"], p["status"] = self.clock(), status
            cmd = p["cmd"]
            if cmd and status.get("done_cmd") == cmd["id"]:
                p["cmd"] = cmd = None
            return {"cmd": cmd, "hub_ns": time.time_ns()}

    def command(self, action, names=None, take_id=None):
        if action not in ("rec", "stop"):
            raise ValueError("action must be rec or stop")
        with self.lock:
            now = self.clock()
            targets = [n for n, p in self.phones.items() if now - p["seen"] < 10] if not names else names
            take_id = take_id or take_id_now(self.clock)
            for n in targets:
                self.cmd_id += 1
                self.phones.setdefault(n, {"seen": 0, "status": {}})["cmd"] = {
                    "id": self.cmd_id, "action": action, "take_id": take_id}
            log("%s -> %s (take %s)" % (action, ",".join(targets) or "no phones", take_id))
            return {"sent": targets, "take_id": take_id}

    def snapshot(self):
        with self.lock:
            now = self.clock()
            return {"phones": {n: {"age_s": round(now - p.get("seen", 0), 1), "status": p.get("status"),
                                   "pending": p.get("cmd")} for n, p in sorted(self.phones.items())},
                    "hub_take": self.hub_take}

    def take_dir(self, take_id, phone):
        if not TAKE.match(take_id) or not NAME.match(phone):
            raise ValueError("bad take or phone name")
        return os.path.join(self.data, take_id, phone)

    def follow_hub(self, url, stop, every=1.0):
        """Mirror hubd's REC/STOP onto the phones."""
        while not stop.wait(every):
            try:
                with urllib.request.urlopen(url + "/api/status", timeout=2) as r:
                    st = json.load(r)
            except Exception:  # noqa: BLE001
                continue
            take = (st.get("take") or {}).get("take_id") if st.get("state") in ("starting", "recording") else None
            if take != self.hub_take:
                if take:
                    self.command("rec", take_id=take)
                elif self.hub_take:
                    self.command("stop", take_id=self.hub_take)
                self.hub_take = take


# ------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    rig = None
    CHUNK = re.compile(r"^/api/chunk/([^/]+)/([^/]+)/(\d{1,6})\.(h264|json)$")
    FINISH = re.compile(r"^/api/finish/([^/]+)/([^/]+)$")
    LOG = re.compile(r"^/api/log/([^/]+)$")
    HAVE = re.compile(r"^/api/have/([^/]+)/([^/]+)$")

    def log_message(self, fmt, *args):
        if "/api/poll" not in self.path and "/api/time" not in self.path and "/api/chunk" not in self.path:
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

    def _raw(self):
        n = int(self.headers.get("Content-Length") or 0)
        buf = bytearray()
        while len(buf) < n:
            b = self.rfile.read(min(1 << 20, n - len(buf)))
            if not b:
                raise ValueError("short body")
            buf += b
        return bytes(buf)

    def _json(self):
        raw = self._raw()
        return json.loads(raw) if raw else {}

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/time":
            return self._send(200, {"hub_ns": time.time_ns()})
        if path == "/api/state":
            return self._send(200, self.rig.snapshot())
        m = self.HAVE.match(path)
        if m:   # which chunks the hub already has (for resume)
            try:
                cdir = os.path.join(self.rig.take_dir(*m.groups()), "chunks")
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            have = sorted(int(f[:-5]) for f in os.listdir(cdir) if f.endswith(".json")) if os.path.isdir(cdir) else []
            return self._send(200, {"have": have})
        page = {"/": "index.html", "/index.html": "index.html", "/control": "control.html"}.get(path)
        if page:
            with open(os.path.join(HERE, page), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        self._send(404, {"error": "not found"})

    UPLOAD = re.compile(r"^/api/file/([A-Za-z0-9._-]{1,100})$")

    def _upload(self, name):
        """A whole file from the phone (e.g. a Google Camera reference clip), streamed to disk."""
        d = os.path.join(self.rig.data, "uploads")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, time.strftime("%Y%m%d-%H%M%S-") + name)
        n, left = 0, int(self.headers.get("Content-Length") or 0)
        with open(p + ".part", "wb") as f:
            while left > 0:
                b = self.rfile.read(min(1 << 20, left))
                if not b:
                    return self._send(400, {"error": "short upload"})
                f.write(b)
                left -= len(b)
                n += len(b)
        os.replace(p + ".part", p)
        log("uploaded %s (%.1f MB)" % (p, n / 1e6))
        self._send(200, {"ok": True, "bytes": n, "path": p})

    def do_PUT(self):
        u = self.UPLOAD.match(self.path)
        if u:
            return self._upload(u.group(1))
        m = self.CHUNK.match(self.path)
        if not m:
            return self._send(404, {"error": "not found"})
        take, phone, seq, ext = m.groups()
        try:
            cdir = os.path.join(self.rig.take_dir(take, phone), "chunks")
            body = self._raw()
            if ext == "json":
                json.loads(body)
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        os.makedirs(cdir, exist_ok=True)
        p = os.path.join(cdir, "%06d.%s" % (int(seq), ext))
        with open(p + ".part", "wb") as f:
            f.write(body)
        os.replace(p + ".part", p)     # .json goes last from the page: it marks the chunk complete
        self._send(200, {"ok": True, "bytes": len(body)})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            b = self._json()
            if path == "/api/poll":
                name = b.get("phone", "")
                if not NAME.match(name):
                    raise ValueError("phone name must be phone1..phone99")
                return self._send(200, self.rig.poll(name, b))
            if path == "/api/cmd":
                return self._send(200, self.rig.command(b.get("action"), b.get("phones"), b.get("take_id")))
            m = self.LOG.match(path)
            if m:
                if not NAME.match(m.group(1)):
                    raise ValueError("bad phone name")
                ldir = os.path.join(self.rig.data, "logs")
                os.makedirs(ldir, exist_ok=True)
                b["hub_ns"] = time.time_ns()
                with open(os.path.join(ldir, m.group(1) + ".jsonl"), "a") as f:
                    f.write(json.dumps(b) + "\n")
                return self._send(200, {"ok": True})
            m = self.FINISH.match(path)
            if m:
                d = self.rig.take_dir(*m.groups())
                os.makedirs(d, exist_ok=True)
                _write_json(os.path.join(d, "finish.json"), b)
                man = assemble(d, b)
                log("finished %s/%s: %d frames, %.1f s, %d dropped, base %s" % (
                    m.group(1), m.group(2), man["frames"], man.get("duration_s", 0),
                    man.get("dropped_frames", 0), man.get("timestamp_base")))
                return self._send(200, man)
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        self._send(404, {"error": "not found"})


class DualStackServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    request_queue_size = 64
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--data", default="/var/lib/hubd/phone-spike")
    ap.add_argument("--hub", default="http://localhost", help="hubd to follow; '' to disable")
    a = ap.parse_args()
    Handler.rig = rig = Rig(a.data)
    stop = threading.Event()
    if a.hub:
        threading.Thread(target=rig.follow_hub, args=(a.hub, stop), daemon=True).start()
    srv = DualStackServer(("::", a.port), Handler)
    log("phone spike on :%d, data %s, following %s" % (a.port, a.data, a.hub or "nothing"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
