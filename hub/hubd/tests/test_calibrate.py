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
        # Settings go out with the next take, and the take log keeps the card readings.
        self.hub.rec()
        arm = [b for n, p, b in self.rig.bodies if n == "cam1" and p == "/take/arm"][-1]
        self.assertEqual(arm["settings"]["gain"], s["gain"])
        self.assertEqual(self.hub.stop()["calibration"]["cam1"]["gain"], s["gain"])

    def test_card_left_in_view_does_not_recalibrate_until_it_returns(self):
        cal = self.hub.calibrator
        cal.page_open()
        cal.tick()
        cal.tick()
        self.assertEqual(cal.status()["seq"], 1)
        self.card_visible = set()
        cal.tick()
        self.card_visible = {"cam1"}
        cal.tick()
        self.assertEqual(cal.status()["seq"], 2)

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
