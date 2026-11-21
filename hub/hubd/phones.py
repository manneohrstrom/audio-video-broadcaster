"""Phone cameras for hubd (docs/phone-plan.md). Standard library only.

A phone is a browser page (/phone) that polls the hub about once a second with its
status and gets back what it should be doing. Phones are driven by reconciling
state on every poll, never by REC/STOP waiting on them:

- rig starting or recording take X, phone ready  -> "rec X"
- phone recording anything else (rig idle, other take) -> "stop"

So a phone that opens late, reloads, or loses Wi-Fi for a while catches up by
itself, and phones never block or slow REC/STOP.

Two pages, one phone: /phone (handheld, the full page) and /phone/tripod (a dark screen
for a phone left in a bracket, set up from the hub page: name, identify, reload).

Footage: the page uploads ~2 s chunks (H.264 Annex B + frame timestamps) while it
records, to <phones_dir>/<take>/<phone>/chunks/. At finish the chunks are joined
into the Pi camera layout (video.h264, video.pts, manifest.json) and deleted.
"""
import json
import os
import re
import shutil
import threading
import time

# A phone is known by a device id it makes once and keeps in the browser ("phone-k7q2"),
# like a Pi camera by its hostname (user, 2026-11-21): files are phone-k7q2.mov, and the
# name people see is given on the hub page (names.py); until then it's the id. A rejoin
# mid-take records a new part: phone-k7q2-part2, -part3 …
DEVICE = re.compile(r"^phone-[a-z0-9]{4,8}$")
PART = re.compile(r"^(?!cam\d+$)[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?(-part\d{1,2})?$")
MODES = ("handheld", "tripod")
IDENTIFY_S = 6.0               # "identify" on the hub page flashes the phone's screen this long
TAKE = re.compile(r"^\d{8}-\d{6}$")
PAGE = re.compile(r"^[A-Za-z0-9]{8,40}$")
NOMINAL_MS = 1000 / 30.0
MAX_CHUNK = 64 << 20           # ~2 s at 16 Mbps is ~4 MB; anything this big is a mistake
BYTES_PER_HOUR = 7.2e9         # 16 Mbps cap
SNAP_WINDOW_S = 10.0           # a snapshot request stays open this long


class PhoneError(ValueError):
    pass


def median(v):
    s = sorted(v)
    return s[len(s) // 2] if s else None


def fit_clock(samples):
    """Map the page clock (performance.now(), ms) to hub wall clock (ns).

    samples: [{"perf_ms": ping midpoint, "hub_ns": hub time, "rtt_ms": round trip}].
    Uses the lowest-RTT half. Fits offset, and rate (drift) when the samples span
    more than a minute. Returns (fn, info)."""
    good = sorted((s for s in samples or [] if s.get("rtt_ms") is not None and s.get("hub_ns")),
                  key=lambda s: s["rtt_ms"])
    if not good:
        raise PhoneError("no clock samples")
    good = good[:max(1, (len(good) + 1) // 2)]
    xs = [float(s["perf_ms"]) for s in good]
    ref = int(good[0]["hub_ns"])             # work relative to one sample: hub ns don't fit a float exactly
    ys = [(int(s["hub_ns"]) - ref) / 1e6 - float(s["perf_ms"]) for s in good]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 60000 ** 2 else 0.0

    def fn(perf_ms):
        return ref + int(round((perf_ms + my + slope * (perf_ms - mx)) * 1e6))
    resid = [y - (my + slope * (x - mx)) for x, y in zip(xs, ys)]
    return fn, {"samples": len(samples), "used": n, "drift_ppm": round(slope * 1e6, 2),
                "best_rtt_ms": round(good[0]["rtt_ms"], 2), "max_rtt_used_ms": round(good[-1]["rtt_ms"], 2),
                "resid_ms_max": round(max(abs(r) for r in resid), 3)}


def find_gaps(pts_ms, nominal=NOMINAL_MS):
    """Same rule as camnode: a frame interval over 1.5 frames is a gap."""
    gaps = []
    for i in range(1, len(pts_ms)):
        d = pts_ms[i] - pts_ms[i - 1]
        if d > 1.5 * nominal:
            gaps.append({"after_frame": i - 1, "at_s": round(pts_ms[i - 1] / 1000, 3),
                         "gap_ms": round(d, 3), "missing": int(round(d / nominal)) - 1})
    return gaps


def write_json(path, obj):
    with open(path + ".part", "w") as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(path + ".part", path)


def capture_delay_ms(table, model):
    """The phone's fixed capture->page delay (spike: ~40 ms on a Pixel 3)."""
    if model and model in table:
        return float(table[model])
    return float(table.get("default", 0))


def assemble(d, meta, delay_table):
    """Join d/chunks into video.h264 + video.pts + manifest.json.

    Each chunk is <seq>.h264 plus <seq>.json ({"frames": [{"ts_us", "arr_ms", "key"}]}),
    the .json written last. If chunks are missing (still on the phone), only the
    manifest is written, with complete=false, and the chunks are kept so the phone
    can send the rest and finish again. When complete, the chunks are deleted."""
    cdir = os.path.join(d, "chunks")
    seqs = sorted(int(f[:-5]) for f in os.listdir(cdir) if f.endswith(".json")) if os.path.isdir(cdir) else []
    last = meta.get("chunks")
    want = range(last) if isinstance(last, int) and last >= 0 else range(seqs[-1] + 1 if seqs else 0)
    have = set(seqs)
    missing = [s for s in want if s not in have]
    model = (meta.get("info") or {}).get("model")
    man = {"schema": 1, "camera": meta.get("phone"), "label": meta.get("label"), "take_id": meta.get("take_id"), "source": "phone",
           "phone_model": model, "complete": not missing, "chunks": len(seqs), "missing_chunks": missing,
           "settings": meta.get("settings"), "encoder": meta.get("encoder"), "phone": meta.get("info"),
           "page_stats": meta.get("stats"), "finished_at": time.time()}
    if missing:
        man.update(frames=None, first_frame_wallclock_ns=None, gaps=[], dropped_frames=None)
        write_json(os.path.join(d, "manifest.json"), man)
        return man

    frames = []
    tmp = os.path.join(d, "video.h264.part")
    with open(tmp, "wb") as out:
        for s in seqs:
            with open(os.path.join(cdir, "%06d.json" % s)) as f:
                frames += json.load(f)["frames"]
            with open(os.path.join(cdir, "%06d.h264" % s), "rb") as f:
                shutil.copyfileobj(f, out, 1 << 20)
        out.flush()
        os.fsync(out.fileno())
    os.replace(tmp, os.path.join(d, "video.h264"))
    if not frames:
        man.update(frames=0, first_frame_wallclock_ns=None, duration_s=0, gaps=[], dropped_frames=0)
        write_json(os.path.join(d, "manifest.json"), man)
        shutil.rmtree(cdir, ignore_errors=True)
        return man

    ts0 = frames[0]["ts_us"]
    pts = [(f["ts_us"] - ts0) / 1000.0 for f in frames]
    with open(os.path.join(d, "video.pts.part"), "w") as f:
        f.write("# timecode format v2\n")
        for t in pts:
            f.write("%.3f\n" % t)
    os.replace(os.path.join(d, "video.pts.part"), os.path.join(d, "video.pts"))

    # VideoFrame.timestamp is on the camera's own clock with a constant offset to
    # performance.now() (spike). Its earliest arrival is the capture time plus the
    # page's delivery delay; the fixed capture->page delay is corrected per model.
    lat = [f["arr_ms"] - f["ts_us"] / 1000.0 for f in frames if f.get("arr_ms") is not None]
    if not lat:
        raise PhoneError("frames have no arrival times")
    clock_fn, clock_info = fit_clock(meta.get("clock"))
    delay = capture_delay_ms(delay_table, model)
    first_perf = ts0 / 1000.0 + min(lat)
    raw_ns = clock_fn(first_perf)
    gaps = find_gaps(pts)
    dur = pts[-1] / 1000.0
    man.update({
        "first_frame_wallclock_ns": raw_ns - int(delay * 1e6),
        "first_frame_wallclock_raw_ns": raw_ns,
        "capture_delay_ms": delay,
        "last_frame_wallclock_ns": raw_ns - int(delay * 1e6) + int(pts[-1] * 1e6),
        "delivery_delay_ms": {"median": round(median(lat) - min(lat), 2), "max": round(max(lat) - min(lat), 2)},
        "clock_fit": clock_info,
        "frames": len(frames),
        "duration_s": round(dur, 3),
        "fps_measured": round((len(pts) - 1) / dur, 4) if dur > 0 else None,
        "keyframes": sum(1 for f in frames if f.get("key")),
        "gaps": gaps,
        "dropped_frames": sum(g["missing"] for g in gaps),
        "pts_source": "webcodecs",
        "exit_code": 0,
        "files": {n: os.path.getsize(os.path.join(d, n)) for n in ("video.h264", "video.pts")},
    })
    write_json(os.path.join(d, "manifest.json"), man)
    shutil.rmtree(cdir, ignore_errors=True)
    return man


class Phones:
    def __init__(self, cfg, clock=time.time):
        self.dir = cfg.get("phones_dir") or os.path.join(cfg["state_dir"], "phones")
        self.seen_s = cfg.get("phone_seen_s", 6.0)
        self.delay = cfg.get("phone_capture_delay_ms", {"default": 40})
        self.min_hours = cfg["checks"]["min_free_hours"]
        self.clock = clock
        self.lock = threading.Lock()
        self.phones = {}        # device id -> {"page", "seen", "status", "cmd", "mode"}
        self.cmd_id = 0
        self.recorded = {}      # take_id -> set of phone names sent "rec"
        self.uploaded = {}      # take_id -> video bytes received (record-time estimate)
        self.snaps = {}         # name -> (jpeg, time): latest still for the snapshot page
        self.snap_wanted = 0.0  # phones send a still on their next poll until then
        self.calib_watch = lambda: False    # set by hubd: is the hub watching for the card?
        self.given_name = lambda name: None  # set by hubd: the name given on the hub page
        self.mounts_path = os.path.join(self.dir, "mounts.json")
        try:
            with open(self.mounts_path) as f:
                self.mounts = json.load(f)  # name -> "tripod" | "handheld"
        except (OSError, ValueError):
            self.mounts = {}
        self.identify_until = {}    # device id -> time: flash its screen until then
        os.makedirs(self.dir, exist_ok=True)

    # -- registry
    def _online(self, p, now):
        return now - p["seen"] < self.seen_s

    def poll(self, body, rig):
        """One poll from a phone page. rig: {"state", "take_id"} from the hub.
        Returns {"cmd": {...} or None, "rig": rig, "hub_ns": now}."""
        name, page = body.get("device") or "", body.get("page", "")
        if not DEVICE.match(name):
            raise PhoneError("bad device id: reload the phone page")
        if not PAGE.match(page):
            raise PhoneError("bad page id")
        mode = body.get("mode") if body.get("mode") in MODES else "handheld"
        now = self.clock()
        with self.lock:
            p = self.phones.get(name)
            if p and p["page"] != page and self._online(p, now) and not p.get("released"):
                raise PhoneError("this phone's page is already open in another tab: close one")
            if not p or p["page"] != page:
                p = self.phones[name] = {"page": page, "cmd": None}
                if mode == "tripod" and self.mounts.get(name) != "tripod":   # a tripod page means focus locked
                    self.mounts[name] = "tripod"
                    write_json(self.mounts_path, self.mounts)
            p["seen"], p["status"], p["mode"] = now, body, mode
            if p["cmd"] and body.get("done_cmd") == p["cmd"]["id"]:
                p["cmd"] = None
            want = self._wanted(name, body, rig)
            if want and (not p["cmd"] or (p["cmd"]["action"], p["cmd"]["take_id"]) != want):
                self.cmd_id += 1
                p["cmd"] = {"id": self.cmd_id, "action": want[0], "take_id": want[1]}
                if want[0] == "rec":
                    p["cmd"]["part"] = self._new_part(want[1], name)
                    self.recorded.setdefault(want[1], set()).add(name)
            elif not want and p["cmd"] and p["cmd"]["action"] == "rec" and body.get("state") != "ready":
                p["cmd"] = None     # stale: the phone can't act on it any more
            want_snap = (now < self.snap_wanted and rig.get("state") == "idle"
                         and self.snaps.get(name, (None, 0))[1] < self.snap_wanted - SNAP_WINDOW_S)
            reload = bool(p.pop("reload", False))
            if reload:
                p["released"] = True        # its next page may take over at once
            return {"id": name, "cmd": p["cmd"], "rig": rig, "hub_ns": time.time_ns(), "snap": want_snap,
                    "mount": self.mounts.get(name, "tripod"),
                    "identify": now < self.identify_until.get(name, 0), "reload": reload,
                    "calib_watch": rig.get("state") == "idle" and self.calib_watch()}

    @staticmethod
    def me(name):
        """For a page that hasn't started its camera (hubd adds the name given on the hub page)."""
        if not DEVICE.match(name or ""):
            raise PhoneError("bad device id")
        return {"id": name}

    def bye(self, body):
        """The page is going away (reload, closed): its next page may take over at once."""
        name, page = body.get("device") or "", body.get("page", "")
        with self.lock:
            p = self.phones.get(name)
            if p and p["page"] == page:
                p["released"] = True
        return {"ok": True}

    def identify(self, name):
        """Flash the phone's screen with its name for a few seconds (which one is it?)."""
        with self.lock:
            if name not in self.phones:
                raise PhoneError("no such phone")
            self.identify_until[name] = self.clock() + IDENTIFY_S
        return {"ok": True, "seconds": IDENTIFY_S}

    def reload(self, name):
        """Reload the phone's page on its next poll: only while it's ready with nothing to
        upload, so a take is never cut short. The tripod page starts its camera by itself."""
        now = self.clock()
        with self.lock:
            p = self.phones.get(name)
            if not p or not self._online(p, now):
                raise PhoneError("that phone isn't connected")
            st = p["status"] or {}
            if st.get("state") not in ("ready", "off") or st.get("queue"):
                raise PhoneError("not now: the phone is %s" % (st.get("state") if st.get("state") not in ("ready", "off") else "still uploading"))
            p["reload"] = True
        return {"ok": True}

    def names(self):
        """Phones seen in the last two minutes (the hub page lists them)."""
        now = self.clock()
        with self.lock:
            return sorted(n for n, p in self.phones.items() if now - p["seen"] < 120)

    def set_mount(self, name, mount):
        """Tripod (focus measured once, then locked) or handheld (continuous autofocus)."""
        if not PART.match(name or "") or mount not in ("tripod", "handheld"):
            raise PhoneError("mount must be tripod or handheld")
        with self.lock:
            self.mounts[name] = mount
            write_json(self.mounts_path, self.mounts)
            return dict(self.mounts)

    # -- snapshot page (Track B)
    def want_snaps(self):
        """Ask every phone for a fresh still on its next poll (for SNAP_WINDOW_S)."""
        with self.lock:
            self.snap_wanted = self.clock() + SNAP_WINDOW_S

    def put_snap(self, name, jpeg):
        if not PART.match(name or "") or not jpeg.startswith(b"\xff\xd8"):
            raise PhoneError("bad phone or not a JPEG")
        with self.lock:
            self.snaps[name] = (jpeg, self.clock())
        return {"ok": True}

    def get_snap(self, name):
        with self.lock:
            return self.snaps.get(name)

    def _new_part(self, take_id, name):
        """Folder for a new recording of this phone in this take: phone1, then
        phone1-2, phone1-3 … if it rejoins (page reload, Wi-Fi drop) mid-take.
        Counted from disk so a hubd restart can't reuse a folder."""
        td = os.path.join(self.dir, take_id)
        os.makedirs(td, exist_ok=True)
        n = sum(1 for d in os.listdir(td) if d == name or re.match(re.escape(name) + r"-part\d+$", d))
        part = name if n == 0 else "%s-part%d" % (name, n + 1)
        os.makedirs(os.path.join(td, part))
        return part

    @staticmethod
    def _wanted(name, st, rig):
        """(action, take_id) the phone should be told, or None if it's where it should be."""
        rig_take = rig.get("take_id") if rig.get("state") in ("starting", "recording") else None
        mine = st.get("take") if st.get("state") == "recording" else None
        if rig_take and st.get("state") == "ready":
            return ("rec", rig_take)
        if mine and mine != rig_take:
            return ("stop", mine)
        return None

    def ready(self):
        """Phones that are online and would record on the next REC."""
        now = self.clock()
        with self.lock:
            return sorted(n for n, p in self.phones.items()
                          if self._online(p, now) and (p["status"] or {}).get("state") == "ready")

    def phones_in(self, take_id):
        with self.lock:
            return sorted(self.recorded.get(take_id, ()))

    def snapshot(self, free_gb):
        now = self.clock()
        with self.lock:
            live = {n: p for n, p in self.phones.items() if now - p["seen"] < 120}
            n_on = sum(1 for p in live.values() if self._online(p, now)) or 1
            out = {}
            for n, p in sorted(live.items()):
                online = self._online(p, now)
                st = p["status"] or {}
                out[n] = {"label": n, "mode": p.get("mode"),
                          "online": online, "age_s": round(now - p["seen"], 1), "status": st,
                          "mount": self.mounts.get(n, "tripod"),
                          "problems": self.problems(n, st, free_gb, n_on) if online else []}
            return out

    def problems(self, name, st, free_gb, n_phones):
        """Warnings only: a phone never blocks REC."""
        p = []
        name = self.given_name(name) or name
        if st.get("state") not in ("ready", "recording"):
            p.append("%s camera not started" % name)
        if st.get("charging") is False:
            p.append("%s not charging (battery %s%%)" % (name, st.get("battery")))
        if st.get("visible") not in (None, "visible"):
            p.append("%s page is not in front: it may stop recording" % name)
        if st.get("state") == "ready" and not st.get("locked"):
            p.append("%s not calibrated: show it the card (otherwise it locks itself at REC)" % name)
        rtt = st.get("rtt_ms")
        if rtt is not None and rtt > 60:
            p.append("%s Wi-Fi slow (clock round trip %d ms)" % (name, rtt))
        if st.get("err"):
            p.append("%s: %s" % (name, st["err"]))
        if free_gb is not None:
            hours = free_gb * 1e9 / (n_phones * BYTES_PER_HOUR)
            if hours < self.min_hours:
                p.append("hub has room for %.1f h of %d phone%s" % (hours, n_phones, "s" if n_phones > 1 else ""))
        sf = st.get("storage_free_gb")
        if sf is not None and sf * 1e9 / BYTES_PER_HOUR < self.min_hours:
            p.append("%s phone storage low (%.1f GB)" % (name, sf))
        return p

    # -- storage
    def take_dir(self, take_id, phone):
        if not TAKE.match(take_id or "") or not PART.match(phone or ""):
            raise PhoneError("bad take or phone name")
        return os.path.join(self.dir, take_id, phone)

    def put_chunk(self, take_id, phone, seq, ext, read, length):
        """Store one chunk file. read(n) reads the request body. Idempotent."""
        if ext not in ("h264", "json") or not 0 <= seq < 1_000_000:
            raise PhoneError("bad chunk")
        if length > MAX_CHUNK:
            raise PhoneError("chunk too large")
        d = self.take_dir(take_id, phone)
        if os.path.exists(os.path.join(d, "manifest.json")) and not os.path.isdir(os.path.join(d, "chunks")):
            return {"ok": True, "already_complete": True}
        cdir = os.path.join(d, "chunks")
        os.makedirs(cdir, exist_ok=True)
        p = os.path.join(cdir, "%06d.%s" % (seq, ext))
        left, buf = length, []
        with open(p + ".part", "wb") as f:
            while left > 0:
                b = read(min(1 << 20, left))
                if not b:
                    raise PhoneError("short upload")
                f.write(b)
                if ext == "json":
                    buf.append(b)
                left -= len(b)
        if ext == "json":
            try:
                json.loads(b"".join(buf))
            except ValueError:
                os.remove(p + ".part")
                raise PhoneError("chunk metadata is not JSON") from None
        os.replace(p + ".part", p)      # the page sends .json after .h264: it marks the chunk complete
        if ext == "h264":
            with self.lock:
                self.uploaded[take_id] = self.uploaded.get(take_id, 0) + length
        return {"ok": True, "bytes": length}

    def have(self, take_id, phone):
        d = self.take_dir(take_id, phone)
        cdir = os.path.join(d, "chunks")
        man = self.manifest(take_id, phone)
        have = sorted(int(f[:-5]) for f in os.listdir(cdir) if f.endswith(".json")) if os.path.isdir(cdir) else []
        return {"have": have, "complete": bool(man and man.get("complete"))}

    def finish(self, take_id, phone, meta):
        """phone is the part folder (phone1, phone1-2 …)."""
        d = self.take_dir(take_id, phone)
        old = self.manifest(take_id, phone)
        if old and old.get("complete"):
            return old
        os.makedirs(d, exist_ok=True)
        meta = dict(meta, phone=phone, take_id=take_id)
        write_json(os.path.join(d, "finish.json"), meta)
        return assemble(d, meta, self.delay)

    def manifest(self, take_id, phone):
        try:
            with open(os.path.join(self.take_dir(take_id, phone), "manifest.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def manifests(self, take_id):
        """{phone: manifest or None (still uploading)} for every phone with files in the take."""
        d = os.path.join(self.dir, take_id) if TAKE.match(take_id or "") else None
        if not d or not os.path.isdir(d):
            return {}
        return {n: self.manifest(take_id, n) for n in sorted(os.listdir(d)) if PART.match(n)}

    def log(self, phone, body):
        if not PART.match(phone or ""):
            raise PhoneError("bad phone id")
        ldir = os.path.join(self.dir, "logs")
        os.makedirs(ldir, exist_ok=True)
        body = dict(body, hub_ns=time.time_ns())
        with open(os.path.join(ldir, phone + ".jsonl"), "a") as f:
            f.write(json.dumps(body)[:20000] + "\n")
