"""The automatic cut's rules (auto_cut.py), on made-up cameras, beats and movement."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import auto_cut  # noqa: E402

SECOND = auto_cut.NS_PER_SECOND
START = 1_794_855_249_000_000_000


def camera(camera_id, first_s=0.0, last_s=200.0, rough=(), server=False, kind="pi"):
    return {"id": camera_id, "kind": kind, "is_sync_server": server,
            "file_zero_ns": START, "first_frame_ns": START + int(first_s * SECOND),
            "last_frame_ns": START + int(last_s * SECOND),
            "rough_sections_ns": [[START + int(a * SECOND), START + int(b * SECOND)] for a, b in rough]}


def beats_document(bpm=100.0, seconds=200.0, confidence=1.0, every_bar_hit=True):
    """Steady beats from the bounce's start; the one of each bar hits hardest."""
    step = 60.0 / bpm
    times = [round(i * step, 3) for i in range(int(seconds / step))]
    return {"beats_s": times, "downbeats_s": times[::4], "confidence": [confidence] * len(times),
            "hit": [(1.0 if i % 4 == 0 else 0.4) if every_bar_hit else 0.6 for i in range(len(times))]}


def movement(levels):
    """motion.json with one reading every 0.5 s: levels is [(from_s, to_s, value)]."""
    times, values = [], []
    for i in range(1, 400):
        t = i * 0.5
        times.append(t)
        values.append(next((v for a, b, v in levels if a <= t < b), 0.0))
    return {"times_s": times, "motion": values}


def shots(cuts, length_ns):
    """[(start s, end s, camera)] from the edit's cuts."""
    ends = [c["at_ns"] for c in cuts[1:]] + [length_ns]
    return [(c["at_ns"] / SECOND, end / SECOND, c["camera"]) for c, end in zip(cuts, ends)]


class Rules(unittest.TestCase):
    def setUp(self):
        self.cameras = [camera("cam1", server=True), camera("cam2"), camera("cam3"), camera("cam4")]
        self.range = [START + 2 * SECOND, START + 182 * SECOND]          # a 3-minute song
        self.length = self.range[1] - self.range[0]

    def cut(self, **kw):
        args = dict(cameras=self.cameras, range_ns=self.range, beats=beats_document(), bounce_start_ns=START)
        args.update(kw)
        return auto_cut.rules_cut(**args)

    def test_shots_hold_4_to_10_seconds(self):
        for start, end, _ in shots(self.cut(), self.length):
            self.assertGreaterEqual(end - start, 4.0 - 1e-6)
            self.assertLessEqual(end - start, 10.0 + 1e-6)

    def test_cuts_land_on_beats_preferring_the_one(self):
        beats = beats_document()
        cuts = self.cut()[1:]
        beat_ns = {START + int(round(t * SECOND)) - self.range[0] for t in beats["beats_s"]}
        bar_ns = {START + int(round(t * SECOND)) - self.range[0] for t in beats["downbeats_s"]}
        self.assertTrue(all(c["at_ns"] in beat_ns for c in cuts))
        self.assertGreater(sum(c["at_ns"] in bar_ns for c in cuts) / len(cuts), 0.8)

    def test_starts_on_the_wide_camera_and_comes_back_to_it(self):
        result = shots(self.cut(), self.length)
        self.assertEqual(result[0][2], "cam1")                          # the sync server by default
        wide_starts = [s for s, _, cam in result if cam == "cam1"]
        gaps = [b - a for a, b in zip(wide_starts, wide_starts[1:])]
        self.assertTrue(gaps and max(gaps) <= 30 + 10 + 10 + 1e-6)     # due after 30 s, on at the next cut
        self.assertEqual(shots(self.cut(wide_id="cam3"), self.length)[0][2], "cam3")

    def test_never_shows_a_camera_outside_its_recording_or_in_a_rough_section(self):
        self.cameras[1] = camera("cam2", first_s=40.0)                   # started late
        self.cameras[2] = camera("cam3", last_s=120.0)                   # stopped early
        self.cameras[3] = camera("cam4", rough=[(60.0, 75.0)])           # dropped frames
        for start, end, cam in shots(self.cut(), self.length):
            a, b = start + 2.0, end + 2.0                                # song time -> file time
            if cam == "cam2":
                self.assertGreaterEqual(a, 40.0 - 0.04)
            if cam == "cam3":
                self.assertLessEqual(b, 120.0 + 0.04)
            if cam == "cam4":
                self.assertTrue(b <= 60.0 or a >= 75.0, (a, b))

    def test_prefers_the_camera_with_movement(self):
        motion = {"cam3": movement([(0, 200, 5.0)]), "cam2": movement([(0, 200, 0.5)]),
                  "cam4": movement([(0, 200, 0.5)])}
        result = shots(self.cut(motion=motion), self.length)
        non_wide = [cam for _, _, cam in result if cam != "cam1"]
        self.assertEqual(max(set(non_wide), key=non_wide.count), "cam3")
        self.assertEqual(len(set(non_wide)), 3)                          # the others still get their turn

    def test_noise_doesnt_decide_in_a_still_room(self):
        """Real take 20261117-132140: hardly anything moved (readings ~0.02, up to ~1.4 on one
        camera now and then); the cameras must take turns, the wide one only when due."""
        motion = {"cam2": movement([(0, 200, 0.05)]), "cam3": movement([(0, 200, 0.01)]),
                  "cam4": movement([(0, 200, 0.03)])}
        result = shots(self.cut(motion=motion), self.length)
        others = [cam for _, _, cam in result if cam != "cam1"]
        counts = {cam: others.count(cam) for cam in ("cam2", "cam3", "cam4")}
        self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)
        wide_starts = [s for s, _, cam in result if cam == "cam1"]
        self.assertTrue(all(b - a >= 30 for a, b in zip(wide_starts, wide_starts[1:])))

    def test_no_camera_twice_in_a_row(self):
        cams = [cam for _, _, cam in shots(self.cut(), self.length)]
        self.assertTrue(all(a != b for a, b in zip(cams, cams[1:])))

    def test_deterministic(self):
        motion = {"cam2": movement([(30, 90, 3.0)])}
        self.assertEqual(self.cut(motion=motion), self.cut(motion=motion))

    def test_untrusted_beats_fall_back_to_time(self):
        cuts = self.cut(beats=beats_document(confidence=0.0))
        for start, end, _ in shots(cuts, self.length):
            self.assertGreaterEqual(end - start, 4.0 - 1e-6)
            self.assertLessEqual(end - start, 10.0 + 1e-6)

    def test_no_beats_at_all_still_cuts_on_time(self):
        cuts = self.cut(beats={"beats_s": []})
        self.assertGreater(len(cuts), 10)

    def test_phones_left_out(self):
        self.cameras.append(camera("pixel-9a", kind="phone"))
        self.assertNotIn("pixel-9a", {c["camera"] for c in self.cut()})

    def test_a_short_song_is_one_shot_on_the_wide_camera(self):
        cuts = auto_cut.rules_cut(self.cameras, [START, START + 8 * SECOND], beats_document(), START)
        self.assertEqual(cuts, [{"at_ns": 0, "camera": "cam1", "by": "auto"}])

    def test_the_sync_drift_moves_the_beats(self):
        points = auto_cut.beat_points({"beats_s": [100.0]}, START, 100.0, auto_cut.DEFAULTS)
        self.assertEqual(points[0][0], START + 100 * SECOND + 10_000_000)  # 100 ppm of 100 s = 10 ms


if __name__ == "__main__":
    unittest.main()
