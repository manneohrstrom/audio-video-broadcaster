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

# A camera under warm light: the card's grey gives these raw linear values per unit of
# gain (red strong, blue weak), before the colour gains (green's is 1).
RAW = (0.0440, 0.0210, 0.0076)


# The colour matrix after the colour gains mixes the channels (raising red lowers green),
# like the real camera: this is what made a plain multiplicative step diverge.
CCM = [[1.9, -0.7, -0.2], [-0.45, 1.75, -0.3], [-0.05, -0.75, 1.8]]


def picture(gain, awb, raw=RAW, ccm=CCM):
    wb = [raw[0] * gain * awb[0], raw[1] * gain, raw[2] * gain * awb[1]]
    lin = [sum(ccm[i][j] * wb[j] for j in range(3)) for i in range(3)]
    return [round(min(255.0, 255 * min(1.0, max(v, 0.0)) ** (1 / 2.2)), 1) for v in lin]


def run(gain, awb, raw=RAW, rounds=calibrate.ROUNDS):
    """The calibrator's loop against the simulated camera."""
    grey = picture(gain, awb, raw)
    prev = (list(awb), grey)
    g, wb, ok = calibrate.next_settings(grey, gain, awb)
    for _ in range(rounds):
        grey = picture(g, wb, raw)
        if calibrate.good(grey, ok):
            break
        cur = (list(wb), grey)
        sens = (calibrate.sensitivity(prev, cur, 0), calibrate.sensitivity(prev, cur, 1))
        prev = cur
        g, wb, ok = calibrate.next_settings(grey, g, wb, sens)
    return g, wb, ok, picture(g, wb, raw)


class MathTest(unittest.TestCase):
    def test_converges_from_auto(self):
        for start in ([1.2, 2.0], [2.5, 1.0], [0.8, 4.0], [1.24, 4.12]):
            g, wb, ok, grey = run(4.0, start)
            self.assertTrue(ok, start)
            self.assertTrue(calibrate.good(grey), (start, grey, wb))

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
            return {"found": [0, 1], "marker_px": 80, "grey_spread": 2.0, "grey": picture(g, wb), "patches": {}}
        cal._measure = measure

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
        self.assertTrue(calibrate.good(picture(s["gain"], s["awb_gains"])))
        rec = json.load(open(os.path.join(self.cfg["state_dir"], "calibration.json")))["cam1"]
        self.assertTrue(rec["converged"] and rec["light_ok"])
        self.assertIn("card_even", rec)
        # Settings go out with the next take, and the take log keeps the card readings.
        self.hub.rec()
        arm = [b for n, p, b in self.rig.bodies if n == "cam1" and p == "/take/arm"][-1]
        self.assertEqual(arm["settings"]["gain"], s["gain"])
        self.assertEqual(self.hub.stop()["calibration"]["cam1"]["gain"], s["gain"])

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
        self.assertTrue(calibrate.good(picture(ls["gain"], ls["awb_gains"])))
        self.assertEqual(self.hub.cam_settings["cam1"]["gain"], ls["gain"])    # kept for the next take too
        self.assertFalse(any(p == "/snapshot" for n, p, b in self.rig.bodies if n == "cam1"))
