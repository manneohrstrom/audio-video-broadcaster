"""The video's title in the render (title_render.py) through the real encoder command. Needs an
ffmpeg with drawtext (the hub's has it; skipped where it's missing, e.g. a Mac's Homebrew ffmpeg)."""
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
import title_render  # noqa: E402
from tests.test_cinematic_effects import grey, to_yuv, H, W  # noqa: E402

FONTS = os.path.join(REPO, "hub/hubd/static/shared/vendor/fonts")
HAS_DRAWTEXT = " drawtext " in subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout

TITLE = {"on": True, "text": "Brockley Live", "lines": ["Anna Berg: piano", "100% Ben's bass, \"live\""],
         "colour": "#e8b15a", "start_s": 1.0, "duration_s": 2.0}


def render_frames(count, title, folder, wanted):
    """A grey picture, `count` frames, through the encoder with this title (no look); the
    frames numbered in `wanted` back as uint8 RGB (only those: the hub has 1 GB)."""
    bounce = os.path.join(folder, "bounce.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "4", bounce],
                   check=True)
    out = os.path.join(folder, "title.mp4")
    command = edit_render.encoder_command(bounce, 0.0, count, out, {}, folder, preset="ultrafast", crf="8",
                                          title=title, fonts_dir=FONTS)
    subprocess.run(command, input=to_yuv([grey(40)]) * count, check=True)
    pick = "+".join("eq(n,%d)" % n for n in wanted)        # quoted below: the commas are safe
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-vf", "select='%s'" % pick, "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return dict(zip(wanted, np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)))


@unittest.skipUnless(HAS_DRAWTEXT, "this ffmpeg has no drawtext")
class TitleRenderTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)     # videos: /tmp is RAM on the hub

    def test_it_dissolves_in_stays_and_is_gone(self):
        wanted = (0, 29, 32, 37, 45, 60, 84, 90, 99)
        video = render_frames(100, TITLE, self.folder, wanted)       # 3.3 s; on from 1.0 to 3.0 s
        text = {n: int((np.abs(video[n].astype(int) - 40).sum(axis=2) > 6).sum()) for n in wanted}
        lit = {n: int(np.abs(video[n].astype(int) - 40).sum()) for n in wanted}
        for n in (0, 29, 90, 99):
            self.assertLess(text[n], 50, "frame %d should show no title" % n)
        self.assertLess(lit[32], lit[37])                             # dissolving in
        self.assertAlmostEqual(lit[45], lit[60], delta=lit[60] * 0.02)   # fully on
        self.assertLess(lit[84], lit[60])                             # dissolving out

    def test_rows_sit_on_their_baselines_in_the_colour(self):
        video = render_frames(70, TITLE, self.folder, (60,))[60].astype(int)
        lit = np.abs(video - 40).sum(axis=2) > 60
        rows = np.where(lit.any(axis=1))[0]
        layout = title_render.layout(TITLE)
        for text, _, size, baseline in layout:
            self.assertTrue(lit[baseline - int(size * 0.75):baseline + 2].any(), text)
        self.assertGreater(rows.min(), layout[0][3] - 84)
        self.assertLess(rows.max(), layout[-1][3] + 15)             # descenders, the shadow
        red, green, blue = np.median(video[lit], axis=0)            # amber, not grey or white
        self.assertGreater(red, blue + 60)

    def test_a_title_that_is_off_draws_nothing(self):
        command = edit_render.encoder_command("b.wav", 0, 10, "o.mp4", {}, self.folder, title=dict(TITLE, on=False))
        self.assertNotIn("-filter_complex", command)


if __name__ == "__main__":
    unittest.main()
