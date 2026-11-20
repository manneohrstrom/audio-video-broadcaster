import json
import os
import sys
import tempfile
import unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import calibrate  # noqa: E402
import hubd  # noqa: E402
from test_hubd import FakeRig  # noqa: E402

# A camera under the studio's warm light: the card's grey gives these raw linear values per
# unit of gain (blue weak), before the colour gains (green's is 1). They need cam1's colour
# gains of 2026-11-19 (red 1.083, blue 5.157) to read neutral: about 2200 K. (Until
# 2026-11-20 red was 0.044, a light no real one is like: about 1600 K.)
RAW = (0.0210 / 1.083, 0.0210, 0.0210 / 5.157)


# The colour matrix after the colour gains mixes the channels (raising red lowers green),
# like the real camera: this is what made a plain multiplicative step diverge.
CCM = [[1.9, -0.7, -0.2], [-0.45, 1.75, -0.3], [-0.05, -0.75, 1.8]]


def picture(gain, awb, raw=RAW, ccm=CCM):
    wb = [raw[0] * gain * awb[0], raw[1] * gain, raw[2] * gain * awb[1]]
    lin = [sum(ccm[i][j] * wb[j] for j in range(3)) for i in range(3)]
    return [round(min(255.0, 255 * min(1.0, max(v, 0.0)) ** (1 / 2.2)), 1) for v in lin]


def run(gain, awb, raw=RAW, rounds=calibrate.ROUNDS, warm=False):
    """The calibrator's loop against the simulated camera (warm: aim at warm_target, as
    the Calibrator does; otherwise neutral)."""
    neutral = (1.0, 1.0)
    grey = picture(gain, awb, raw)
    prev = (list(awb), grey)
    sens = (calibrate.SENS, calibrate.SENS)
    target = calibrate.warm_target(grey, awb)[:2] if warm else neutral
    g, wb, ok = calibrate.next_settings(grey, gain, awb, target=target)
    for _ in range(rounds):
        grey = picture(g, wb, raw)
        target = calibrate.warm_target(grey, wb)[:2] if warm else neutral
        if calibrate.good(grey, ok, target):
            break
        cur = (list(wb), grey)
        sens = (calibrate.sensitivity(prev, cur, 0), calibrate.sensitivity(prev, cur, 1))
        prev = cur
        g, wb, ok = calibrate.next_settings(grey, g, wb, sens, target)
    return g, wb, ok, picture(g, wb, raw)


def studio_raw(neutral_awb, level=0.021):
    """Raw grey values per unit of gain for a light that needs these colour gains to be neutral."""
    return (level / neutral_awb[0], level, level / neutral_awb[1])


def ratios(grey):
    return calibrate.ratios(grey)


def aimed(gain, awb, raw=RAW, brightness=True):
    """True if the simulated camera at these settings reads the grey on its warm target
    (brightness=False: its colour only)."""
    grey = picture(gain, awb, raw)
    return calibrate.good(grey, brightness, calibrate.warm_target(grey, awb)[:2])


class MathTest(unittest.TestCase):
    def test_converges_from_auto(self):
        for start in ([1.2, 2.0], [2.5, 1.0], [0.8, 4.0], [1.24, 4.12]):
            g, wb, ok, grey = run(4.0, start)
            self.assertTrue(ok, start)
            self.assertTrue(calibrate.good(grey), (start, grey, wb))
            g, wb, ok, grey = run(4.0, start, warm=True)
            self.assertTrue(calibrate.good(grey, ok, calibrate.warm_target(grey, wb)[:2]), (start, grey, wb))

    def test_plain_step_would_diverge(self):
        # Guard for the bug of 2026-11-16: with the colour matrix, sensitivity 1 overshoots.
        grey = picture(4.0, [1.2, 2.0])
        g, wb, _ = calibrate.next_settings(grey, 4.0, [1.2, 2.0], sens=(1.0, 1.0))
        for _ in range(4):
            g, wb, _ = calibrate.next_settings(picture(g, wb), g, wb, sens=(1.0, 1.0))
        self.assertFalse(calibrate.good(picture(g, wb)))

    def test_not_enough_light(self):
        dark = tuple(v / 20 for v in RAW)
        g, wb, ok, grey = run(8.0, [1.2, 2.0], dark)
        self.assertFalse(ok)
        self.assertEqual(g, calibrate.MAX_GAIN)
        self.assertTrue(calibrate.good(grey, light_ok=False))   # neutral, just dark

    def test_clipped_reading_steps_down(self):
        g, _, _ = calibrate.next_settings([255, 255, 255], 10.0, [1.5, 1.5])
        self.assertEqual(g, 5.0)

    def test_light_kelvin_follows_the_cameras_curve(self):
        for kelvin, red, blue in calibrate.CT_CURVE:
            self.assertAlmostEqual(calibrate.light_kelvin([red, blue]), kelvin, delta=1)
        # Between points, and past the warm end: the four cameras' calibrations of 2026-11-19.
        self.assertTrue(3000 < calibrate.light_kelvin([1.45, 2.8]) < 3650)
        for awb in ([1.083, 5.157], [1.013, 5.048], [1.076, 5.032], [0.976, 5.318]):
            self.assertTrue(1950 < calibrate.light_kelvin(awb) < 2250, awb)

    def test_warmth_by_colour_temperature(self):
        self.assertEqual(calibrate.warmth(5000), 0)
        self.assertEqual(calibrate.warmth(6500), 0)
        self.assertAlmostEqual(calibrate.warmth(calibrate.ANCHOR_K), 1)
        self.assertTrue(0 < calibrate.warmth(3500) < calibrate.warmth(2750) < 1)
        self.assertEqual(calibrate.warmth(1000), calibrate.MAX_WARM)

    def test_warm_light_stays_warm_like_the_pixel(self):
        # The studio's bulbs (cam1's neutral gains, 2026-11-19): the grey ends up as warm as
        # the Pixel 9a read it, not neutral, with less blue gain and more red than neutral.
        raw = studio_raw([1.083, 5.157])
        g, wb, ok, grey = run(8.0, [1.5, 3.0], raw, warm=True)
        rg, bg = ratios(grey)
        self.assertAlmostEqual(rg, calibrate.ANCHOR_RG, delta=0.06)
        self.assertAlmostEqual(bg, calibrate.ANCHOR_BG, delta=0.04)
        _, neutral_wb, _, neutral_grey = run(8.0, [1.5, 3.0], raw)
        self.assertTrue(calibrate.good(neutral_grey))
        self.assertGreater(wb[0], neutral_wb[0])
        self.assertLess(wb[1], neutral_wb[1])

    def test_daylight_stays_neutral(self):
        raw = studio_raw([2.142, 1.695])            # 5715 K, the curve's coolest point
        g, wb, ok, grey = run(4.0, [1.5, 1.5], raw, warm=True)
        self.assertTrue(calibrate.good(grey), grey)

    def test_warmth_zero_is_the_old_neutral_calibration(self):
        old = calibrate.WARMTH
        calibrate.WARMTH = 0.0
        try:
            _, _, _, grey = run(8.0, [1.5, 3.0], studio_raw([1.083, 5.157]), warm=True)
            self.assertTrue(calibrate.good(grey), grey)
        finally:
            calibrate.WARMTH = old

    def test_protect_highlights_steps_down_until_little_is_blown_out(self):
        seen = []

        def measure(gain):
            seen.append(gain)
            return {"clipped": gain * 0.0025}
        gain, lowered = calibrate.Calibrator.protect_highlights(32.0, {"clipped": 0.08}, measure)
        self.assertTrue(lowered)
        self.assertLessEqual(gain * 0.0025, calibrate.CLIP_MAX)
        self.assertEqual(seen, [24.0, 18.0, 13.5])
        self.assertEqual(calibrate.Calibrator.protect_highlights(8.0, {"clipped": 0.01}, measure), (8.0, False))
        # Never more than CLIP_ROUNDS stills, never below gain 1.
        gain, _ = calibrate.Calibrator.protect_highlights(4.0, {"clipped": 1.0}, lambda g: {"clipped": 1.0})
        self.assertEqual(gain, 1.266)

    def test_card_problems(self):
        self.assertIn("closer", calibrate.card_problem({"marker_px": 10, "grey_spread": 1}))
        self.assertIn("reflecting", calibrate.card_problem({"marker_px": 80, "grey_spread": 30}))
        self.assertIsNone(calibrate.card_problem({"marker_px": 80, "grey_spread": 2}))


class CardEvennessTest(unittest.TestCase):
    """The white patch against the grey: is the card lit evenly enough for its colour readings?"""

    def test_an_evenly_lit_card(self):
        self.assertTrue(calibrate.card_lit_evenly({"grey": [119, 119, 119], "patches": {"white": [236, 238, 240]}}))

    def test_the_readings_of_2026_11_16(self):
        # cam1 and cam3 from that day: the right side of the card was dark.
        self.assertFalse(calibrate.card_lit_evenly({"grey": [118.1, 118.1, 117.1], "patches": {"white": [154.9, 155.5, 159.5]}}))
        self.assertFalse(calibrate.card_lit_evenly({"grey": [120, 119, 119], "patches": {"white": [107, 103, 94]}}))

    def test_no_white_reading_says_nothing(self):
        self.assertIsNone(calibrate.card_lit_evenly({"grey": [119, 119, 119], "patches": {}}))
        self.assertIsNone(calibrate.Calibrator.record_note({"card_even": None}))
        self.assertEqual(calibrate.Calibrator.record_note({"card_even": False}), calibrate.UNEVEN_CARD)

class CalibratorTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.cfg = hubd.load_config(None)
        self.cfg.update(state_dir=tempfile.mkdtemp(), client_head_start_s=0, sync_timeout_s=1)
        self.rig = FakeRig(self.cfg["nodes"])
        self.hub = hubd.Hub(self.cfg, nodes=self.rig, clock=lambda: self.now[0], sleep=lambda s: None)
        self.hub.spawn = lambda fn, *a: fn(*a)
        self.hub.poll_once()
        self.card_visible = {"cam1"}
        cal = self.hub.calibrator

        def measure(jpg):           # the fake still carries its request: picture from its settings
            body = json.loads(jpg[len(b"\xff\xd8snap "):])
            cam = next(n for n, p, b in reversed(self.rig.bodies) if b is not None and json.dumps(b) == json.dumps(body))
            if cam not in self.card_visible:
                return {"found": []}
            st = body["settings"]
            g, wb = (12.0, [1.9, 2.6]) if body["auto"] else (st["gain"], st["awb_gains"])
            self.gains_seen.append(g)
            return {"found": [0, 1], "marker_px": 80, "grey_spread": 2.0, "grey": picture(g, wb), "patches": {},
                    "clipped": self.clip_per_gain * g}
        self.clip_per_gain = 0.0         # share of the picture blown out per unit of gain
        self.gains_seen = []
        cal._measure = measure

    def test_lowers_the_gain_to_keep_the_highlights(self):
        # Like cam1 on 2026-11-20: the card's brightness would blow out much of the room.
        cal = self.hub.calibrator
        self.clip_per_gain = 0.008        # 7.2 % blown out at the card's gain (9)
        cal.page_open()
        cal.tick()
        s = self.hub.cam_settings["cam1"]
        self.assertLessEqual(self.clip_per_gain * s["gain"], calibrate.CLIP_MAX)
        self.assertEqual(s["gain"], self.gains_seen[-1])                 # the last still had it
        rec = json.load(open(os.path.join(self.cfg["state_dir"], "calibration.json")))["cam1"]
        self.assertTrue(rec["highlights_limited"] and rec["light_ok"], rec)
        st = cal.status()["devices"]["cam1"]
        self.assertTrue(st["highlights_limited"])
        self.assertIsNone(st["message"])                                 # not a warning
        self.assertTrue(aimed(s["gain"], s["awb_gains"], brightness=False))   # darker, colour as aimed

    def calibrated(self, cam, kelvin, awb, at=None, gain=10.0):
        cal = self.hub.calibrator
        self.now[0] = at or self.now[0] + 60
        cal.save(cam, gain, awb, True, {"grey": [130, 119, 103], "patches": {}}, 3, True, (1.2, 0.76, kelvin))

    def test_a_camera_whose_card_saw_other_light_follows_the_others(self):
        # 2026-11-20: cam4's card by the door caught daylight.
        self.calibrated("cam2", 2050, [1.09, 4.376])
        self.calibrated("cam1", 2150, [1.203, 4.465])
        self.calibrated("cam3", 2100, [1.107, 4.615])
        self.calibrated("cam4", 2900, [1.426, 3.168], gain=8.777)
        s = self.hub.cam_settings
        self.assertEqual(s["cam4"]["awb_gains"], [1.107, 4.465])          # the others' median
        self.assertEqual(s["cam4"]["gain"], 8.777)                         # its own brightness
        self.assertEqual(s["cam1"]["awb_gains"], [1.203, 4.465])           # the others keep theirs
        rec = self.hub.calibrator.records["cam4"]
        self.assertEqual(rec["following"], ["cam1", "cam2", "cam3"])
        self.assertEqual(rec["own_awb_gains"], [1.426, 3.168])
        msg = self.hub.calibrator.status()["devices"]["cam4"]["message"]
        self.assertIn("2900", msg)
        self.assertIn("2100", msg)
        # Shown the card again in the same light as the others: its own gains again.
        self.calibrated("cam4", 2150, [1.15, 4.4])
        self.assertEqual(s["cam4"]["awb_gains"], [1.15, 4.4])
        self.assertIsNone(self.hub.calibrator.records["cam4"].get("following"))

    def test_the_odd_one_out_also_when_it_was_calibrated_first(self):
        self.calibrated("cam4", 2900, [1.426, 3.168])
        self.assertEqual(self.hub.cam_settings["cam4"]["awb_gains"], [1.426, 3.168])   # nothing to compare yet
        self.calibrated("cam1", 2150, [1.2, 4.4])
        self.calibrated("cam2", 2100, [1.1, 4.5])
        self.assertEqual(self.hub.cam_settings["cam4"]["awb_gains"], [1.15, 4.45])

    def test_no_odd_one_out_when_the_others_disagree_or_are_old(self):
        self.calibrated("cam1", 2150, [1.2, 4.4])
        self.calibrated("cam2", 3200, [1.5, 2.9])
        self.calibrated("cam4", 2900, [1.426, 3.168])
        self.assertEqual(self.hub.cam_settings["cam4"]["awb_gains"], [1.426, 3.168])
        # Calibrations from another session (older than SESSION_S) don't count.
        self.calibrated("cam3", 2900, [1.4, 3.2], at=self.now[0] + calibrate.SESSION_S + 60)
        self.assertEqual(self.hub.cam_settings["cam3"]["awb_gains"], [1.4, 3.2])

    def test_highlights_fine_leaves_the_gain(self):
        cal = self.hub.calibrator
        self.clip_per_gain = 0.0001
        cal.page_open()
        cal.tick()
        rec = json.load(open(os.path.join(self.cfg["state_dir"], "calibration.json")))["cam1"]
        self.assertFalse(rec["highlights_limited"])

    def test_shows_waiting_until_a_page_is_open(self):
        cal = self.hub.calibrator
        cal.tick()
        self.assertEqual(self.rig.bodies, [])                # no page open: no stills
        self.assertEqual(cal.status()["devices"]["cam1"]["status"], "waiting")

    def test_calibrates_the_camera_that_sees_the_card(self):
        cal = self.hub.calibrator
        cal.page_open()
        cal.tick()
        st = cal.status()
        self.assertEqual(st["devices"]["cam1"]["status"], "calibrated")
        self.assertEqual(st["devices"]["cam2"]["status"], "waiting")
        self.assertEqual(st["seq"], 1)
        s = self.hub.cam_settings["cam1"]
        self.assertTrue(aimed(s["gain"], s["awb_gains"]))
        rec = json.load(open(os.path.join(self.cfg["state_dir"], "calibration.json")))["cam1"]
        self.assertTrue(rec["converged"] and rec["light_ok"])
        self.assertTrue(rec["light_k"] and rec["warm_target"][0] >= 1 and rec["warm_target"][1] <= 1, rec)
        self.assertEqual(st["devices"]["cam1"]["light_k"], rec["light_k"])
        self.assertIn("card_even", rec)
        # Settings go out with the next take, and the take log keeps the card readings.
        self.hub.rec()
        arm = [b for n, p, b in self.rig.bodies if n == "cam1" and p == "/take/arm"][-1]
        self.assertEqual(arm["settings"]["gain"], s["gain"])
        self.assertEqual(self.hub.stop()["calibration"]["cam1"]["gain"], s["gain"])

    def test_a_little_dark_at_maximum_gain_is_no_problem(self):
        # The studio on 2026-11-19: cam2's grey 106 at maximum gain (about 80 % of the target).
        cal = self.hub.calibrator
        m = {"grey": [107.1, 105.7, 105.1], "patches": {}}
        cal.save("cam2", calibrate.MAX_GAIN, [1.05, 5.16], False, m, 3, True)
        rec = cal.records["cam2"]
        self.assertTrue(rec["light_ok"])
        self.assertAlmostEqual(rec["light_level"], 0.77, places=2)
        self.assertIsNone(cal.status()["devices"]["cam2"]["message"])

    def test_more_than_a_stop_dark_at_maximum_gain_warns(self):
        cal = self.hub.calibrator
        m = {"grey": [71.3, 70.4, 71.0], "patches": {}}      # cam4 that evening: about 32 %
        cal.save("cam4", calibrate.MAX_GAIN, [0.97, 5.33], False, m, 4, True)
        self.assertFalse(cal.records["cam4"]["light_ok"])
        self.assertIn("not enough light", cal.status()["devices"]["cam4"]["message"])

    def test_gain_above_the_sensor_limit_is_accepted(self):
        self.hub.set_camera_settings("cam1", {"gain": 24.0})
        self.assertEqual(self.hub.cam_settings["cam1"]["gain"], 24.0)
        with self.assertRaises(ValueError):
            self.hub.set_camera_settings("cam1", {"gain": calibrate.MAX_GAIN + 1})

    def test_card_left_in_view_does_not_recalibrate_until_it_returns(self):
        cal = self.hub.calibrator
        cal.page_open()
        for vis in ({"cam1"}, {"cam1"}, set(), {"cam1"}):
            self.card_visible = vis
            self.now[0] += calibrate.EVERY_S
            cal.page_open(); cal.tick()
        self.assertEqual(cal.status()["seq"], 2)

    def test_looks_less_often_once_settled(self):
        cal = self.hub.calibrator
        looks = lambda: len([b for b in self.rig.bodies if b[0] == "cam2" and b[1] == "/snapshot"])
        cal.page_open()
        cal.watch_until = 1e12                      # the page keeps polling throughout
        cal.tick()
        n = looks()
        cal.tick()                                  # too soon
        self.assertEqual(looks(), n)
        self.now[0] += calibrate.EVERY_S
        cal.tick()
        self.assertEqual(looks(), n + 1)            # uncalibrated: every EVERY_S
        cal.records["cam2"] = {"at": 0, "gain": 4, "awb_gains": [1, 1], "light_ok": True}
        self.now[0] += calibrate.SETUP_S
        cal.tick()
        self.assertEqual(looks(), n + 2)
        self.now[0] += calibrate.EVERY_S
        cal.tick()
        self.assertEqual(looks(), n + 2)            # settled: only every EVERY_SETTLED_S
        self.now[0] += calibrate.EVERY_SETTLED_S
        cal.tick()
        self.assertEqual(looks(), n + 3)

    def test_status_says_when_a_camera_takes_a_still(self):
        shooting = lambda: self.hub.snapshot()["nodes"]["cam1"]["shooting"]
        self.assertFalse(shooting())
        with self.hub.taking_still("cam1"):
            self.assertTrue(shooting())
        self.assertTrue(shooting())                 # lingers so the page's 1 s poll sees it
        self.now[0] += hubd.SHOT_LINGER_S
        self.assertFalse(shooting())

    def test_looks_often_after_a_calibration_so_the_card_can_come_back(self):
        cal = self.hub.calibrator
        looks = lambda: len([b for b in self.rig.bodies if b[0] == "cam1" and b[1] == "/snapshot"])
        cal.page_open()
        cal.watch_until = 1e12
        self.now[0] += calibrate.SETUP_S + 1        # setup over: a calibrated camera would settle
        cal.tick()
        self.assertEqual(cal.status()["seq"], 1)
        self.card_visible = set()                   # card lowered for one look
        self.now[0] += calibrate.EVERY_S
        n = looks()
        cal.tick()
        self.assertEqual(looks(), n + 1)            # still looking every EVERY_S
        self.card_visible = {"cam1"}                # and shown again
        self.now[0] += calibrate.EVERY_S
        cal.tick()
        self.assertEqual(cal.status()["seq"], 2)
        self.now[0] += calibrate.AGAIN_S            # then it settles to EVERY_SETTLED_S
        cal.tick()
        n = looks()
        self.now[0] += calibrate.EVERY_S
        cal.tick()
        self.assertEqual(looks(), n)

    def test_nothing_during_a_take(self):
        cal = self.hub.calibrator
        self.hub.rec()
        cal.page_open()
        n = len(self.rig.bodies)
        cal.tick()
        self.assertEqual(len([b for b in self.rig.bodies[n:] if b[1] == "/snapshot"]), 0)
        self.hub.stop()


if __name__ == "__main__":
    unittest.main()


class PhoneCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.cfg = hubd.load_config(None)
        self.cfg.update(state_dir=tempfile.mkdtemp(), client_head_start_s=0, sync_timeout_s=1)
        self.rig = FakeRig(self.cfg["nodes"])
        self.hub = hubd.Hub(self.cfg, nodes=self.rig, clock=lambda: self.now[0], sleep=lambda s: None)
        self.body = {"phone": "Paul's Pixel", "page": "abcdefgh1", "state": "ready"}

    def test_poll_tells_the_phone_when_to_watch_and_its_mount(self):
        r = self.hub.phone_poll(self.body)
        self.assertEqual((r["calib_watch"], r["mount"]), (False, "tripod"))
        self.hub.calibrator.page_open()
        self.assertTrue(self.hub.phone_poll(self.body)["calib_watch"])
        self.hub.phones.set_mount("pauls-pixel", "handheld")
        self.assertEqual(self.hub.phone_poll(self.body)["mount"], "handheld")
        with self.assertRaises(ValueError):
            self.hub.phones.set_mount("pauls-pixel", "gimbal")
        again = hubd.Hub(self.cfg, nodes=self.rig, sleep=lambda s: None)
        self.assertEqual(again.phones.mounts, {"pauls-pixel": "handheld"})

    def test_phone_reports_and_take_keeps_its_record(self):
        cal = self.hub.calibrator
        self.hub.phone_poll(self.body)
        self.assertEqual(cal.status()["phones"]["pauls-pixel"]["status"], "waiting")
        cal.phone_report("pauls-pixel", {"status": "calibrating", "message": "hold the card still"})
        self.assertEqual(cal.status()["phones"]["pauls-pixel"]["status"], "calibrating")
        with self.assertRaises(ValueError):
            cal.phone_report("pauls-pixel", {"status": "calibrated"})          # no record
        cal.phone_report("pauls-pixel", {"status": "calibrated", "record": {
            "iso": 170, "k": 2850, "light_ok": True, "grey": [120, 119, 118], "patches": {"white": [140, 139, 138]}}})
        self.assertEqual(cal.status()["phones"]["pauls-pixel"]["message"], calibrate.UNEVEN_CARD)   # kept with the record
        cal.phone_report("pauls-pixel", {"status": "calibrated", "record": {"iso": 170, "k": 2850, "light_ok": True}})
        st = cal.status()
        self.assertEqual((st["seq"], st["phones"]["pauls-pixel"]["iso"]), (2, 170))   # two reports
        self.hub.poll_once()
        self.hub.rec()
        self.hub.phone_poll(self.body)          # the phone is told to record
        s = self.hub.stop()
        self.assertEqual(s["calibration"]["phone:pauls-pixel"]["k"], 2850)


class LiveCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.cfg = hubd.load_config(None)
        self.cfg.update(state_dir=tempfile.mkdtemp(), client_head_start_s=0, sync_timeout_s=1)
        self.rig = FakeRig(self.cfg["nodes"])
        for st in self.rig.st.values():
            st["settings"] = {"gain": 2.0, "awb_gains": [1.5, 1.5], "lens_position": 1.0, "shutter_us": 20000}
        self.hub = hubd.Hub(self.cfg, nodes=self.rig, sleep=lambda s: None)
        self.hub.spawn = lambda fn, *a: fn(*a)
        self.hub.set_camera_settings("cam1", {"lens_position": 0.7})
        self.hub.live(True, ["cam1"])
        self.hub.poll_once()

        def grab(cam):                   # a frame from the stream, as its current settings make it
            st = dict(self.rig.st[cam]["settings"], **self.rig.st[cam].get("live_settings", {}))
            return json.dumps({"cam": cam, "gain": st["gain"], "awb": st["awb_gains"]}).encode()
        self.hub.grab_live = grab

        def measure(jpg):
            d = json.loads(jpg)
            return {"found": [0, 1], "marker_px": 80, "grey_spread": 1.0, "grey": picture(d["gain"], d["awb"]), "patches": {}}
        self.hub.calibrator._measure = measure

    def test_live_start_sends_the_hubs_settings(self):
        self.assertEqual(self.rig.st["cam1"]["live_settings"], {"lens_position": 0.7})

    def test_calibrates_a_live_camera_on_its_stream(self):
        cal = self.hub.calibrator
        cal.page_open()
        cal.tick()
        st = cal.status()["devices"]["cam1"]
        self.assertEqual(st["status"], "calibrated")
        ls = self.rig.st["cam1"]["live_settings"]
        self.assertTrue(aimed(ls["gain"], ls["awb_gains"]))
        self.assertEqual(self.hub.cam_settings["cam1"]["gain"], ls["gain"])    # kept for the next take too
        self.assertFalse(any(p == "/snapshot" for n, p, b in self.rig.bodies if n == "cam1"))
