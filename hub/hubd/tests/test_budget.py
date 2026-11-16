import os
import sys
import tempfile
import unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import budget  # noqa: E402
import hubd  # noqa: E402
from test_hubd import FakeRig  # noqa: E402

GB, M = 1e9, budget.MBPS


class LimitsTest(unittest.TestCase):
    def test_rig_from_the_plan(self):
        # 4 Pis at 8 Mbps, 5 phones at 15, 115 GB free on the hub, 15 GB reserve:
        # 100 GB / (75 + 32 + 2*15 Mbps) = 100e9 / 17.125e6 B/s = 5839 s (97 min).
        pis = {"cam%d" % i: (25 * GB, 8 * M) for i in range(1, 5)}
        r = budget.limits(115 * GB, 15 * GB, pis, {"p%d" % i: 15 * M for i in range(5)})
        self.assertEqual(r["limited_by"], "hub")
        self.assertAlmostEqual(r["total_s"], 100e9 / (137 * M), delta=1)
        self.assertEqual(r["remaining_s"], r["total_s"])

    def test_camera_card_is_the_limit_without_phones(self):
        pis = {"cam1": (10 * GB, 8 * M), "cam2": (5 * GB, 8 * M)}
        r = budget.limits(115 * GB, 15 * GB, pis, {}, node_reserve_b=1 * GB)
        self.assertEqual(r["limited_by"], "cam2")
        self.assertEqual(r["total_s"], round(4 * GB / (8 * M)))     # 4000 s

    def test_during_a_take_uploaded_phone_video_is_not_counted_twice(self):
        # 600 s in: the phone has uploaded 600*rate, so the hub has that much less free.
        # The total stays what it was at REC.
        ph = {"p": 15 * M}
        before = budget.limits(100 * GB, 0, {}, ph)
        during = budget.limits(100 * GB - 600 * 15 * M, 0, {}, ph, elapsed_s=600)
        self.assertAlmostEqual(during["total_s"], before["total_s"], delta=1)
        self.assertEqual(during["remaining_s"], during["total_s"] - 600)

    def test_never_negative_and_nothing_to_record(self):
        r = budget.limits(1 * GB, 15 * GB, {"cam1": (1 * GB, 8 * M)}, {}, elapsed_s=30)
        self.assertEqual(r["remaining_s"], 0)
        self.assertIsNone(budget.limits(100 * GB, 0, {}, {})["total_s"])

    def test_unknown_free_space_is_skipped(self):
        r = budget.limits(None, 0, {"cam1": (None, 8 * M), "cam2": (2 * GB, 8 * M)}, {})
        self.assertEqual(list(r["per"]), ["cam2"])

    def test_measured_rate(self):
        d = 10 * M
        self.assertEqual(budget.measured_rate(None, 300, d), d)
        self.assertEqual(budget.measured_rate(1e9, 30, d), d)                # too early
        self.assertEqual(budget.measured_rate(600 * 8 * M, 600, d), 8 * M)
        self.assertEqual(budget.measured_rate(600 * 1 * M, 600, d), d / 2)   # floor


class HubBudgetTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.cfg = hubd.load_config(None)
        self.cfg.update(state_dir=tempfile.mkdtemp(), client_head_start_s=0, sync_timeout_s=1)
        self.rig = FakeRig(self.cfg["nodes"])
        for s in self.rig.st.values():
            s["free_gb"] = 20.0
        self.hub = hubd.Hub(self.cfg, nodes=self.rig, clock=lambda: self.now[0], sleep=lambda s: None)
        self.hub.health = {"free_gb": 100.0}

        self.hub.spawn = lambda fn, *a: fn(*a)

    def stopped(self):
        return self.hub.snapshot()["state"] == "idle"

    def test_snapshot_shows_the_estimate(self):
        self.hub.poll_once()
        self.hub.check_budget()
        b = self.hub.snapshot()["budget"]
        # Hub: 85 GB / (4*10 + 2*10 Mbps) = 11333 s; each camera: 19 GB / 10 Mbps = 15200 s.
        self.assertEqual(b["limited_by"], "hub")
        self.assertEqual(b["total_s"], round(85e9 / (60 * M)))
        self.assertEqual((b["cameras"], b["phones"], b["warn"]), (4, 0, False))

    def test_rec_refused_with_almost_no_space(self):
        self.hub.health = {"free_gb": 15.5}
        with self.assertRaises(hubd.Refused) as e:
            self.hub.rec()
        self.assertIn("recording space", e.exception.reasons[0])
        self.assertEqual(self.rig.calls, [])

    def test_take_stops_itself_when_space_runs_out(self):
        self.hub.rec()
        self.now[0] += 60
        self.hub.check_budget()
        self.assertFalse(self.stopped())
        self.now[0] += 20000               # past every limit
        self.hub.check_budget()
        self.assertTrue(self.stopped())
        self.assertIn("stopped automatically", self.hub.snapshot()["message"])
        self.assertTrue(any(p == "/take/stop" for _, p, _ in self.rig.calls))

    def test_auto_stop_can_be_switched_off(self):
        self.cfg["budget"]["auto_stop"] = False
        self.hub.rec()
        self.now[0] += 20000
        self.hub.check_budget()
        self.assertFalse(self.stopped())
        self.assertEqual(self.hub.snapshot()["budget"]["remaining_s"], 0)

    def test_measured_camera_rate_takes_over(self):
        self.hub.rec()
        self.now[0] += 1000
        for s in self.rig.st.values():
            s["free_gb"] = 19.0            # 1 GB in 1000 s = 8 Mbps
        self.hub.poll_once()
        b = self.hub.compute_budget()
        self.assertEqual(b["total_s"], round(85e9 / (48 * M)))


if __name__ == "__main__":
    unittest.main()
