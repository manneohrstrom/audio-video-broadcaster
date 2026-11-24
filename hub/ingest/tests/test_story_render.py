"""The Instagram story (story_render.py): where the 9:16 slice sits, frame by frame, and one
real render of a small synthetic take (about 5 s here) checked for size, length and framing.

The synthetic cameras show a left-to-right ramp in brightness (16 at the left edge, 235 at
the right), so the story's left column tells where the slice was taken from."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "hub/ingest"))
import edit_render  # noqa: E402
import ingest  # noqa: E402
import story_render  # noqa: E402

NS = 1_000_000_000


def shot(camera, first, end, dissolve=False):
    return edit_render.Shot(camera, first, end, dissolve)


class SlicePositionTest(unittest.TestCase):
    def test_left_middle_right(self):
        self.assertEqual(story_render.slice_left(0), 0)
        self.assertEqual(story_render.slice_left(1), 1920 - 608)
        self.assertEqual(story_render.slice_left(0.5), 656)
        self.assertEqual(story_render.slice_left(7), 1312)               # clamped

    def test_steady_between_cuts_and_gliding_over_the_dissolve(self):
        shots = [shot("cam1", 0, 30), shot("cam2", 30, 60)]
        positions = story_render.positions_per_frame(shots, {"cam1": 0, "cam2": 1}, half_dissolve=4)
        self.assertEqual(len(positions), 60)
        self.assertEqual(positions[:26], [0] * 26)
        self.assertEqual(positions[34:], [1312] * 26)
        self.assertEqual(positions[30], 656)                           # half way at the cut, as the dissolve
        glide = positions[26:34]
        self.assertEqual(glide, sorted(glide))

    def test_no_dissolve_jumps_at_the_cut(self):
        shots = [shot("cam1", 0, 10), shot("cam2", 10, 20)]
        positions = story_render.positions_per_frame(shots, {"cam2": 0}, half_dissolve=0)
        self.assertEqual(positions, [656] * 10 + [0] * 10)              # cam1 isn't framed: centred

    def test_a_dissolve_cut_holds_the_slice_then_jumps_to_the_same_picture(self):
        shots = [shot("cam1", 0, 30), shot("cam2", 30, 60, dissolve=True)]
        positions = story_render.positions_per_frame(shots, {"cam1": 0, "cam2": 1}, half_dissolve=4)
        self.assertEqual(positions, [0] * 34 + [1312] * 26)
        self.assertEqual(story_render.dissolve_shift(shots, {"cam1": 0, "cam2": 1}, 1), -1312)
        self.assertEqual(story_render.dissolve_shift(shots, {"cam1": 0, "cam2": 1}, 0), 0)
        gliding = [shot("cam1", 0, 30), shot("cam2", 30, 60)]
        self.assertEqual(story_render.dissolve_shift(gliding, {"cam1": 0, "cam2": 1}, 1), 0)

    def test_shifting_a_picture(self):
        width, height = 8, 2
        luma = np.tile(np.arange(width, dtype=np.uint8), height)
        chroma = np.tile(np.arange(width // 2, dtype=np.uint8) + 100, height // 2)
        picture = np.concatenate([luma, chroma, chroma + 50])
        right = story_render.shift_picture(picture, 2, width, height)
        self.assertEqual(list(right[:width]), [0, 0, 0, 1, 2, 3, 4, 5])
        self.assertEqual(list(right[width * height:width * height + 4]), [100, 100, 101, 102])
        left = story_render.shift_picture(picture, -4, width, height)
        self.assertEqual(list(left[:width]), [4, 5, 6, 7, 7, 7, 7, 7])
        self.assertIs(story_render.shift_picture(picture, 0, width, height), picture)

    def test_the_expression_has_one_term_per_run(self):
        positions = [0] * 5 + [100, 200] + [300] * 5
        self.assertEqual(story_render.runs(positions), [(0, 4, 0), (5, 5, 100), (6, 6, 200), (7, 11, 300)])
        self.assertEqual(story_render.x_expression(positions),
                         "100*between(n,5,5)+200*between(n,6,6)+300*between(n,7,11)")
        self.assertEqual(story_render.x_expression([0, 0]), "0")


class StoryRangeTest(unittest.TestCase):
    def edit(self, **changes):
        document = {"range_ns": [1000 * NS, 1100 * NS], "cuts": [{"at_ns": 0, "camera": "cam1"}],
                    "title": {"on": True}, "branding": True}
        document.update(changes)
        return document

    def test_its_own_range(self):
        edit = self.edit(story={"range_ns": [20 * NS, 35 * NS], "framing": {}})
        self.assertEqual(story_render.story_range_ns(edit), [20 * NS, 35 * NS])

    def test_without_one_30_s_from_the_video_start(self):
        self.assertEqual(story_render.story_range_ns(self.edit()), [0, 30 * NS])
        self.assertEqual(story_render.story_range_ns(self.edit(trim_ns=[50 * NS, 70 * NS])), [50 * NS, 70 * NS])

    def test_no_title_and_no_border(self):
        result = story_render.story_edit(self.edit(story={"range_ns": [5 * NS, 9 * NS]}))
        self.assertIsNone(result["title"])
        self.assertFalse(result["branding"])
        self.assertEqual(result["trim_ns"], [5 * NS, 9 * NS])


class EncoderGraphTest(unittest.TestCase):
    def graph(self, command):
        return command[command.index("-filter_complex") + 1]

    def test_the_slice_alone(self):
        command = edit_render.encoder_command("b.wav", 0, 90, "out.mp4", last_filters="crop=1:1")
        self.assertEqual(self.graph(command), "[0:v]crop=1:1[video]")

    def test_the_slice_after_the_effects(self):
        work = tempfile.mkdtemp()
        try:
            command = edit_render.encoder_command("b.wav", 0, 90, "out.mp4", effects={"vignette": 0.5}, work_dir=work,
                                                  last_filters="crop=1:1")
            graph = self.graph(command)
            self.assertTrue(graph.endswith("[finished];[finished]crop=1:1[video]"))
            self.assertEqual(graph.count("[video]"), 1)
        finally:
            shutil.rmtree(work, ignore_errors=True)


T0 = 1794688178_123456789
W, H = 640, 360


def make_camera(work, camera_id, reversed_ramp=False):
    """A 6 s camera file showing the ramp (dark to bright, or bright to dark), laid out as
    ingest lays them out (rewrap)."""
    ramp = np.round(16 + np.arange(W) * 219 / (W - 1)).astype(np.uint8)
    if reversed_ramp:
        ramp = ramp[::-1].copy()
    picture = np.repeat(ramp[None, :], H, axis=0)
    mkv = os.path.join(work, camera_id + ".mkv")
    encoder = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "%dx%d" % (W, H), "-framerate", "30",
         "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "4", "-pix_fmt", "yuv420p", "-g", "15", mkv],
        stdin=subprocess.PIPE)
    for _ in range(180):
        encoder.stdin.write(picture.tobytes())
    encoder.stdin.close()
    assert encoder.wait() == 0
    timecode, _, residual = ingest.grid_origin(T0)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-itsoffset", "%.6f" % (residual / 1e9), "-i", mkv,
                    "-map", "0:v", "-c", "copy", "-video_track_timescale", "90000", "-movie_timescale", "90000",
                    "-timecode", timecode, "-f", "mov", os.path.join(work, "take", camera_id + ".mov")], check=True)
    return {"first_frame_wallclock_ns": T0, "last_frame_wallclock_ns": T0 + 179 * NS // 30, "duration_s": 5.97,
            "frames": 180, "label": camera_id}


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class StoryRenderTest(unittest.TestCase):
    def test_a_dissolve_cut_mixes_the_two_slices(self):
        """cam1: the ramp, framed left; cam2: the ramp reversed, framed right. At the cut, half
        way through the dissolve, the story's left column is half cam1's left edge (16) and half
        cam2's column 1312, which is bright-to-dark: 16 + 607 x 219 / 1919. A glide would show
        both pictures' column 656 instead (125)."""
        work = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(work, "take"))
            take_doc = {"take_id": "20261124-120000", "cameras": {},
                        "manifests": {"cam1": make_camera(work, "cam1"), "cam2": make_camera(work, "cam2", reversed_ramp=True)}}
            bounce = os.path.join(work, "bounce.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=8", "-ar", "48000", bounce],
                           check=True)
            edit = {"range_ns": [T0 + NS // 10, T0 + 5 * NS], "bounce_start_ns": T0, "dissolve_s": 0.3, "look": None,
                    "cuts": [{"at_ns": 0, "camera": "cam1"}, {"at_ns": 2 * NS, "camera": "cam2", "story_dissolve": True}],
                    "story": {"range_ns": [NS // 2, 3 * NS + NS // 2], "framing": {"cam1": 0.0, "cam2": 1.0}}}
            with open(os.path.join(REPO, "hub/hubd/static/editor/looks.json")) as f:
                looks = json.load(f)
            out = os.path.join(work, "story.mp4")
            story_render.StoryRenderer(edit, take_doc, os.path.join(work, "take"), bounce, None, looks, work).run(out)
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                 capture_output=True, check=True).stdout
            frames = np.frombuffer(raw, np.uint8).reshape(-1, 1920, 1080)
            left_edge = [int(frame[960, 4]) for frame in frames]
            self.assertLess(abs(left_edge[0] - 16), 4)
            self.assertLess(abs(left_edge[45] - (16 + (16 + 607 * 219 / 1919)) / 2), 6)
            self.assertLess(abs(left_edge[-1] - (16 + 607 * 219 / 1919)), 4)          # cam2's slice, its own place
            self.assertLess(abs(left_edge[50] - left_edge[-1]), 4)                     # after the dissolve: the same picture
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_the_videos_dissolve_runs_past_the_cut(self):
        """The YouTube video (EditRenderer), same two cameras: the dissolve is centred on the cut,
        so a frame after it is still a mix (until 2026-11-24 it jumped to the new shot at the cut)."""
        work = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(work, "take"))
            take_doc = {"take_id": "20261124-120000", "cameras": {},
                        "manifests": {"cam1": make_camera(work, "cam1"), "cam2": make_camera(work, "cam2", reversed_ramp=True)}}
            bounce = os.path.join(work, "bounce.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=8", "-ar", "48000", bounce],
                           check=True)
            edit = {"range_ns": [T0 + NS // 10, T0 + 3 * NS], "bounce_start_ns": T0, "dissolve_s": 0.3, "look": None,
                    "cuts": [{"at_ns": 0, "camera": "cam1"}, {"at_ns": NS, "camera": "cam2"}]}
            with open(os.path.join(REPO, "hub/hubd/static/editor/looks.json")) as f:
                looks = json.load(f)
            out = os.path.join(work, "video.mp4")
            renderer = edit_render.EditRenderer(edit, take_doc, os.path.join(work, "take"), bounce, None, looks, work)
            renderer.run(out)
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                 capture_output=True, check=True).stdout
            frames = np.frombuffer(raw, np.uint8).reshape(-1, 1080, 1920)
            left_edge = [int(frame[540, 4]) for frame in frames]          # cam1: 16 there, cam2: 235
            cut = 30
            mixes = [(value - 16) / (235 - 16) for value in left_edge[cut - 5:cut + 6]]
            expected = [edit_render.dissolve_mix(n, cut, renderer.half_dissolve) for n in range(cut - 5, cut + 6)]
            for got, want in zip(mixes, expected):
                self.assertLess(abs(got - want), 0.04, (mixes, expected))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_a_story_is_9_16_and_framed_per_camera(self):
        work = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(work, "take"))
            take_doc = {"take_id": "20261124-120000", "cameras": {},
                        "manifests": {camera: make_camera(work, camera) for camera in ("cam1", "cam2")}}
            bounce = os.path.join(work, "bounce.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=8", "-ar", "48000", bounce],
                           check=True)
            edit = {"range_ns": [T0 + NS // 10, T0 + 5 * NS], "bounce_start_ns": T0, "dissolve_s": 0.3, "look": None,
                    "cuts": [{"at_ns": 0, "camera": "cam1"}, {"at_ns": 2 * NS, "camera": "cam2"}],
                    "title": {"on": True, "text": "Not in a story", "lines": [], "colour": "#ffffff", "duration_s": 6},
                    "branding": True,
                    "story": {"range_ns": [NS // 2, 3 * NS + NS // 2], "framing": {"cam1": 0.0, "cam2": 1.0}}}
            with open(os.path.join(REPO, "hub/hubd/static/editor/looks.json")) as f:
                looks = json.load(f)
            out = os.path.join(work, "story.mp4")
            renderer = story_render.StoryRenderer(edit, take_doc, os.path.join(work, "take"), bounce, None, looks, work,
                                                  brand_dir="/nowhere")
            self.assertIsNone(renderer.brand_dir)
            self.assertIsNone(renderer.title)
            renderer.run(out)

            info = edit_render.render.probe_output(out)
            self.assertEqual(info["size"], (1080, 1920))
            self.assertEqual(info["frames"], 90)                       # 3 s
            self.assertEqual(info["rate"], "30/1")
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                 capture_output=True, check=True).stdout
            frames = np.frombuffer(raw, np.uint8).reshape(-1, 1920, 1080)
            # The ramp at the slice's left edge: cam1 from the left edge, cam2 from 1312 px.
            left_edge = [int(frame[960, 4]) for frame in frames]
            expected_cam2 = 16 + 1312 * 219 / 1919
            self.assertLess(abs(left_edge[0] - 16), 4)
            self.assertLess(abs(left_edge[-1] - expected_cam2), 4)
            self.assertLess(abs(left_edge[45] - (16 + expected_cam2) / 2), 6)      # the cut, half way in the glide
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
