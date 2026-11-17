import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import health_history  # noqa: E402
import hubd  # noqa: E402


class HistoryTest(unittest.TestCase):
    def test_one_point_per_interval_and_gaps_for_offline_cameras(self):
        now = [0.0]
        h = health_history.HealthHistory(clock=lambda: now[0])
        self.assertTrue(h.maybe_record({"temp_c": 50, "mem_available_mb": 600}, {"cam1": {"temp_c": 40}, "cam2": None}))
        now[0] = 5
        self.assertFalse(h.maybe_record({"temp_c": 51}, {}))                    # too soon
        now[0] = 10
        self.assertTrue(h.maybe_record({"temp_c": 52, "mem_available_mb": 590}, {"cam1": {"temp_c": 41}, "cam2": {"temp_c": 39}}))
        d = h.as_dict()
        self.assertEqual(d["hub"]["temp_c"], [50, 52])
        self.assertEqual(d["hub"]["mem_available_mb"], [600, 590])
        self.assertEqual(d["cameras"]["cam2"]["temp_c"], [None, 39])

    def test_keeps_an_hour(self):
        now = [0.0]
        h = health_history.HealthHistory(clock=lambda: now[0])
        for i in range(400):
            now[0] = i * 10
            h.maybe_record({"load": i}, {})
        self.assertEqual(len(h.as_dict()["hub"]["load"]), health_history.POINTS)
        self.assertEqual(h.as_dict()["hub"]["load"][-1], 399)


class MemoryTest(unittest.TestCase):
    def test_reads_meminfo(self):
        path = os.path.join(tempfile.mkdtemp(), "meminfo")
        with open(path, "w") as f:
            f.write("MemTotal:        1014272 kB\nMemAvailable:     491216 kB\nSwapTotal:       1013744 kB\nSwapFree:          10560 kB\n")
        self.assertEqual(hubd.memory_mb(path), (990, 479, 979))
        self.assertEqual(hubd.memory_mb("/nonexistent"), (None, None, None))


if __name__ == "__main__":
    unittest.main()
