"""Camera moves (camera_move.py): the geometry, the ffmpeg filter that does it in the render,
and how the render plans moved shots (trimmed, plan_shots). The numbers in GeometryTest are
the same as in hub/hubd/tests/editor/camera-move.test.mjs (the preview's half)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import numpy as np

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "hub/ingest"))
import camera_move  # noqa: E402
import edit_render  # noqa: E402
import ingest  # noqa: E402

NS = 1_000_000_000
FPS = 30


class CleanMoveTest(unittest.TestCase):
    def test_kinds_and_amounts(self):
        self.assertEqual(camera_move.clean_move({"kind": "in", "amount": 0.4}), {"kind": "in", "amount": 0.4})
        self.assertEqual(camera_move.clean_move({"kind": "left"}), {"kind": "left", "amount": 0.5})
        self.assertEqual(camera_move.clean_move({"kind": "out", "amount": 7}), {"kind": "out", "amount": 1.0})
        self.assertEqual(camera_move.clean_move({"kind": "out", "amount": True}), {"kind": "out", "amount": 0.5})
        for nothing in (None, {}, {"kind": "none"}, {"kind": "zoom"}, "in"):
            self.assertIsNone(camera_move.clean_move(nothing))


class GeometryTest(unittest.TestCase):
    def assertRect(self, rect, expected):
        for got, want in zip(rect, expected):
            self.assertAlmostEqual(got, want, places=6)

    def test_push_in_over_the_whole_visible_shot(self):
        move = {"kind": "in", "amount": 0.5}             # 6 % (a 10.3 s shot allows 15 %)
        self.assertRect(camera_move.rect_at(move, 0, 10, 0.3, -0.15), (0, 0, 1, 1))
        end = 1 / 1.06
        self.assertRect(camera_move.rect_at(move, 0, 10, 0.3, 10.15), (0.5 - end / 2, 0.5 - end / 2, end, end))
        half = 1 / 1.03
        self.assertRect(camera_move.rect_at(move, 0, 10, 0.3, 5.0), (0.5 - half / 2, 0.5 - half / 2, half, half))
        # held before and after the shot is on screen
        self.assertRect(camera_move.rect_at(move, 0, 10, 0.3, 12), camera_move.rect_at(move, 0, 10, 0.3, 10.15))

    def test_pull_out_is_push_in_backwards(self):
        a = camera_move.rect_at({"kind": "out", "amount": 1}, 2, 12, 0, 3)
        b = camera_move.rect_at({"kind": "in", "amount": 1}, 2, 12, 0, 11)
        self.assertRect(a, b)

    def test_a_short_shot_moves_less_not_faster(self):
        zoom0, zoom1, _, _ = camera_move.path({"kind": "in", "amount": 1}, 2.0)
        self.assertAlmostEqual(zoom1 - zoom0, 0.03)
        zoom0, zoom1, _, _ = camera_move.path({"kind": "in", "amount": 1}, 20.0)
        self.assertAlmostEqual(zoom1 - zoom0, 0.12)

    def test_drift_crosses_the_room_at_a_constant_zoom(self):
        move = {"kind": "right", "amount": 0.5}
        size = 1 / 1.06
        self.assertRect(camera_move.rect_at(move, 0, 10, 0, 0), (0, 0.5 - size / 2, size, size))
        self.assertRect(camera_move.rect_at(move, 0, 10, 0, 10), (1 - size, 0.5 - size / 2, size, size))
        left = camera_move.rect_at({"kind": "left", "amount": 0.5}, 0, 10, 0, 0)
        self.assertAlmostEqual(left[0], 1 - size)


def evaluate(expression, frame_in, width, height):
    """An ffmpeg expression of perspective's (in, W, H, clip) evaluated in Python."""
    text = expression.replace("clip(", "_clip(").replace("in", "_in")
    return eval(text, {"_clip": lambda v, lo, hi: min(hi, max(lo, v)), "_in": frame_in, "W": width, "H": height})


class PerspectiveFilterTest(unittest.TestCase):
    def test_corners_follow_rect_at_frame_by_frame(self):
        """The decoder of a shot (frames 300..600, dissolve 9 frames) started at frame 296:
        its k-th frame (perspective's in = k + 1) is song frame 296 + k."""
        move = {"kind": "in", "amount": 0.8}
        text = camera_move.perspective_filter(move, 300, 600, 9, 296, FPS)
        corners = dict(re.findall(r"(x[0-3]|y[0-3])='([^']*)'", text))
        self.assertIn("interpolation=cubic", text)
        for k in (0, 1, 150, 308):
            seconds = (296 + k) / FPS
            left, top, w, h = camera_move.rect_at(move, 10.0, 20.0, 9 / FPS, seconds)
            self.assertAlmostEqual(evaluate(corners["x0"], k + 1, 1920, 1080), left * 1920, places=2)
            self.assertAlmostEqual(evaluate(corners["y0"], k + 1, 1920, 1080), top * 1080, places=2)
            self.assertAlmostEqual(evaluate(corners["x3"], k + 1, 1920, 1080), (left + w) * 1920, places=2)
            self.assertAlmostEqual(evaluate(corners["y3"], k + 1, 1920, 1080), (top + h) * 1080, places=2)

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_ffmpeg_takes_it_and_a_move_that_hasnt_started_changes_nothing(self):
        """The filter parses, and on the shot's first visible frame (no zoom yet) the picture is the same."""
        move_filter = camera_move.perspective_filter({"kind": "in", "amount": 1}, 0, 300, 0, 0, FPS)

        def frames(extra):
            vf = "format=yuv420p" + ("," + extra if extra else "") + ",format=gray"
            return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30", "-frames:v", "2",
                                   "-vf", vf, "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        plain, moved = frames(None), frames(move_filter)
        size = 640 * 360
        first = sum(abs(a - b) for a, b in zip(plain[:size], moved[:size])) / size
        second = sum(abs(a - b) for a, b in zip(plain[size:], moved[size:])) / size
        self.assertLess(first, 0.5)
        self.assertGreater(second, first)


class RenderPlanTest(unittest.TestCase):
    def edit(self, **changes):
        edit = {"range_ns": [1000 * NS, 1060 * NS], "dissolve_s": 0.3,
                "cuts": [{"at_ns": 0, "camera": "cam1"},
                         {"at_ns": 10 * NS, "camera": "cam2", "move": {"kind": "in", "amount": 0.5}},
                         {"at_ns": 30 * NS, "camera": "cam3", "move": {"kind": "left", "amount": 1}}]}
        edit.update(changes)
        return edit

    def take(self):
        cameras = {c: {"id": c, "kind": "pi", "start_ns": 990 * NS, "end_ns": 1070 * NS, "origin_ns": 990 * NS}
                   for c in ("cam1", "cam2", "cam3")}
        return cameras

    def plan(self, edit):
        original = edit_render.render.cameras
        edit_render.render.cameras = lambda take_doc, include_phones=True: self.take()
        try:
            return edit_render.plan_shots(edit, {}, edit["dissolve_s"])[0]
        finally:
            edit_render.render.cameras = original

    def test_moves_go_on_their_shots(self):
        shots = self.plan(self.edit())
        self.assertEqual([s.move for s in shots], [None, {"kind": "in", "amount": 0.5}, {"kind": "left", "amount": 1.0}])
        self.assertEqual(shots[1].move_span, (300, 900))
        self.assertEqual(shots[2].move_span, (900, 1800))

    def test_a_trimmed_shot_keeps_its_whole_length_for_the_move(self):
        """The video starts at 15 s and ends at 50 s: cam2's shot (10-30 s) starts the video, cam3's
        (30-60 s) ends it; their moves are still timed over the whole shots, as in the preview."""
        trimmed = edit_render.trimmed(self.edit(trim_ns=[15 * NS, 50 * NS]))
        shots = self.plan(trimmed)
        self.assertEqual([s.camera_id for s in shots], ["cam2", "cam3"])
        self.assertEqual(shots[0].move_span, (-150, 450))
        self.assertEqual(shots[1].move_span, (450, 1350))
        self.assertEqual((shots[0].first_frame, shots[1].end_frame), (0, 1050))


T0 = 1794688178_000000000
SQUARE = 100                  # px of the 640x360 source: 300 px wide at 1080p


def make_still_camera(work, camera_id, length_s):
    """take/<camera>.mov as ingest writes it (real timing, 90 kHz), from T0: a white square in
    the middle of grey. Returns (manifest, camera state)."""
    mkv = os.path.join(work, camera_id + ".mkv")
    picture = "color=c=gray:s=640x360:r=30,drawbox=x=%d:y=%d:w=%d:h=%d:color=white:t=fill" % (
        320 - SQUARE // 2, 180 - SQUARE // 2, SQUARE, SQUARE)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", picture, "-t", str(length_s),
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", "-pix_fmt", "yuv420p", "-g", "15", mkv], check=True)
    tc, _origin, residual = ingest.grid_origin(T0)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-itsoffset", "%.6f" % (residual / 1e9), "-i", mkv,
                    "-map", "0:v", "-c", "copy", "-video_track_timescale", "90000", "-movie_timescale", "90000",
                    "-timecode", tc, "-f", "mov", os.path.join(work, "take", camera_id + ".mov")], check=True)
    frames = int(length_s * 30)
    manifest = {"first_frame_wallclock_ns": T0, "last_frame_wallclock_ns": T0 + (frames - 1) * NS // 30,
                "duration_s": (frames - 1) / 30, "frames": frames, "label": camera_id,
                "sync_role": "server" if camera_id == "cam1" else "client"}
    return manifest, {"rough": [], "timing": "real"}


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class RenderTest(unittest.TestCase):
    """A real render (1080p, ~2 s): cam1 still for 2 s, then cam2 pushing in (amount 1) to the end
    at 8 s. The square's width in every frame is the zoom rect_at gives, within a pixel and a half."""

    def test_the_render_zooms_as_rect_at_says(self):
        with tempfile.TemporaryDirectory() as work:
            os.makedirs(os.path.join(work, "take"))
            manifests, states = {}, {}
            for camera_id in ("cam1", "cam2"):
                manifests[camera_id], states[camera_id] = make_still_camera(work, camera_id, 10)
            bounce = os.path.join(work, "bounce.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=10", "-ar", "48000", bounce], check=True)
            move = {"kind": "in", "amount": 1.0}
            edit = {"range_ns": [T0, T0 + 8 * NS], "bounce_start_ns": T0, "dissolve_s": 0.3, "look": None,
                    "cuts": [{"at_ns": 0, "camera": "cam1"}, {"at_ns": 2 * NS, "camera": "cam2", "move": move}]}
            with open(os.path.join(REPO, "hub/hubd/static/editor/looks.json")) as f:
                looks = json.load(f)
            out = os.path.join(work, "out.mp4")
            take_doc = {"take_id": "20261128-120000", "manifests": manifests, "cameras": states}
            edit_render.EditRenderer(edit, take_doc, os.path.join(work, "take"), bounce, None, looks, work).run(out)
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                                 capture_output=True, check=True).stdout
        frames = np.frombuffer(raw, np.uint8).reshape(-1, 1080, 1920)
        self.assertEqual(len(frames), 240)
        for f in (0, 30, 50, 75, 120, 180, 239):
            width = int((frames[f][540] > 190).sum())
            if f < 55:                       # cam1 (the dissolve is the same picture, still)
                expected = 300.0
            else:
                expected = 300.0 / camera_move.rect_at(move, 2.0, 8.0, 0.3, f / FPS)[2]
            self.assertAlmostEqual(width, expected, delta=1.5, msg="frame %d" % f)
        self.assertGreater((frames[239][540] > 190).sum(), 325)          # 9.2 % in by the end


if __name__ == "__main__":
    unittest.main()
