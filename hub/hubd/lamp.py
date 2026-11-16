"""Recording light (Phase 4 C1, docs/phase4-plan.md): a smart bulb that shows the rig state.

hubd owns it. A background thread works out the "look" from the rig state about once a
second and sends it to the bulb when it changes, and again every refresh_s anyway, so the
bulb catches up after a restart, a power cut or a Wi-Fi blip. Every request has a short
timeout and runs on that thread only: the bulb can never block or slow REC/STOP/LIVE.

Looks, highest priority first (draft colours from the plan; all can be overridden in config):
    error        flashing red   camera offline, REC refused, new dropped frames, drive full
    recording    red            also while stopping
    arming       amber          REC pressed, cameras starting
    live         blue
    ingest       pulsing cyan   hub-ingest is pulling/writing
    ingest_done  purple         ingest finished since the drive was plugged in: safe to eject
    ready        dim green

Bulb types (config "lamp.type"):
    tasmota   HTTP /cm?cmnd=...           (LocalBytes bulbs ship with Tasmota or ESPHome)
    esphome   REST of the web_server component: POST /light/<light_id>/turn_on?...
    log       no bulb: logs what it would send (dry run on the hub)
    null      off (default)

Standard library only.
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

DEFAULT_LAMP = {
    "type": None,            # "tasmota" | "esphome" | "log" | None
    "address": None,         # IP or host name; give it a DHCP reservation
    "light_id": None,        # esphome only: the light's id, as in http://<bulb>/ (e.g. "localbytes_bulb")
    "user": None,            # tasmota web password, if one is set (normally none)
    "password": None,
    "timeout_s": 1.0,
    "refresh_s": 30.0,       # resend the steady look this often
    "alert_s": 8.0,          # how long a one-off error (REC refused, new drops) flashes
    "drive_min_free_gb": 2.0,
    "startup_grace_s": 15.0,  # no "camera offline" until hubd has polled for this long
    # rgb 0-255, dim 0-100. "flash": on/off each period_s; "pulse": fade between dim and a quarter of it.
    "looks": {
        "off":         {"rgb": [0, 0, 0],       "dim": 0},
        "ready":       {"rgb": [0, 255, 0],     "dim": 10},
        "live":        {"rgb": [0, 0, 255],     "dim": 60},
        "arming":      {"rgb": [255, 120, 0],   "dim": 70},
        "recording":   {"rgb": [255, 0, 0],     "dim": 100},
        "ingest":      {"rgb": [0, 255, 255],   "dim": 40, "pulse": True, "period_s": 2.0},
        "ingest_done": {"rgb": [150, 0, 255],   "dim": 40},
        "error":       {"rgb": [255, 0, 0],     "dim": 100, "flash": True, "period_s": 1.0},
    },
}


def merge_config(user):
    cfg = json.loads(json.dumps(DEFAULT_LAMP))
    user = dict(user or {})
    looks = user.pop("looks", {})
    cfg.update(user)
    for name, look in looks.items():
        cfg["looks"][name] = dict(cfg["looks"].get(name, {}), **look)
    return cfg


# ------------------------------------------------------------------ deciding the look

class RigLight:
    """Turns rig inputs into a look name and a reason. Pure apart from the memory it
    needs: when ingest finished, the dropped-frame count, and one-off alerts."""

    def __init__(self, cfg, clock=time.monotonic):
        self.cfg, self.clock = cfg, clock
        self.started = clock()
        self.alert_until, self.alert_why = 0.0, None
        self.preview_until, self.preview_look = 0.0, None
        self.was_ingesting = False
        self.ingest_done = False
        self.take_id, self.dropped = None, 0

    def alert(self, why, seconds=None):
        self.alert_until = self.clock() + (seconds or self.cfg["alert_s"])
        self.alert_why = why

    def preview(self, look, seconds):
        self.preview_look, self.preview_until = look, self.clock() + seconds

    def decide(self, rig, ingest):
        """rig: {"state", "nodes": {name: status-or-None}, "take_id"}; ingest: hub-ingest's
        state.json (or {}). Returns (look, reason)."""
        now = self.clock()
        if now < self.preview_until:
            return self.preview_look, "preview"
        state = rig.get("state", "idle")
        nodes = rig.get("nodes") or {}
        status = (ingest or {}).get("status") or ""

        # Dropped frames during a take: flash for alert_s whenever the count goes up.
        if state == "recording":
            if rig.get("take_id") != self.take_id:
                self.take_id, self.dropped = rig.get("take_id"), 0
            d = sum(((st or {}).get("take") or {}).get("dropped_so_far") or 0 for st in nodes.values())
            if d > self.dropped:
                self.alert("%d dropped frame(s) so far" % d)
            self.dropped = max(self.dropped, d)

        # Ingest done: it was ingesting and is now idle with the drive still in.
        ingesting = status == "ingesting"
        no_drive = status.startswith("no ") or ingest.get("drive") == "ejected"
        if ingesting:
            self.ingest_done = False
        elif self.was_ingesting and status.startswith("idle"):
            self.ingest_done = True
        if no_drive or state != "idle":
            self.ingest_done = False
        self.was_ingesting = ingesting

        errors = []
        if now < self.alert_until:
            errors.append(self.alert_why)
        if now - self.started >= self.cfg["startup_grace_s"]:
            off = sorted(n for n, st in nodes.items() if st is None)
            if off:
                errors.append("offline: " + ", ".join(off))
        free = ingest.get("drive_free_gb")
        if not no_drive and free is not None and free < self.cfg["drive_min_free_gb"]:
            errors.append("STUDIO drive nearly full (%.1f GB)" % free)
        if "fsck" in status:
            errors.append("STUDIO drive needs repair")
        if errors:
            return "error", "; ".join(errors)

        if state in ("recording", "stopping"):
            return "recording", state
        if state == "starting":
            return "arming", state
        if any((st or {}).get("mode") in ("live", "live-starting") for st in nodes.values()):
            return "live", "live"
        if ingesting:
            return "ingest", "ingesting"
        if self.ingest_done:
            return "ingest_done", "ingest finished, safe to eject"
        return "ready", "ready"


def frame(look, now):
    """What the bulb should show at this moment for a look: (rgb, dim, fade_s).
    Flashing and pulsing alternate between two frames every period_s."""
    rgb, dim = list(look.get("rgb", [0, 0, 0])), int(look.get("dim", 0))
    period = float(look.get("period_s", 1.0))
    phase = int(now / period) % 2 if (look.get("flash") or look.get("pulse")) else 0
    if look.get("flash"):
        return rgb, (dim if phase == 0 else 0), 0.0
    if look.get("pulse"):
        return rgb, (dim if phase == 0 else max(1, dim // 4)), period
    return rgb, dim, 0.0


# ------------------------------------------------------------------ bulb drivers

def _open(req, timeout):
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class Tasmota:
    """Tasmota HTTP API. Backlog0 runs the commands without the 200 ms gap between them.
    Color2 sets the colour without touching the dimmer; Dimmer 0 switches the bulb off and
    any Dimmer > 0 switches it back on. Fade/Speed make the pulse smooth (Speed is in
    half-seconds: Speed 4 = about 2 s)."""

    def __init__(self, cfg, opener=_open):
        self.cfg, self.opener = cfg, opener

    def url(self, rgb, dim, fade_s):
        if fade_s > 0:
            fade = "Fade 1; Speed %d" % max(1, min(40, round(fade_s * 2)))
        else:
            fade = "Fade 0"
        cmd = "Backlog0 %s; Color2 %d,%d,%d; Dimmer %d" % ((fade,) + tuple(rgb) + (dim,))
        q = {"cmnd": cmd}
        if self.cfg.get("user"):
            q.update(user=self.cfg["user"], password=self.cfg.get("password") or "")
        return "http://%s/cm?%s" % (self.cfg["address"], urllib.parse.urlencode(q))

    def send(self, rgb, dim, fade_s):
        out = self.opener(self.url(rgb, dim, fade_s), self.cfg["timeout_s"])
        try:
            reply = json.loads(out or b"{}")
        except ValueError:
            raise OSError("not a Tasmota reply: %r" % out[:80])
        if "WARNING" in reply or reply.get("Command") == "Unknown":
            raise OSError("Tasmota: %s" % reply)


class ESPHome:
    """ESPHome web_server REST API (needs `web_server:` in the bulb's YAML; LocalBytes
    images normally have it). brightness is 0-255, transition in seconds."""

    def __init__(self, cfg, opener=_open):
        if not cfg.get("light_id"):
            raise ValueError("lamp.light_id is required for esphome (see http://<bulb>/)")
        self.cfg, self.opener = cfg, opener

    def request(self, rgb, dim, fade_s):
        base = "http://%s/light/%s/" % (self.cfg["address"], urllib.parse.quote(self.cfg["light_id"]))
        if dim <= 0:
            url = base + "turn_off?" + urllib.parse.urlencode({"transition": fade_s})
        else:
            url = base + "turn_on?" + urllib.parse.urlencode(
                {"r": rgb[0], "g": rgb[1], "b": rgb[2], "brightness": round(dim * 255 / 100),
                 "transition": fade_s})
        return urllib.request.Request(url, method="POST", data=b"")

    def send(self, rgb, dim, fade_s):
        self.opener(self.request(rgb, dim, fade_s), self.cfg["timeout_s"])


class LogOnly:
    def __init__(self, cfg, log):
        self.log = log

    def send(self, rgb, dim, fade_s):
        self.log("lamp (dry run): rgb %s dim %d fade %.1fs" % (rgb, dim, fade_s))


def make_driver(cfg, log, opener=_open):
    t = cfg.get("type")
    if t == "tasmota":
        return Tasmota(cfg, opener)
    if t == "esphome":
        return ESPHome(cfg, opener)
    if t == "log":
        return LogOnly(cfg, log)
    if t:
        raise ValueError("unknown lamp.type %r (tasmota, esphome, log)" % t)
    return None


# ------------------------------------------------------------------ the worker

class Lamp:
    """Runs RigLight + a driver on its own thread. inputs() returns (rig, ingest)."""

    def __init__(self, cfg, inputs, log, driver=None, clock=time.monotonic):
        self.cfg = merge_config(cfg)
        self.inputs, self.log, self.clock = inputs, log, clock
        self.driver = driver or make_driver(self.cfg, log)
        self.light = RigLight(self.cfg, clock=clock)
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.look, self.reason = None, None
        self.sent, self.sent_at = None, None
        self.error, self.fail_since = None, None

    @property
    def enabled(self):
        return self.driver is not None

    def poke(self):
        """Something changed (REC/STOP/LIVE): update now instead of within a second."""
        self.wake.set()

    def alert(self, why):
        with self.lock:
            self.light.alert(why)
        self.wake.set()

    def preview(self, look, seconds=5.0):
        if look not in self.cfg["looks"]:
            raise ValueError("unknown look %r; one of %s" % (look, ", ".join(sorted(self.cfg["looks"]))))
        with self.lock:
            self.light.preview(look, min(float(seconds), 60.0))
        self.wake.set()
        return self.status()

    def status(self):
        return {"enabled": self.enabled, "type": self.cfg.get("type"), "address": self.cfg.get("address"),
                "look": self.look, "reason": self.reason, "reachable": self.enabled and self.error is None,
                "error": self.error}

    def step(self):
        """One round: decide, and send if the frame changed or the refresh is due."""
        rig, ingest = self.inputs()
        with self.lock:
            look, reason = self.light.decide(rig, ingest or {})
        if look != self.look:
            self.log("lamp: %s (%s)" % (look, reason))
        self.look, self.reason = look, reason
        now = self.clock()
        fr = frame(self.cfg["looks"][look], now)
        refresh = self.sent_at is None or now - self.sent_at >= self.cfg["refresh_s"]
        retry = self.error is not None and now - self.sent_at >= 5.0
        if fr == self.sent and not refresh and not retry:
            return
        try:
            self.driver.send(*fr)
        except Exception as e:  # noqa: BLE001 - the bulb must never break hubd
            if self.error is None:
                self.log("lamp unreachable: %s" % e)
                self.fail_since = now
            self.error, self.sent_at = str(e), now
            return
        if self.error is not None:
            self.log("lamp reachable again after %.0f s" % (now - self.fail_since))
        self.error, self.sent, self.sent_at = None, fr, now

    def run(self, stop):
        if not self.enabled:
            return
        self.log("lamp: %s at %s" % (self.cfg["type"], self.cfg.get("address")))
        while not stop.is_set():
            try:
                self.step()
            except Exception as e:  # noqa: BLE001
                self.log("lamp: %s" % e)
            # Cheap when nothing changed (no request); fast enough for a 1 s flash.
            self.wake.wait(0.25)
            self.wake.clear()


def read_ingest_state(path, _cache={}):
    """hub-ingest's state.json, re-read only when it changes."""
    try:
        m = os.path.getmtime(path)
    except OSError:
        return {}
    if _cache.get("m") != m:
        try:
            with open(path) as f:
                _cache.update(m=m, st=json.load(f))
        except (OSError, ValueError):
            return _cache.get("st") or {}
    return _cache.get("st") or {}
