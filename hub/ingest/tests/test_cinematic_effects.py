"""The cinematic effects (2026-11-17) in the render: each one through the real encoder
command (needs ffmpeg) on a few synthetic 1080p frames, checked for what it should do."""
import functools
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "hub/ingest"))
import edit_render  # noqa: E402
import look_render  # noqa: E402

W, H = 1920, 1080


def to_yuv(rgb_frames):
    """RGB uint8 frames (H, W, 3) -> raw yuv420p bytes (Rec. 709, TV range) for the encoder."""
    raw = b"".join(frame.tobytes() for frame in rgb_frames)
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
                           "-i", "-", "-vf", "scale=out_color_matrix=bt709:out_range=tv", "-pix_fmt", "yuv420p",
                           "-f", "rawvideo", "-"], input=raw, capture_output=True, check=True).stdout


def render(rgb_frames, effects, folder, downbeats_s=(), audio_start_s=0.0):
    """The frames through the encoder with these effects; the result as RGB frames."""
    bounce = os.path.join(folder, "bounce.wav")
    if not os.path.exists(bounce):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "3", bounce],
                       check=True)
    out = os.path.join(folder, "out.mp4")
    command = edit_render.encoder_command(bounce, audio_start_s, len(rgb_frames), out, effects, folder,
                                          preset="ultrafast", crf="8", downbeats_s=downbeats_s)
    subprocess.run(command, input=to_yuv(rgb_frames), check=True)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3).astype(int)


def grey(level):
    return np.full((H, W, 3), level, np.uint8)


def disc(level=255, radius=40, centre=(960, 540), background=10):
    frame = grey(background)
    yy, xx = np.ogrid[:H, :W]
    frame[(yy - centre[1]) ** 2 + (xx - centre[0]) ** 2 <= radius ** 2] = level
    return frame


def texture():
    rng = np.random.default_rng(1)
    small = rng.integers(40, 220, (H // 8, W // 8, 1), dtype=np.uint8)
    return np.repeat(np.repeat(np.repeat(small, 8, 0), 8, 1), 3, 2)


class CinematicEffectsTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)     # videos: /tmp is RAM on the hub

    def test_letterbox_bars_are_black(self):
        frames = render([grey(128)] * 3, {"letterbox": 1.0}, self.folder)
        bar = look_render.letterbox_bar_px(1.0)
        self.assertEqual(bar, 138)                                   # 2.39:1 on 1080
        self.assertLess(frames[0, :bar - 2].max(), 6)
        self.assertLess(frames[0, H - bar + 2:].max(), 6)
        self.assertGreater(frames[0, bar + 4:H - bar - 4].mean(), 100)

    def test_halation_is_a_red_orange_halo(self):
        plain = render([disc()] * 3, {}, self.folder)[1]
        halo = render([disc()] * 3, {"halation": 1.0}, self.folder)[1]
        ring = halo[540, 1010:1040] - plain[540, 1010:1040]          # just outside the disc
        red, green, blue = ring.mean(axis=0)
        self.assertGreater(red, 6)          # 8.5 on ffmpeg 9 (Mac), 7.8 on 7.1 (the hub)
        self.assertGreater(red, green * 1.8)
        self.assertGreater(green, blue)

    def test_clarity_adds_contrast_at_edges(self):
        frame = grey(80)
        frame[:, W // 2:] = 170
        plain = render([frame] * 3, {}, self.folder)[1, 540, :, 1]
        clear = render([frame] * 3, {"clarity": 1.0}, self.folder)[1, 540, :, 1]
        self.assertLess(clear[W // 2 - 20:W // 2].min(), plain[W // 2 - 20:W // 2].min() - 5)     # darker side darker
        self.assertGreater(clear[W // 2:W // 2 + 20].max(), plain[W // 2:W // 2 + 20].max() + 5)  # lighter side lighter
        self.assertLess(abs(clear[200] - plain[200]), 3)                                         # flat areas untouched

    def test_light_trails_follow_a_moving_light(self):
        frames = [disc(centre=(400 + 60 * k, 540), radius=20) for k in range(6)]
        out = render(frames, {"trails": 1.0}, self.folder)
        self.assertGreater(out[5, 540, 400:460].mean(), 120)          # where the light was 5 frames ago

    def test_projector_weaves_a_still_picture(self):
        still = [texture()] * 8
        plain = render(still, {}, self.folder)
        weave = render(still, {"projector": 1.0}, self.folder)
        self.assertLess(np.abs(plain[3] - plain[4]).mean(), 1)
        moved = sum(np.abs(weave[k] - weave[k + 1])[100:-100, 100:-100].mean() > 5 for k in range(7))
        self.assertGreaterEqual(moved, 3)

    def test_projector_matches_the_preview_on_frame_0(self):
        # The shader (look-renderer.js) at song frame 0: weave dx = round(1.6 x 0.4 sin 1.3) = 1
        # pixel, dy = round(0.6 sin 0.4) = 0, flicker + 0.035 x 255 x 0.4 sin 0.8 RGB levels.
        # Caught 2026-11-18: crop moved in even steps only (no exact=1) and eq's brightness
        # flicker did nothing on a dark picture (now the flicker is added in RGB, like the shader).
        still = [texture()] * 2
        plain = render(still, {}, self.folder)[0].astype(float)
        weave = render(still, {"projector": 1.0}, self.folder)[0].astype(float)
        inner = (slice(100, -100), slice(100, -100))
        flicker = round(0.035 * 255 * 0.4 * math.sin(0.8))       # whole RGB levels, as geq writes them
        for dx in (0, 1, 2):
            shifted = np.roll(plain, -dx, axis=1)
            error = np.abs(weave[inner] - flicker - shifted[inner]).mean()
            if dx == 1:
                self.assertLess(error, 1.5)
            else:
                self.assertGreater(error, 5)
        self.assertAlmostEqual((weave[inner] - np.roll(plain, -1, axis=1)[inner]).mean(), flicker, delta=0.6)

    def test_an_effect_keeps_the_colours(self):
        # Any effect goes through RGB; with nothing to do it must give back the same picture.
        # Caught 2026-11-18: the YUV -> RGB step assumed Rec. 601, a warm red lost 11 Y levels.
        warm = np.zeros((H, W, 3), np.uint8)
        warm[:] = (192, 48, 32)
        plain = render([warm] * 2, {}, self.folder)[0]
        through_rgb = render([warm] * 2, {"letterbox": 0.0001}, self.folder)[0]      # bars of 0 px
        self.assertLess(np.abs(plain - through_rgb).max(), 3)

    def test_camera_colours_reach_the_video_unchanged(self):
        # An untagged camera file (Rec. 709, as Chrome shows it in the preview) through the real
        # decoder (with a colour table) and encoder: the same YUV in the finished video. Caught
        # 2026-11-18: ffmpeg read untagged video as Rec. 601, a warm red came out much darker.
        yuv = (83, 106, 192)                               # RGB (193, 48, 32) in Rec. 709
        frame = bytes([yuv[0]]) * (W * H) + bytes([yuv[1]]) * (W * H // 4) + bytes([yuv[2]]) * (W * H // 4)
        camera = os.path.join(self.folder, "cam.mov")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % (W, H),
                        "-r", "30", "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "1", camera],
                       input=frame * 4, check=True)
        identity = os.path.join(self.folder, "identity.cube")
        with open(identity, "w") as f:
            f.write("LUT_3D_SIZE 17\n")
            f.writelines("%.6f %.6f %.6f\n" % (r / 16, g / 16, b / 16)
                         for b in range(17) for g in range(17) for r in range(17))
        self.assertEqual(edit_render.source_colour(camera), ("bt709", "tv"))      # untagged: as Chrome
        decoded = subprocess.run(edit_render.decoder_command(camera, 0.0, 2, identity, "yuv420p",
                                                             colour=edit_render.source_colour(camera)),
                                 capture_output=True, check=True).stdout
        for effects in ({}, {"grain": 0.0001}):            # without and with the effects' RGB step
            bounce = os.path.join(self.folder, "bounce.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "1",
                            bounce], check=True)
            out = os.path.join(self.folder, "colour.mp4")
            subprocess.run(edit_render.encoder_command(bounce, 0.0, 2, out, effects, self.folder, preset="ultrafast",
                                                       crf="8"), input=decoded, check=True)
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                                  "yuv420p", "-"], capture_output=True, check=True).stdout
            got = (raw[540 * W + 960], raw[W * H + 270 * W // 2 + 480], raw[W * H * 5 // 4 + 270 * W // 2 + 480])
            for want, value in zip(yuv, got):
                self.assertLessEqual(abs(want - value), 1, (effects, got))

    def test_a_tagged_source_is_read_by_its_tags(self):
        # A phone's file may say Rec. 601 full range; the browser follows the tags, so must we.
        # RGB (193, 48, 32) written as Rec. 601 full range, tagged so; in 709 TV it is (83, 106, 192).
        warm = np.zeros((H, W, 3), np.uint8)
        warm[:] = (193, 48, 32)
        phone = os.path.join(self.folder, "phone.mov")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (W, H),
                        "-r", "30", "-i", "-", "-vf", "scale=out_color_matrix=bt601:out_range=pc", "-pix_fmt", "yuvj420p",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "1", "-colorspace", "smpte170m",
                        "-color_range", "pc", phone], input=warm.tobytes() * 2, check=True)
        colour = edit_render.source_colour(phone)
        self.assertEqual(colour, ("smpte170m", "pc"))
        decoded = subprocess.run(edit_render.decoder_command(phone, 0.0, 1, None, "yuv420p", colour=colour),
                                 capture_output=True, check=True).stdout
        got = (decoded[540 * W + 960], decoded[W * H + 270 * W // 2 + 480], decoded[W * H * 5 // 4 + 270 * W // 2 + 480])
        for want, value in zip((83, 106, 192), got):
            self.assertLessEqual(abs(want - value), 2, got)

    def test_beat_pulse_zooms_on_a_downbeat(self):
        frame = grey(20)
        frame[530:550, 900:1020] = 230                                  # a bar in the middle
        out = render([frame] * 24, {"pulse": 1.0}, self.folder, downbeats_s=[0.1], audio_start_s=0.0)
        width = lambda f: int((f[540, :, 1] > 120).sum())
        self.assertEqual(width(out[0]), width(out[23]))                 # before the beat, after the punch: no zoom
        self.assertGreater(width(out[3]), width(out[0]) + 3)            # just after the beat: zoomed in

    def test_beat_pulse_in_a_long_song(self):
        # 302 downbeats, as the 12-minute song of 2026-11-28 (ffmpeg refused a plain chain of >94).
        frame = grey(20)
        frame[530:550, 900:1020] = 230
        downbeats = [0.1] + [10.0 + 2.0 * i for i in range(301)]
        out = render([frame] * 24, {"pulse": 1.0}, self.folder, downbeats_s=downbeats, audio_start_s=0.0)
        width = lambda f: int((f[540, :, 1] > 120).sum())
        self.assertGreater(width(out[3]), width(out[0]) + 3)            # still zooms on the first downbeat

    def test_denoise_in_the_decoder(self):
        rng = np.random.default_rng(2)
        noisy = [np.clip(128 + rng.normal(0, 6, (H, W, 3)), 0, 255).astype(np.uint8) for _ in range(6)]   # high-gain-like
        source = os.path.join(self.folder, "cam.mov")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % (W, H),
                        "-r", "30", "-i", "-", "-c:v", "libx264", "-qp", "0", source], input=to_yuv(noisy), check=True)

        def decoded(denoise):
            command = edit_render.decoder_command(source, 0, 6, None, "gray", denoise=denoise)
            raw = subprocess.run(command, capture_output=True, check=True).stdout
            return np.frombuffer(raw, np.uint8).reshape(-1, H, W)[4].astype(float)
        self.assertLess(decoded(1.0).std(), decoded(0).std() * 0.7)

    def test_film_overlay_loops_frame_by_frame(self):
        # A 10-frame clip whose frame k is grey level 40 + 16k, in a folder with ':' and ','
        # in its name (the path goes inside the filter graph). Over a mid grey, "overlay" at
        # full strength gives back about the clip's level, so frame n shows clip frame n % 10.
        overlays = os.path.join(self.folder, "over:lays, test")
        clip_folder = os.path.join(overlays, "steps")
        os.makedirs(clip_folder)
        with open(os.path.join(clip_folder, "overlay.json"), "w") as f:
            json.dump({"id": "steps", "mode": "overlay"}, f)
        steps = [grey(40 + 16 * k) for k in range(10)]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % (W, H),
                        "-r", "30", "-i", "-", "-c:v", "libx264", "-qp", "0", os.path.join(clip_folder, "clip.mp4")],
                       input=to_yuv(steps), check=True)
        graph = functools.partial(look_render.effect_filter_graph, overlays_dir=overlays)
        with mock.patch.object(look_render, "effect_filter_graph", graph):
            out = render([grey(128)] * 25, {"overlay:steps": 1.0}, self.folder)
        self.assertEqual(out.shape[0], 25)
        levels = out[:, H // 2, W // 2, 1]
        for n in range(25):
            self.assertAlmostEqual(levels[n], 40 + 16 * (n % 10), delta=6, msg="frame %d" % n)

    def test_everything_at_once(self):
        effects = {name: 0.7 for name in look_render.EFFECTS}
        out = render([texture()] * 6, effects, self.folder, downbeats_s=[0.05])
        self.assertEqual(out.shape, (6, H, W, 3))


if __name__ == "__main__":
    unittest.main()
