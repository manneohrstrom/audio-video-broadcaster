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
previous round (secant), kept within SENS_RANGE.

Warm light stays warm (user, 2026-11-20: "the incandescent light comes out almost bluish";
the Pixel 9a keeps it warm). Making the grey exactly neutral corrects all of a light's
colour, and the studio's amber bulbs are about 2200 K, warmer than the camera's tuning even
goes (2750 K). Eyes, and phone cameras, adapt only part of the way, so under warm light
the grey is aimed a little warm instead (warm_target): how much depends on how warm the
light is, read from the colour gains that would make the grey neutral, through the camera's
own colour-temperature curve (CT_CURVE). Anchored on the Pixel 9a's reading of the same
card under the same lamps (2026-11-18: R/G 1.12, B/G 0.76 in linear light), with a little
more red by the user's choice (ANCHOR_RG). From 5000 K up the grey stays neutral, as before.

Then the highlights: if more than CLIP_MAX of the whole picture is blown out at the card's
brightness, the gain comes down until it isn't (protect_highlights)."""
import json
import math
import os
import threading
import time

import calib

TARGET = 119.0          # the grey's sRGB level after calibration (18 % grey)
GAMMA = 2.2
# The IMX708's analogue gain stops at 16; above that the camera makes up the rest with
# digital gain (checked on cam4, 2026-11-19: gain 32 gave analogue 16 x digital 2.0, and a
# brighter picture). Up to 2x digital for the dim studio (user, 2026-11-19): more noise,
# and lamps in the picture clip sooner, so no further.
MAX_GAIN = 32.0
# At maximum gain, a grey still within a stop of TARGET (linear) counts as enough light:
# no warning. Darker than that: "not enough light".
DARK_OK = 0.5
MAX_STEP = 2.0          # gain changes at most this factor per round
TOL_LEVEL = 0.08        # grey's brightness within ±8 % (linear)
TOL_WB = 0.04           # R/G and B/G within ±4 %
MIN_MARKER_PX = 30      # smaller: card too far away to read well
MAX_SPREAD = 14.0       # grey patch noise/texture above this: glare or a fold
ROUNDS = 5
# Highlights (user, 2026-11-20): the card sets the brightness wherever it's held, so a card in
# a dark corner brightened the whole room a stop (cam1's stills, as calib.measure counts them:
# gain 32, 8.5 % blown out; gain 17, 3.5-4.1 %). After the colours, the gain comes down
# until no more than this share of the whole picture is blown out (about gain 17's), in
# steps of CLIP_STEP, at most CLIP_ROUNDS stills.
CLIP_MAX = 0.04
CLIP_STEP = 0.75
CLIP_ROUNDS = 4
# The odd one out (user, 2026-11-20): cam4's card, by the door, caught daylight (2900 K
# against the others' ~2100 K) and its picture of the lamp-lit room came out deep yellow.
# The Pis are one model, so the same colour gains give the same colour under the same light:
# a camera whose light is more than OUTLIER_MIRED from the others' (while they agree among
# themselves) takes their median colour gains and keeps its own brightness (reconcile).
# Only calibrations within SESSION_S of the newest count.
OUTLIER_MIRED = 60.0
SESSION_S = 6 * 3600
SENS = 2.2              # d log(R/G) / d log(red gain), measured on cam1; same for blue
SENS_RANGE = (0.8, 4.0)
WATCH_S = 20            # watching stays on this long after a page last asked for status
# How often a camera looks for the card. Every look starts and stops the camera; after ~590
# of them on 2026-11-16, cam2's driver froze (needed a reboot). So: often while setting up
# (just after a page opens) and for cameras not yet calibrated, rarely otherwise.
EVERY_S = 3.0
EVERY_SETTLED_S = 15.0
SETUP_S = 300
# Looking often again for a while after a calibration and after the card leaves the
# picture, so the card can be shown again straight away to recalibrate (user 2026-11-17:
# at every 15 s the card had to be away ~15 s and back ~15 s before it was picked up).
AGAIN_S = 120


def lin(v):
    return (max(float(v), 0.5) / 255.0) ** GAMMA


# The IMX708 Wide's colour-temperature curve, from libcamera's tuning file
# (/usr/share/libcamera/ipa/rpi/vc4/imx708_wide.json, "rpi.awb" "ct_curve", libcamera 0.8):
# for each colour temperature, the red and blue colour gains that make a grey neutral.
# (The file stores their inverses, the raw R/G and B/G.)
CT_CURVE = [(2750.0, 1.269, 3.510), (2940.0, 1.323, 3.223), (3650.0, 1.590, 2.378),
            (4625.0, 1.874, 1.938), (5715.0, 2.142, 1.695)]
NEUTRAL_K = 5000.0      # this warm or cooler: the grey is made neutral
ANCHOR_K = 2200.0       # the studio's bulbs (cam1's calibration of 2026-11-19, through CT_CURVE)...
# ...where the Pixel 9a kept the grey this warm (linear R/G 1.12, B/G 0.76), plus a little red:
# the user's pick from test stills on 2026-11-20 (red gain +4 %, about +9 % R/G through the
# colour matrix): amber rather than yellow, closer to the Pixel's rosy look of the room.
ANCHOR_RG, ANCHOR_BG = 1.22, 0.76
WARMTH = 1.0            # 1: like the Pixel 9a; 0: neutral (the old behaviour); more: warmer
MAX_WARM = 1.25         # warmer light than the anchor keeps at most this much more


def light_kelvin(neutral_awb):
    """The light's colour temperature from the colour gains that make the grey neutral,
    through CT_CURVE (in mired, log gain; extended in a straight line past its ends, as the
    studio's bulbs are warmer than its warmest point). Red and blue each give an estimate;
    the result is their mean, as libcamera does."""
    estimates = []
    for i in (0, 1):
        gains = [math.log(point[1 + i]) for point in CT_CURVE]     # monotonic along the curve
        mireds = [1e6 / point[0] for point in CT_CURVE]
        x = math.log(neutral_awb[i])
        rising = gains[-1] > gains[0]
        if (x < gains[0]) == rising:
            j = 0                                                  # past the warm end
        elif (x > gains[-1]) == rising:
            j = len(gains) - 2                                     # past the cool end
        else:
            j = next(k for k in range(len(gains) - 1) if min(gains[k], gains[k + 1]) <= x <= max(gains[k], gains[k + 1]))
        estimates.append(mireds[j] + (x - gains[j]) * (mireds[j + 1] - mireds[j]) / (gains[j + 1] - gains[j]))
    mired = sum(estimates) / len(estimates)
    return min(max(1e6 / max(mired, 1.0), 1500.0), 12000.0)


def warmth(kelvin):
    """How much of the anchor's warmth to keep under light of this colour temperature:
    0 at NEUTRAL_K and above, 1 at ANCHOR_K, in proportion in mired, times WARMTH."""
    w = (1e6 / kelvin - 1e6 / NEUTRAL_K) / (1e6 / ANCHOR_K - 1e6 / NEUTRAL_K)
    return min(max(w, 0.0), MAX_WARM) * WARMTH


def neutral_gains(grey, awb):
    """The colour gains that would make this grey neutral (one step's estimate, with the
    usual sensitivity: near the target, the step is small and the estimate close)."""
    R, G, B = (lin(v) for v in grey)
    return [awb[0] * (G / R) ** (1 / SENS), awb[1] * (G / B) ** (1 / SENS)]


def warm_target(grey, awb):
    """What the grey should read (linear R/G and B/G) under the light it was read in, and
    that light's colour temperature: (target_rg, target_bg, kelvin). Depends only on the
    reading and its colour gains, so it holds still while the loop converges on it."""
    kelvin = light_kelvin(neutral_gains(grey, awb))
    w = warmth(kelvin)
    return ANCHOR_RG ** w, ANCHOR_BG ** w, kelvin


# The card's white is printed at 243 and its grey at 119: in linear light white is about
# 4.8 times as bright. Much less than that and the light falls unevenly across the card
# (the white patch is in its lower right corner, the grey in the top middle). The grey,
# which the lock uses, is still right then, but the colour readings kept for the editor
# aren't: on 2026-11-16 cam3 read white darker than grey, the whole right side of the card
# dark (2026-11-17 check of the calibration data).
WHITE_OVER_GREY_MIN = 2.0       # phones compress highlights more than the Pis: room for that
UNEVEN_CARD = "the card's right side reads dark: light it evenly and show it again (colour readings unreliable)"


def luminance(rgb):
    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def card_lit_evenly(readings):
    """False if the white patch reads too dark next to the grey; None if it wasn't read."""
    white, grey = (readings.get("patches") or {}).get("white"), readings.get("grey")
    if not white or not grey:
        return None
    return luminance(white) >= WHITE_OVER_GREY_MIN * luminance(grey)


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


def next_settings(grey, gain, awb, sens=(SENS, SENS), target=(1.0, 1.0)):
    """Gain and colour gains that bring this grey reading to TARGET brightness and the
    target colour (linear R/G, B/G: warm_target; (1, 1) is neutral).
    grey: [r, g, b] read at total gain `gain` and colour gains `awb` = [red, blue].
    sens: how strongly R/G and B/G respond to the red and blue gains (log-log).
    Returns (gain, [red, blue], enough_light)."""
    R, G, B = (lin(v) for v in grey)
    want = gain * lin(TARGET) / G
    if max(grey) >= 250:            # clipped: the reading understates; step down hard
        want = gain * 0.5
    # At most a stop per round: a channel reading near zero (colour gains far off) asks for
    # a huge step, which overshoots once the colour gains move too.
    g = min(max(want, 1.0, gain / MAX_STEP), MAX_GAIN, gain * MAX_STEP)
    ok = want <= MAX_GAIN * 1.02 or g < MAX_GAIN       # not at the limit yet: keep going
    r = min(max(awb[0] * (target[0] * G / R) ** (1 / sens[0]), 0.3), 10.0)
    b = min(max(awb[1] * (target[1] * G / B) ** (1 / sens[1]), 0.3), 10.0)
    return round(g, 3), [round(r, 3), round(b, 3)], ok


def good(grey, light_ok=True, target=(1.0, 1.0)):
    R, G, B = (lin(v) for v in grey)
    wb = abs(R / G / target[0] - 1) <= TOL_WB and abs(B / G / target[1] - 1) <= TOL_WB
    level = abs(G / lin(TARGET) - 1) <= TOL_LEVEL
    return wb and (level or not light_ok)        # too dark at max gain: best we can do


def light_level(grey):
    """The grey's brightness as a share of TARGET, in linear light (1.0 = on target)."""
    return lin(grey[1]) / lin(TARGET)


def card_problem(m):
    if m.get("marker_px", 0) < MIN_MARKER_PX:
        return "bring the card closer"
    if m.get("grey_spread", 0) > MAX_SPREAD:
        return "tilt the card: a light is reflecting on it"
    return None


def release_freed_memory():
    """Give memory freed by OpenCV (full-size stills, every few seconds while a page is open)
    back to the system. Without this hubd kept it and grew past 1 GB (2026-11-16), which
    starved the renders on the 1 GB hub. glibc only; a no-op elsewhere."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass


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
        self.watch_since = 0.0
        self.last_look = {}         # cam -> time of its last look for the card
        self.often_until = {}       # cam -> look every EVERY_S until then (see AGAIN_S)
        self.seq = 0                # bumped on every new calibration (the page chimes)
        self.card = None

    # -- what the pages see
    def page_open(self):
        now = self.clock()
        if now >= self.watch_until:
            self.watch_since = now          # a new setup session
        self.watch_until = now + WATCH_S

    def due(self, cam):
        now = self.clock()
        settled = (cam in self.records and now - self.watch_since > SETUP_S
                   and now >= self.often_until.get(cam, 0))
        return now - self.last_look.get(cam, 0) >= (EVERY_SETTLED_S if settled else EVERY_S)

    def watching(self):
        return self.clock() < self.watch_until and self.hub.state == "idle"

    def status(self):
        with self.lock:
            out = {}
            for cam in self.hub.cfg["nodes"]:
                rec = self.records.get(cam)
                cur = self.live.get(cam) or {}
                st = cur.get("status") or ("calibrated" if rec else "waiting")
                out[cam] = {"status": st, "message": cur.get("message") or self.record_note(rec),
                            "at": rec and rec["at"], "gain": rec and rec["gain"], "awb_gains": rec and rec["awb_gains"],
                            "light_ok": rec and rec["light_ok"], "light_k": rec and rec.get("light_k"),
                            "highlights_limited": rec and rec.get("highlights_limited")}
            phones = {}
            for name in self.hub.phones.names():
                rec = self.records.get("phone:" + name)
                cur = self.live.get("phone:" + name) or {}
                phones[name] = {"status": cur.get("status") or ("calibrated" if rec else "waiting"),
                                "message": cur.get("message") or self.record_note(rec), "at": rec and rec["at"],
                                "iso": rec and rec.get("iso"), "k": rec and rec.get("k"),
                                "light_ok": rec and rec.get("light_ok")}
            return {"watching": self.watching(), "seq": self.seq, "devices": out, "phones": phones}

    @staticmethod
    def record_note(record):
        """What a kept calibration needs saying about itself (shown until it's redone)."""
        if record and record.get("card_even") is False:
            return UNEVEN_CARD
        if record and record.get("following"):
            return "its card saw different light (≈ %s K, the others ≈ %s K): using their colour" % (
                record.get("light_k"), record.get("others_k"))
        return None

    def reconcile(self):
        """Give a Pi camera whose card saw different light the other cameras' colour gains
        (see OUTLIER_MIRED); give a camera that no longer stands out its own back."""
        changes = {}
        with self.lock:
            recs = {cam: rec for cam, rec in self.records.items()
                    if cam in self.hub.cfg["nodes"] and rec.get("light_k")}
            newest = max((rec["at"] for rec in recs.values()), default=0)
            recs = {cam: rec for cam, rec in recs.items() if newest - rec["at"] <= SESSION_S}
            own = {cam: rec.get("own_awb_gains") or rec["awb_gains"] for cam, rec in recs.items()}
            for cam, rec in recs.items():
                others = [c for c in recs if c != cam]
                mireds = sorted(1e6 / recs[c]["light_k"] for c in others)
                follow, middle = None, None
                if len(others) >= 2:
                    middle = mireds[len(mireds) // 2] if len(mireds) % 2 else sum(mireds[len(mireds) // 2 - 1:len(mireds) // 2 + 1]) / 2
                    agree = all(abs(m - middle) <= OUTLIER_MIRED for m in mireds)
                    if agree and abs(1e6 / rec["light_k"] - middle) > OUTLIER_MIRED:
                        follow = sorted(others)
                if follow:
                    gains = [sorted(own[c][i] for c in follow) for i in (0, 1)]
                    awb = [round(g[len(g) // 2] if len(g) % 2 else (g[len(g) // 2 - 1] + g[len(g) // 2]) / 2, 3) for g in gains]
                else:
                    awb = own[cam]
                if awb != rec["awb_gains"] or follow != rec.get("following"):
                    rec.update(awb_gains=awb, own_awb_gains=own[cam], following=follow,
                               others_k=follow and round(1e6 / middle / 50) * 50)
                    changes[cam] = awb
            if changes:
                self._write()
        for cam, awb in changes.items():
            self.hub.set_camera_settings(cam, {"awb_gains": awb})
        return changes

    # -- phones calibrate themselves on their page (it has the camera controls) and report here
    PHONE_STATUS = ("waiting", "calibrating", "calibrated")

    def phone_report(self, name, body):
        st = body.get("status")
        if st not in self.PHONE_STATUS:
            raise ValueError("status must be one of %s" % ", ".join(self.PHONE_STATUS))
        msg = body.get("message")
        msg = str(msg)[:200] if msg else None
        key = "phone:" + name
        with self.lock:
            if st == "calibrated":
                rec = body.get("record")
                if not isinstance(rec, dict):
                    raise ValueError("a calibrated report needs its record")
                rec = dict(rec, at=self.clock(), card_even=card_lit_evenly(rec))
                self.records[key] = rec
                self._write()
                self.seq += 1
            self.live[key] = {"status": st, "message": msg}
        return {"ok": True}

    def _write(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.records, f, indent=1)
        os.replace(tmp, self.path)

    def _set(self, cam, status=None, message=None):
        with self.lock:
            self.live[cam] = {"status": status, "message": message}

    # -- the loop
    def tick(self):
        """Start a step for every camera that can take part and isn't busy. Non-blocking."""
        if not self.watching():
            return
        with self.hub.lock:
            ready = [n for n, st in self.hub.status.items() if st and st.get("mode") in ("idle", "live")]
        for cam in ready:
            with self.lock:
                if cam in self.busy or not self.due(cam):
                    continue
                self.busy.add(cam)
                self.last_look[cam] = self.clock()
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
            result = calib.measure(jpg, self.card)
            release_freed_memory()
            return result

    def card_gone(self, cam):
        if self.in_view.get(cam):
            self.often_until[cam] = self.clock() + AGAIN_S     # ready for the card again
        self.in_view[cam] = False
        self._set(cam)

    def step(self, cam):
        with self.hub.lock:
            st = self.hub.status.get(cam) or {}
        if st.get("mode") == "live":
            return self.step_live(cam, st)
        jpg, meta = self.hub.still(cam, auto=True)
        m = self._measure(jpg)
        if not m.get("grey"):
            self.card_gone(cam)
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
        target = warm_target(m["grey"], awb)
        g, wb, ok = next_settings(m["grey"], gain, awb, target=target[:2])
        rounds, sens = 0, (SENS, SENS)
        for rounds in range(1, ROUNDS + 1):
            jpg, _ = self.hub.still(cam, settings={"gain": g, "awb_gains": wb})
            m = self._measure(jpg)
            if not m.get("grey"):
                self._set(cam, "waiting", "the card moved out of view: hold it still")
                return
            target = warm_target(m["grey"], wb)
            if good(m["grey"], ok, target[:2]) or rounds == ROUNDS:
                break
            cur = (list(wb), m["grey"])
            sens = (sensitivity(prev, cur, 0), sensitivity(prev, cur, 1))
            prev = cur
            g, wb, ok = next_settings(m["grey"], g, wb, sens, target[:2])
        converged = good(m["grey"], ok, target[:2])
        g, limited = self.protect_highlights(
            g, m, lambda gain: self._measure(self.hub.still(cam, settings={"gain": gain, "awb_gains": wb})[0]))
        self.save(cam, g, wb, ok, m, rounds, converged, target, limited)
        self.in_view[cam] = True

    @staticmethod
    def protect_highlights(gain, m, measure):
        """Lower the gain while more than CLIP_MAX of the picture is blown out. m: the
        reading at `gain`; measure(gain): a reading at another. Returns (gain, lowered)."""
        lowered = False
        for _ in range(CLIP_ROUNDS):
            if m.get("clipped", 0) <= CLIP_MAX or gain <= 1.0:
                break
            gain = round(max(gain * CLIP_STEP, 1.0), 3)
            m = measure(gain)
            lowered = True
        return gain, lowered

    def step_live(self, cam, st):
        """In LIVE the camera belongs to MediaMTX: read frames from the stream and change
        settings on it (no restart, no gap: spike B-S1). No auto metering here, so start
        from the settings the stream runs with now."""
        m = self._measure(self.hub.grab_live(cam))
        if not m.get("grey"):
            self.card_gone(cam)
            return
        if self.in_view.get(cam):
            return
        bad = card_problem(m)
        if bad:
            self._set(cam, "waiting", bad)
            return
        self._set(cam, "calibrating", "hold the card still")
        with self.hub.lock:
            base = dict(st.get("settings") or {}, **self.hub.cam_settings.get(cam, {}))
        g, wb = float(base.get("gain", 2.0)), list(base.get("awb_gains", [1.5, 1.5]))
        prev, sens, ok, rounds = None, (SENS, SENS), True, 0
        for rounds in range(1, ROUNDS + 2):
            target = warm_target(m["grey"], wb)
            if good(m["grey"], ok, target[:2]) and rounds > 1:
                break
            cur = (list(wb), m["grey"])
            if prev:
                sens = (sensitivity(prev, cur, 0), sensitivity(prev, cur, 1))
            prev = cur
            g, wb, ok = next_settings(m["grey"], g, wb, sens, target[:2])
            self.hub.push_live(cam, {"gain": g, "awb_gains": wb})
            self.hub.sleep(1.0)          # let frames with the new settings through
            m = self._measure(self.hub.grab_live(cam))
            if not m.get("grey"):
                self._set(cam, "waiting", "the card moved out of view: hold it still")
                return
        target = warm_target(m["grey"], wb)
        converged = good(m["grey"], ok, target[:2])

        def measure_live(gain):
            self.hub.push_live(cam, {"gain": gain, "awb_gains": wb})
            self.hub.sleep(1.0)
            return self._measure(self.hub.grab_live(cam))
        g, limited = self.protect_highlights(g, m, measure_live)
        self.save(cam, g, wb, ok, m, rounds, converged, target, limited)
        self.in_view[cam] = True

    def save(self, cam, gain, awb, reachable, m, rounds, converged, target=(1.0, 1.0, None), highlights=False):
        """reachable: the target brightness was within MAX_GAIN. target: warm_target's
        (R/G, B/G, light's kelvin) the grey was aimed at. highlights: the gain was lowered
        to keep the picture from blowing out (m is the card reading from before that)."""
        self.hub.set_camera_settings(cam, {"gain": gain, "awb_gains": awb})
        light_ok = reachable or highlights or light_level(m["grey"]) >= DARK_OK
        rec = {"at": self.clock(), "gain": gain, "awb_gains": awb, "light_ok": light_ok, "converged": converged,
               "light_level": round(light_level(m["grey"]), 2), "highlights_limited": highlights,
               "light_k": target[2] and round(target[2] / 50) * 50, "warm_target": [round(target[0], 3), round(target[1], 3)],
               "rounds": rounds, "grey": m["grey"], "patches": m["patches"], "marker_px": m.get("marker_px"),
               "card_even": card_lit_evenly(m), "own_awb_gains": awb}
        with self.lock:
            self.records[cam] = rec
            self._write()
            self.seq += 1
            self.often_until[cam] = self.clock() + AGAIN_S     # watch for the card leaving
            msg = None if light_ok else "not enough light: at maximum gain"
            if not converged and light_ok:
                msg = "close, not exact: show the card again to retry"
            msg = msg or self.record_note(rec)
            self.live[cam] = {"status": "calibrated", "message": msg}
        self.reconcile()            # this camera, or another, may stand out now
