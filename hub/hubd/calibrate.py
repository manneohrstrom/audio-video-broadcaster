"""Automatic calibration by showing the card (docs/phase4-plan.md, Track B, B3): Pi cameras.

While the rig is idle and a hub page is open, every online idle Pi camera takes an
auto-metered still about once a second. When the card is in one, the hub works out the
gain and colour gains that make the card's grey read TARGET and neutral, checks them with
a still at those settings (up to three rounds), and keeps them: they go out with every
arm from then on. The card readings (grey, white, colour patches) are kept for the editor.

Showing the card again recalibrates, but only after it has left the picture, so a card
left lying in view doesn't recalibrate over and over. Nothing happens during takes or LIVE.

Exposure maths in linear light: the JPEG is roughly sRGB, so level v (0-255) is about
(v/255)^2.2 in linear terms. Gain is a plain multiplier, so one step nearly lands.

White balance is not: the camera's colour matrix runs after the colour gains and mixes the
channels, so raising the red gain also lowers green. Measured on cam1 (2026-11-16):
doubling the red gain moved R/G by about 2^2.2. A plain "gain *= G/R" step then overshoots
about 2x and swings further each round (the first version did exactly that). So each
step is scaled by the measured sensitivity: starting at SENS, then estimated from the
previous round (secant), kept within SENS_RANGE."""
import json
import os
import threading
import time

import calib

TARGET = 119.0          # the grey's sRGB level after calibration (18 % grey)
GAMMA = 2.2
MAX_GAIN = 16.0         # IMX708 analogue gain limit
TOL_LEVEL = 0.08        # grey's brightness within ±8 % (linear)
TOL_WB = 0.04           # R/G and B/G within ±4 %
MIN_MARKER_PX = 30      # smaller: card too far away to read well
MAX_SPREAD = 14.0       # grey patch noise/texture above this: glare or a fold
ROUNDS = 5
SENS = 2.2              # d log(R/G) / d log(red gain), measured on cam1; same for blue
SENS_RANGE = (0.8, 4.0)
WATCH_S = 20            # watching stays on this long after a page last asked for status


def lin(v):
    return (max(float(v), 0.5) / 255.0) ** GAMMA


def ratios(grey):
    R, G, B = (lin(v) for v in grey)
    return R / G, B / G


def sensitivity(prev, cur, i):
    """Secant estimate of d log(ratio) / d log(gain) for channel i (0 red, 1 blue) from two
    rounds: (awb, grey) each. SENS if the gain hardly moved."""
    import math
    if not prev:
        return SENS
    dg = math.log(cur[0][i] / prev[0][i])
    if abs(dg) < 0.02:
        return SENS
    s = math.log(ratios(cur[1])[i] / ratios(prev[1])[i]) / dg
    return min(max(s, SENS_RANGE[0]), SENS_RANGE[1])


def next_settings(grey, gain, awb, sens=(SENS, SENS)):
    """Gain and colour gains that bring this grey reading to TARGET and neutral.
    grey: [r, g, b] read at total gain `gain` and colour gains `awb` = [red, blue].
    sens: how strongly R/G and B/G respond to the red and blue gains (log-log).
    Returns (gain, [red, blue], enough_light)."""
    R, G, B = (lin(v) for v in grey)
    want = gain * lin(TARGET) / G
    if max(grey) >= 250:            # clipped: the reading understates; step down hard
        want = gain * 0.5
    ok = want <= MAX_GAIN * 1.02
    g = min(max(want, 1.0), MAX_GAIN)
    r = min(max(awb[0] * (G / R) ** (1 / sens[0]), 0.3), 10.0)
    b = min(max(awb[1] * (G / B) ** (1 / sens[1]), 0.3), 10.0)
    return round(g, 3), [round(r, 3), round(b, 3)], ok


def good(grey, light_ok=True):
    R, G, B = (lin(v) for v in grey)
    wb = abs(R / G - 1) <= TOL_WB and abs(B / G - 1) <= TOL_WB
    level = abs(G / lin(TARGET) - 1) <= TOL_LEVEL
    return wb and (level or not light_ok)        # too dark at max gain: best we can do


def card_problem(m):
    if m.get("marker_px", 0) < MIN_MARKER_PX:
        return "bring the card closer"
    if m.get("grey_spread", 0) > MAX_SPREAD:
        return "tilt the card: a light is reflecting on it"
    return None


class Calibrator:
    def __init__(self, hub, clock=time.time):
        self.hub, self.clock = hub, clock
        self.lock = threading.Lock()
        self.path = os.path.join(hub.cfg["state_dir"], "calibration.json")
        try:
            with open(self.path) as f:
                self.records = json.load(f)
        except (OSError, ValueError):
            self.records = {}
        self.live = {}              # cam -> {"status", "message"} while watching
        self.in_view = {}           # cam -> card seen in the latest still
        self.busy = set()
        self.watch_until = 0.0
        self.seq = 0                # bumped on every new calibration (the page chimes)
        self.card = None

    # -- what the pages see
    def page_open(self):
        self.watch_until = self.clock() + WATCH_S

    def watching(self):
        return self.clock() < self.watch_until and self.hub.state == "idle"

    def status(self):
        with self.lock:
            out = {}
            for cam in self.hub.cfg["nodes"]:
                rec = self.records.get(cam)
                cur = self.live.get(cam) or {}
                st = cur.get("status") or ("calibrated" if rec else "waiting")
                out[cam] = {"status": st, "message": cur.get("message"),
                            "at": rec and rec["at"], "gain": rec and rec["gain"], "awb_gains": rec and rec["awb_gains"],
                            "light_ok": rec and rec["light_ok"]}
            return {"watching": self.watching(), "seq": self.seq, "devices": out}

    def _set(self, cam, status=None, message=None):
        with self.lock:
            self.live[cam] = {"status": status, "message": message}

    # -- the loop
    def tick(self):
        """Start a step for every camera that can take part and isn't busy. Non-blocking."""
        if not self.watching():
            return
        with self.hub.lock:
            ready = [n for n, st in self.hub.status.items() if st and st.get("mode") == "idle"]
        for cam in ready:
            with self.lock:
                if cam in self.busy:
                    continue
                self.busy.add(cam)
            self.hub.spawn(self._step_safely, cam)

    def _step_safely(self, cam):
        try:
            self.step(cam)
        except Exception as e:  # noqa: BLE001 - offline, rig busy, camera error: try again next tick
            self._set(cam, None, None if "rig is" in str(e) else "camera: %s" % e)
        finally:
            with self.lock:
                self.busy.discard(cam)

    def _measure(self, jpg):
        if self.card is None:
            self.card = calib.load_card(self.hub.cfg.get("card_json"))
        with self.hub.calib_lock:
            return calib.measure(jpg, self.card)

    def step(self, cam):
        jpg, meta = self.hub.still(cam, auto=True)
        m = self._measure(jpg)
        if not m.get("grey"):
            self.in_view[cam] = False
            self._set(cam)
            return
        if self.in_view.get(cam):
            return                      # still in view since it was calibrated: wait for it to leave
        bad = card_problem(m)
        if bad:
            self._set(cam, "waiting", bad)
            return
        self._set(cam, "calibrating", "hold the card still")
        gain = float(meta.get("AnalogueGain", 1.0)) * float(meta.get("DigitalGain", 1.0))
        awb = meta.get("ColourGains") or [1.5, 1.5]
        prev = (list(awb), m["grey"])
        g, wb, ok = next_settings(m["grey"], gain, awb)
        rounds = 0
        for rounds in range(1, ROUNDS + 1):
            jpg, _ = self.hub.still(cam, settings={"gain": g, "awb_gains": wb})
            m = self._measure(jpg)
            if not m.get("grey"):
                self._set(cam, "waiting", "the card moved out of view: hold it still")
                return
            if good(m["grey"], ok) or rounds == ROUNDS:
                break
            cur = (list(wb), m["grey"])
            sens = (sensitivity(prev, cur, 0), sensitivity(prev, cur, 1))
            prev = cur
            g, wb, ok = next_settings(m["grey"], g, wb, sens)
        self.save(cam, g, wb, ok, m, rounds, good(m["grey"], ok))
        self.in_view[cam] = True

    def save(self, cam, gain, awb, light_ok, m, rounds, converged):
        self.hub.set_camera_settings(cam, {"gain": gain, "awb_gains": awb})
        rec = {"at": self.clock(), "gain": gain, "awb_gains": awb, "light_ok": light_ok, "converged": converged,
               "rounds": rounds, "grey": m["grey"], "patches": m["patches"], "marker_px": m.get("marker_px")}
        with self.lock:
            self.records[cam] = rec
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.records, f, indent=1)
            os.replace(tmp, self.path)
            self.seq += 1
            msg = None if light_ok else "not enough light: at maximum gain"
            if not converged and light_ok:
                msg = "close, not exact: show the card again to retry"
            self.live[cam] = {"status": "calibrated", "message": msg}
