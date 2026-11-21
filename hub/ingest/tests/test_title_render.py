"""The title sequence in the render (title_render.py) through the real encoder command.
Needs Pillow (python3-pil on the hub; skipped where it's missing)."""
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
try:
    import PIL  # noqa: F401
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

TITLE = {"on": True, "text": "Blue in Green", "lines": ["Anna Berg: piano", "100% Ben's bass, \"live\""],
         "colour": "#e8b15a", "start_s": 0, "duration_s": 3.0}
FPS = 30


def render_frames(seconds, title, folder, wanted_s):
    """A grey picture through the encoder with this title (no look); the frames at the times
    in `wanted_s` back as uint8 RGB (only those: the hub has 1 GB)."""
    count = int(seconds * FPS)
    bounce = os.path.join(folder, "bounce.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds), bounce],
                   check=True)
    out = os.path.join(folder, "title.mp4")
    command = edit_render.encoder_command(bounce, 0.0, count, out, {}, folder, preset="ultrafast", crf="8",
                                          title=title, fonts_dir=FONTS)
    subprocess.run(command, input=to_yuv([grey(120)]) * count, check=True)
    wanted = [round(s * FPS) for s in wanted_s]
    pick = "+".join("eq(n,%d)" % n for n in wanted)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-vf", "select='%s'" % pick, "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return dict(zip(wanted_s, np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3).astype(int)))


def lit_rows(frame, threshold=60):
    rows = np.where((frame.sum(axis=2) > threshold).any(axis=1))[0]
    return rows.min(), rows.max()


@unittest.skipUnless(HAS_PILLOW, "no Pillow here")
class TitleSequenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.mkdtemp()                         # videos: /tmp is RAM on the hub
        cls.frames = render_frames(13, TITLE, cls.folder, (0.0, 1.5, 3.1, 5.0, 6.93, 10.0, 12.9))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.folder, True)

    def test_it_starts_from_black(self):
        self.assertLess(self.frames[0.0].max(), 30)

    def test_brockley_live_in_cream_on_black_centred(self):
        frame = self.frames[1.5]
        lit = frame.sum(axis=2) > 300
        top, bottom = np.where(lit.any(axis=1))[0][[0, -1]]
        baseline = title_render.card_baseline(title_render.CARD_SIZE, title_render.BRAND_CAP_HEIGHT)
        self.assertAlmostEqual(bottom, baseline, delta=3)
        self.assertAlmostEqual(top, baseline - title_render.BRAND_CAP_HEIGHT * title_render.CARD_SIZE, delta=4)
        columns = np.where(lit.any(axis=0))[0]
        self.assertLess(columns[-1] - columns[0], 700)          # Elastic, no letter spacing (Outfit spaced: ~1000)
        columns = np.where(lit.any(axis=0))[0]
        self.assertAlmostEqual((columns[0] + columns[-1]) / 2, W / 2, delta=6)
        red, green, blue = np.median(frame[lit], axis=0)
        self.assertGreater(red, 200)
        self.assertGreater(red, blue + 10)                      # cream, not white
        self.assertLess(frame[~lit].max(), 140)                 # black around it (anti-aliased edges aside)

    def test_black_between_the_cards_then_the_title_in_its_colour(self):
        self.assertLess(self.frames[3.1].max(), 40)             # 0.2 s of black between them
        frame = self.frames[5.0]
        lit = frame.sum(axis=2) > 300
        red, green, blue = np.median(frame[lit], axis=0)
        self.assertGreater(red, blue + 80)                      # amber
        self.assertLess(self.frames[6.93].max(), 40)            # faded out, the video not in yet

    def test_the_video_with_the_strip_then_without(self):
        frame = self.frames[10.0]
        top, _ = title_render.strip_layout(title_render.lines_below(TITLE))
        self.assertAlmostEqual(frame[400, 960].mean(), 120, delta=6)           # the picture
        self.assertAlmostEqual(frame[top + 4, 40].mean(), 60, delta=8)         # the strip: 50 % black
        self.assertGreater(frame[top:].max(), 200)                             # its text
        text_columns = np.where((frame[top:].sum(axis=2) > 500).any(axis=0))[0]
        self.assertAlmostEqual(text_columns[0], title_render.DETAIL_LEFT, delta=4)   # left-aligned
        self.assertAlmostEqual(self.frames[12.9][top + 4, 40].mean(), 120, delta=6)   # gone


class TitleGraphTest(unittest.TestCase):
    def test_a_title_that_is_off_draws_nothing(self):
        command = edit_render.encoder_command("b.wav", 0, 10, "o.mp4", {}, ".", title=dict(TITLE, on=False))
        self.assertNotIn("-filter_complex", command)

    def test_the_logo_waits_for_the_strip(self):
        self.assertAlmostEqual(title_render.logo_start_s(TITLE), 7.95 + 0.7 + 3 + 0.7 + 1)
        self.assertAlmostEqual(title_render.logo_start_s(dict(TITLE, lines=[])), 6.95 + 1.5 + 1)
        self.assertEqual(title_render.logo_start_s(None), 5)


if __name__ == "__main__":
    unittest.main()
