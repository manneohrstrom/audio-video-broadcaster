"""The editor's render over awkward footage (2026-11-17): cameras that start late or stop
early, dropped frames, a phone. Every output frame is checked against the hub-clock time
it should show: in sync (within a frame), held across dropped frames, black where the
camera wasn't recording (as in the editor's preview). Takes about 7 s (one 1080p render).

Each source frame carries a barcode: camera number (2 bits) and frame index + 1 (16 bits),
so an all-black frame reads as "nothing". Files are made like ingest makes them: frames
at their real times (VFR, gaps kept), then ingest's rewrap command (-itsoffset residual,
-c copy, 90 kHz .mov).
"""
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

WORK = None                       # set per test run (a temporary folder)
NS = 1_000_000_000
W, H = 640, 360
T0 = 1794688178_123456789

# id, barcode number, kind, first frame (s after T0), length (s), real fps, dropped stretches (s after its first frame)
CAMERAS = [
    ("cam1", 1, "pi", 0.000, 30.0, 30.011, []),
    ("cam2", 2, "pi", 3.000, 21.0, 30.011, []),             # starts late, stops early (T0 + 24 s)
    ("cam3", 3, "pi", 0.017, 30.0, 30.011, [(10.0, 12.0)]),  # 2 s of dropped frames
    ("phone1", 0, "phone", 1.500, 25.0, 29.94, [(13.5, 14.0)]),
]
CUTS_S = [(0, "cam2"), (5, "cam1"), (9, "cam3"), (14, "phone1"), (18, "cam2"), (25, "cam1")]
RANGE = (T0 + NS // 2, T0 + 28 * NS)
DISSOLVE_S = 0.3


def barcode(number, index):
    frame = np.full((H, W), 128, np.uint8)
    value = index + 1
    for bit in range(16):
        frame[0:120, bit * 40:(bit + 1) * 40] = 235 if (value >> bit) & 1 else 16
    for bit in range(2):
        frame[120:240, bit * 40:(bit + 1) * 40] = 235 if (number >> bit) & 1 else 16
    return frame


def make_camera(camera_id, number, kind, first_s, length_s, fps, drops):
    """Writes take/<camera>.mov; returns (manifest, ingest camera state, {index: hub ns})."""
    kept = [i for i in range(int(length_s * fps)) if not any(a <= i / fps < b for a, b in drops)]
    times = {i: T0 + int(round((first_s + i / fps) * NS)) for i in kept}
    first = times[kept[0]]
    # Frame k of the file is source frame kept[k]: its time = kept[k] / fps. kept[k] = k + the
    # frames dropped before it, so the pts is (k + sum of missing frames of earlier gaps) / fps.
    jumps = []
    for a, b in drops:
        start = next(i for i in range(int(length_s * fps)) if i / fps >= a)
        end = next(i for i in range(int(length_s * fps)) if i / fps >= b)
        jumps.append((kept.index(end), end - start))
    index_expr = "N" + "".join("+%d*gte(N,%d)" % (missing, at) for at, missing in jumps)
    mkv = os.path.join(WORK, camera_id + ".mkv")
    encoder = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "%dx%d" % (W, H),
         "-framerate", "30", "-i", "-", "-vf", "settb=1/90000,setpts='(%s)/(%r*TB)'" % (index_expr, fps), "-fps_mode", "passthrough",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", "-pix_fmt", "yuv420p", "-g", "15", mkv],
        stdin=subprocess.PIPE)
    for index in kept:
        encoder.stdin.write(barcode(number, index).tobytes())
    encoder.stdin.close()
    assert encoder.wait() == 0
    # ingest's rewrap, exactly (Ingest.rewrap)
    tc, origin, residual = ingest.grid_origin(first)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y",
                    "-itsoffset", "%.6f" % (residual / 1e9), "-i", mkv, "-map", "0:v", "-c", "copy",
                    "-video_track_timescale", "90000", "-movie_timescale", "90000",
                    "-timecode", tc, "-f", "mov", os.path.join(WORK, "take", camera_id + ".mov")], check=True)
    manifest = {"first_frame_wallclock_ns": first, "last_frame_wallclock_ns": times[kept[-1]],
                "duration_s": (times[kept[-1]] - first) / NS, "frames": len(kept),
                "sync_role": "server" if camera_id == "cam1" else "client", "label": camera_id}
    if kind == "phone":
        manifest.update(source="phone", fps_measured=fps)
    rough = [{"start_wallclock_ns": T0 + int((first_s + a - 1) * NS), "end_wallclock_ns": T0 + int((first_s + b + 1) * NS)}
             for a, b in drops]
    return manifest, {"rough": rough, "timing": "real"}, times


def read_output(path):
    """[(camera number, source index) or None] per output frame."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, 1080, 1920)
    sx = 1920 / W
    out = []
    for frame in frames:
        bits = [frame[int(60 * 1080 / H), int((b * 40 + 20) * sx)] > 128 for b in range(16)]
        cams = [frame[int(180 * 1080 / H), int((b * 40 + 20) * sx)] > 128 for b in range(2)]
        value = sum(1 << b for b, on in enumerate(bits) if on)
        number = sum(1 << b for b, on in enumerate(cams) if on)
        grey = frame[int(300 * 1080 / H), 960]
        out.append(None if value == 0 and grey < 60 else (number, value - 1, int(grey)))
    return out


def render_and_read():
    os.makedirs(os.path.join(WORK, "take"))
    manifests, states, times = {}, {}, {}
    for camera_id, number, kind, first_s, length_s, fps, drops in CAMERAS:
        manifests[camera_id], states[camera_id], times[camera_id] = make_camera(
            camera_id, number, kind, first_s, length_s, fps, drops)
    take_doc = {"take_id": "20261114-202934", "manifests": manifests, "cameras": states}
    bounce = os.path.join(WORK, "bounce.wav")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=30", "-ar", "48000", bounce], check=True)
    edit = {"range_ns": list(RANGE), "bounce_start_ns": T0, "dissolve_s": DISSOLVE_S, "look": None,
            "cuts": [{"at_ns": int(s * NS), "camera": c} for s, c in CUTS_S]}
    with open(os.path.join(REPO, "hub/hubd/static/editor/looks.json")) as f:
        looks = json.load(f)
    out = os.path.join(WORK, "out.mp4")
    renderer = edit_render.EditRenderer(edit, take_doc, os.path.join(WORK, "take"), bounce, None, looks, WORK)
    renderer.run(out)
    return read_output(out), times, renderer.total_frames


def check(frames, times, total):
    numbers = {c[0]: c[1] for c in CAMERAS}
    spans = {c[0]: (min(times[c[0]].values()), max(times[c[0]].values())) for c in CAMERAS}
    cut_frames = [round(s * 30) for s, _ in CUTS_S]
    problems, counts = [], {"in sync": 0, "black (not recording)": 0, "held (dropped frames)": 0, "dissolve (skipped)": 0}
    for k, got in enumerate(frames):
        if any(abs(k - c) <= 5 for c in cut_frames[1:]):
            counts["dissolve (skipped)"] += 1
            continue
        hub = RANGE[0] + k * NS // 30
        camera = [c for s, c in CUTS_S if s * 30 <= k][-1]
        start, end = spans[camera]
        if (hub < start + NS // 30 or hub > end - NS // 30) and got is None:
            counts["black (not recording)"] += 1          # at or just past an edge: black is right
            continue
        if hub < start - NS // 30 or hub > end + NS // 30:
            if got is not None:
                problems.append("frame %d (%.2f s): %s isn't recording, got %s" % (k, k / 30, camera, got))
            else:
                counts["black (not recording)"] += 1
            continue
        if got is None or got[0] != numbers[camera]:
            problems.append("frame %d (%.2f s): wanted %s, got %s" % (k, k / 30, camera, got))
            continue
        shown = times[camera].get(got[1])
        if shown is None:
            problems.append("frame %d: index %d isn't a frame of %s" % (k, got[1], camera))
            continue
        error_ms = (shown - hub) / 1e6
        before = [t for t in times[camera].values() if t <= hub + NS // 60]
        latest = max(before) if before else None
        if abs(error_ms) <= 1000 / 30 + 1:
            counts["in sync"] += 1
        elif latest is not None and shown == latest:
            counts["held (dropped frames)"] += 1          # the last frame before a gap, held
        else:
            problems.append("frame %d (%.2f s): %s shows a frame %.1f ms off" % (k, k / 30, camera, error_ms))
    return counts, problems


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class RenderOverAwkwardFootageTest(unittest.TestCase):
    def test_every_frame_is_in_sync_held_or_black(self):
        global WORK
        WORK = tempfile.mkdtemp()
        try:
            frames, times, total = render_and_read()
            self.assertEqual(len(frames), total)
            counts, problems = check(frames, times, total)
            self.assertEqual(problems, [])
            self.assertGreater(counts["in sync"], 500)
            self.assertGreater(counts["held (dropped frames)"], 50)          # cam3's 2 s, the phone's 0.5 s
            self.assertGreater(counts["black (not recording)"], 100)         # cam2 before its start, after its end
        finally:
            shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
