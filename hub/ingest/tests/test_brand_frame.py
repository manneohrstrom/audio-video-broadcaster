"""The border and logo (the editor's "Border & logo" switch) in the render: the last layer,
on top of the look's effects, so the grain doesn't touch it. Runs the real encoder command
(needs ffmpeg) on a few grey frames."""
import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "hub/ingest"))
import edit_render  # noqa: E402

W, H = 1920, 1080
FRAMES = 3
BRAND_FRAME = os.path.join(REPO, "hub/hubd/static/editor/brand/brand-frame.png")
GREY = 100      # luma of the test picture


def render(effects, brand_frame, folder):
    bounce = os.path.join(folder, "bounce.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1", bounce],
                   check=True)
    out = os.path.join(folder, "out.mp4")
    command = edit_render.encoder_command(bounce, 0.0, FRAMES, out, effects, folder, preset="ultrafast", crf="10",
                                          brand_frame=brand_frame)
    frame = np.concatenate([np.full(W * H, GREY, np.uint8), np.full(W * H // 2, 128, np.uint8)])
    subprocess.run(command, input=frame.tobytes() * FRAMES, check=True)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W)


class BrandFrameTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def test_border_and_logo_on_top_of_the_grain(self):
        luma = render({"grain": 1.0}, BRAND_FRAME, self.folder)
        border = np.concatenate([luma[:20].ravel(), luma[-20:].ravel(), luma[:, :20].ravel(), luma[:, -20:].ravel()])
        self.assertGreaterEqual(border.min(), 230)                   # white (235), no grain in it
        middle = luma[400:600, 800:1100]
        self.assertGreater(middle.std(), 2)                          # the grain is there underneath
        logo_box = luma[H - 24 - 32 - 56:H - 24 - 32, W - 24 - 32 - 345:W - 24 - 32]
        self.assertGreater((logo_box > 200).mean(), 0.1)             # white letters
        self.assertGreater((logo_box < 150).mean(), 0.3)             # picture between them

    def test_without_effects_only_the_border_and_logo_change(self):
        plain = render({}, None, self.folder).astype(int)
        branded = render({}, BRAND_FRAME, self.folder).astype(int)
        self.assertLess(abs(plain[:20].mean() - plain[500, 960]), 1)  # off: the edge is picture
        self.assertGreaterEqual(branded[:20].min(), 230)
        self.assertLessEqual(np.abs(branded[100:900, 100:1400] - plain[100:900, 100:1400]).max(), 1)   # x264 noise only


if __name__ == "__main__":
    unittest.main()
