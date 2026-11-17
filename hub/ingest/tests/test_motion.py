"""Movement per camera (motion.py) on two made-up proxies: one moving, one still."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import motion  # noqa: E402


def make_video(path, source, seconds=4):
    """A 960x540 30 fps H.264 file with a keyframe every 15 frames, like an editing proxy."""
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "%s%ssize=960x540:rate=30:duration=%d" % (source, ":" if "=" in source else "=", seconds),
                    "-c:v", "libx264", "-g", "15", "-keyint_min", "15", "-sc_threshold", "0",
                    "-pix_fmt", "yuv420p", path], check=True)


class Motion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.moving = os.path.join(cls.dir.name, "moving.mp4")
        cls.still = os.path.join(cls.dir.name, "still.mp4")
        make_video(cls.moving, "testsrc2")          # a moving test pattern
        make_video(cls.still, "color=c=gray")

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_moving_reads_higher_than_still(self):
        moving, still = motion.measure(self.moving), motion.measure(self.still)
        self.assertGreater(sum(moving["motion"]) / len(moving["motion"]), 1.0)
        self.assertLess(max(still["motion"]), 0.5)

    def test_one_reading_per_keyframe_on_file_time(self):
        result = motion.measure(self.moving)
        self.assertEqual(len(result["times_s"]), len(result["motion"]))
        self.assertEqual(len(result["motion"]), 7)               # 8 keyframes in 4 s, 7 changes
        steps = [b - a for a, b in zip(result["times_s"], result["times_s"][1:])]
        for step in steps:
            self.assertAlmostEqual(step, 0.5, delta=0.01)
        self.assertAlmostEqual(result["times_s"][0], 0.5, delta=0.01)

    def test_written_atomically(self):
        out = os.path.join(self.dir.name, "cam1.motion.json")
        motion.write_json(out, motion.measure(self.still))
        self.assertTrue(os.path.exists(out))
        self.assertFalse(os.path.exists(out + ".tmp"))


if __name__ == "__main__":
    unittest.main()
