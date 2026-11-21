"""The border and logo (the editor's "Border & logo" switch) in the render: the last layers,
on top of the look's effects, so the grain doesn't touch them. The border is there from the
first frame; the logo fades in (title_render.logo_start_s). Runs the real encoder command
(needs ffmpeg) on grey frames."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "hub/ingest"))
import edit_render  # noqa: E402

W, H = 1920, 1080
BRAND_DIR = os.path.join(REPO, "hub/hubd/static/editor/brand")
FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../hubd/static/shared/vendor/fonts")
GREY = 100      # luma of the test picture
LOGO_BOX = (slice(H - 24 - 32 - 56, H - 24 - 32), slice(W - 24 - 32 - 345, W - 24 - 32))


def render(effects, brand_dir, folder, frames=3, wanted=(0,), title=None):
    """Grey frames through the encoder; the frames numbered in `wanted`, as luma."""
    bounce = os.path.join(folder, "bounce.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "13", bounce],
                   check=True)
    out = os.path.join(folder, "out.mp4")
    command = edit_render.encoder_command(bounce, 0.0, frames, out, effects, folder, preset="ultrafast", crf="10",
                                          brand_dir=brand_dir, title=title, fonts_dir=FONTS)
    frame = np.concatenate([np.full(W * H, GREY, np.uint8), np.full(W * H // 2, 128, np.uint8)])
    subprocess.run(command, input=frame.tobytes() * frames, check=True)
    pick = "+".join("eq(n,%d)" % n for n in wanted)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-vf", "select='%s'" % pick, "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    frames_out = np.frombuffer(raw, np.uint8).reshape(-1, H, W)
    assert len(frames_out) == len(wanted)
    return dict(zip(wanted, frames_out))


def lit(luma):
    """How much of the logo's box is white letters."""
    return (luma[LOGO_BOX] > 200).mean()


class BrandFrameTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)     # videos: /tmp is RAM on the hub

    def test_border_from_the_start_and_the_logo_fades_in_at_5_s(self):
        # No title: the logo fades in from 5.0 s over 1 s (frames 150 to 180).
        frames = render({"grain": 1.0}, BRAND_DIR, self.folder, frames=200, wanted=(0, 149, 165, 185, 199))
        for n, luma in frames.items():
            border = np.concatenate([luma[:20].ravel(), luma[-20:].ravel(), luma[:, :20].ravel(), luma[:, -20:].ravel()])
            self.assertGreaterEqual(border.min(), 230, n)            # white (235), no grain in it
        self.assertGreater(frames[0][400:600, 800:1100].std(), 2)   # the grain is there underneath
        self.assertLess(lit(frames[0]), 0.01)                       # no logo yet
        self.assertLess(lit(frames[149]), 0.01)
        letters = frames[199][LOGO_BOX] > 200
        half = frames[165][LOGO_BOX][letters].mean()
        self.assertTrue(GREY + 30 < half < 220, half)               # about half way through the fade
        self.assertGreater(lit(frames[185]), 0.1)                   # white letters
        self.assertGreater((frames[199][LOGO_BOX] < 150).mean(), 0.3)   # picture between them

    def test_after_a_title_the_logo_waits(self):
        title = {"on": True, "text": "T", "lines": [], "colour": "#ffffff", "start_s": 0, "duration_s": 3.0}
        # No names: the sequence is over when the video is in (8.45 s), the logo from 9.45 s.
        frames = render({}, BRAND_DIR, self.folder, frames=315, wanted=(270, 314), title=title)
        self.assertLess(lit(frames[270]), 0.01)
        self.assertGreater(lit(frames[314]), 0.1)

    def test_without_effects_only_the_border_and_logo_change(self):
        plain = render({}, None, self.folder, frames=200, wanted=(199,))[199].astype(int)
        branded = render({}, BRAND_DIR, self.folder, frames=200, wanted=(199,))[199].astype(int)
        self.assertLess(abs(plain[:20].mean() - plain[500, 960]), 1)  # off: the edge is picture
        self.assertGreaterEqual(branded[:20].min(), 230)
        self.assertLessEqual(np.abs(branded[100:900, 100:1400] - plain[100:900, 100:1400]).max(), 1)   # x264 noise only


if __name__ == "__main__":
    unittest.main()
