"""Editing proxies keep the original's frame times exactly (needs ffmpeg)."""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import proxies  # noqa: E402


class ProxyTest(unittest.TestCase):
    def test_times_are_exact_and_keyframes_every_half_second(self):
        tmp = tempfile.mkdtemp()
        source = os.path.join(tmp, "cam.mov")
        # 3 s at a slightly uneven 30.011 fps, starting 1.5 ms in: like a real-timing camera file
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30.011:duration=3",
                        "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-output_ts_offset", "0.0015",
                        "-video_track_timescale", "90000", "-y", source], check=True)
        seen, checked = [], []
        result = proxies.make_proxy(source, os.path.join(tmp, "out", "cam.mp4"), poll_s=0.05, frames=len(proxies.frame_times(source)),
                                    progress=seen.append, checking=lambda: checked.append(len(seen)))
        self.assertEqual(result["frames"], len(proxies.frame_times(source)))
        self.assertTrue(seen and all(0 <= f <= 1 for f in seen) and seen == sorted(seen))   # the encode, as it goes
        self.assertGreater(seen[-1], 0.9)
        self.assertEqual(checked, [len(seen)])                                            # then the check, once
        self.assertLess(result["max_time_error_ms"], 0.5)
        keys = subprocess.run(["ffprobe", "-v", "error", "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
                               "-of", "csv=p=0", os.path.join(tmp, "out", "cam.mp4")], capture_output=True, text=True).stdout.split()
        self.assertGreaterEqual(len(keys), 6)

    def test_stop_removes_the_part_file(self):
        tmp = tempfile.mkdtemp()
        source = os.path.join(tmp, "cam.mov")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=20",
                        "-c:v", "libx264", "-preset", "ultrafast", "-y", source], check=True)
        with self.assertRaises(InterruptedError):
            proxies.make_proxy(source, os.path.join(tmp, "out", "cam.mp4"), should_stop=lambda: True, poll_s=0.05)
        self.assertEqual(os.listdir(os.path.join(tmp, "out")), [])


if __name__ == "__main__":
    unittest.main()
