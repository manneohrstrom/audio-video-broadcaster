"""Editor projects: cameras on the hub clock, the song's range, the edit's load and save."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import editor_projects as ep  # noqa: E402

SECOND = ep.NS_PER_SECOND
TAKE = "20261116-185403"
FIRST_FRAME = 1_794_855_249_168_184_320          # cam1 of the real test take


def take_document():
    def manifest(first, seconds, role="client", **extra):
        return dict({"first_frame_wallclock_ns": first, "last_frame_wallclock_ns": first + seconds * SECOND,
                     "duration_s": seconds, "frames": seconds * 30, "sync_role": role}, **extra)
    return {
        "take_id": TAKE,
        "manifests": {
            "cam2": manifest(FIRST_FRAME + 35_000, 76),
            "cam1": manifest(FIRST_FRAME, 76, role="server"),
            "pixel-9a": manifest(FIRST_FRAME + 2 * SECOND, 60, source="phone", label="Pixel 9a"),
            "cam3": manifest(FIRST_FRAME, 76),             # recorded but never ingested: left out
        },
        "cameras": {
            "cam1": {"timing": "real", "rough": []},
            "cam2": {"timing": "real", "rough": [{"start_wallclock_ns": FIRST_FRAME + 10 * SECOND,
                                                    "end_wallclock_ns": FIRST_FRAME + 11 * SECOND}]},
            "pixel-9a": {"timing": "real", "rough": []},
        },
    }


class FakeAudio:
    def __init__(self, root, synced=True):
        self.root = root
        d = os.path.join(root, TAKE, "track-1", "v1")
        os.makedirs(d)
        if synced:
            with open(os.path.join(d, "sync.json"), "w") as f:
                json.dump({"bounce_start_ns": FIRST_FRAME - 5 * SECOND, "anchor": "hub mic: music",
                           "confidence": "high", "drift_ppm": 20}, f)

    def take_dir(self, take):
        return os.path.join(self.root, take)

    def list(self, take):
        return {"songs": [{"id": "track-1", "name": "Track 1", "versions": [
            {"version": 1, "complete": True, "duration_s": 120.0, "filename": "mix.wav"}]}]}


class HelpersTest(unittest.TestCase):
    def test_grid_origin(self):
        self.assertEqual(ep.grid_origin_ns(10 * SECOND + 40_000_000), 10 * SECOND + 33_333_333)
        self.assertEqual(ep.grid_origin_ns(10 * SECOND), 10 * SECOND)

    def test_cameras_pi_first_then_phones_only_ingested(self):
        cams = ep.describe_cameras(take_document())
        self.assertEqual([c["id"] for c in cams], ["cam1", "cam2", "pixel-9a"])
        self.assertTrue(cams[0]["is_sync_server"])
        self.assertEqual(cams[2]["label"], "Pixel 9a")
        self.assertEqual(cams[2]["kind"], "phone")
        self.assertEqual(cams[1]["rough_sections_ns"], [[FIRST_FRAME + 10 * SECOND, FIRST_FRAME + 11 * SECOND]])
        self.assertLessEqual(cams[0]["file_zero_ns"], cams[0]["first_frame_ns"])

    def test_range_is_the_overlap(self):
        cams = ep.describe_cameras(take_document())
        start, end = ep.song_range_ns(FIRST_FRAME - 5 * SECOND, 120.0, cams)
        self.assertEqual(start, FIRST_FRAME)                     # the bounce began before the cameras
        self.assertEqual(end, FIRST_FRAME + 35_000 + 76 * SECOND)
        with self.assertRaises(ep.ProjectError):
            ep.song_range_ns(FIRST_FRAME + 500 * SECOND, 60.0, cams)

    def test_validate_cuts(self):
        cuts = ep.validate_cuts([{"at_ns": 5 * SECOND, "camera": "cam2"}, {"at_ns": 3, "camera": "cam1"},
                                 {"at_ns": 6 * SECOND, "camera": "cam2"}], {"cam1", "cam2"}, 60 * SECOND)
        self.assertEqual([(c["at_ns"], c["camera"]) for c in cuts], [(0, "cam1"), (5 * SECOND, "cam2")])
        for bad in ([], [{"at_ns": 0, "camera": "cam9"}], [{"at_ns": 61 * SECOND, "camera": "cam1"}],
                    [{"at_ns": 1.5, "camera": "cam1"}]):
            with self.assertRaises(ep.ProjectError):
                ep.validate_cuts(bad, {"cam1", "cam2"}, 60 * SECOND)


class RebaseTest(unittest.TestCase):
    def test_cuts_keep_their_hub_time(self):
        cuts = [{"at_ns": 0, "camera": "cam1"}, {"at_ns": 10 * SECOND, "camera": "cam2"}]
        moved = ep.rebase_cuts(cuts, [100 * SECOND, 200 * SECOND], [100 * SECOND + 6_500_000, 199 * SECOND])
        self.assertEqual([c["at_ns"] for c in moved], [0, 10 * SECOND - 6_500_000])

    def test_cuts_before_the_new_start_collapse_and_after_the_end_go(self):
        cuts = [{"at_ns": 0, "camera": "cam1"}, {"at_ns": 2 * SECOND, "camera": "cam2"},
                {"at_ns": 50 * SECOND, "camera": "cam3"}, {"at_ns": 95 * SECOND, "camera": "cam4"}]
        moved = ep.rebase_cuts(cuts, [100 * SECOND, 200 * SECOND], [105 * SECOND, 190 * SECOND])
        self.assertEqual([(c["at_ns"], c["camera"]) for c in moved], [(0, "cam2"), (45 * SECOND, "cam3")])
        self.assertIsNone(ep.rebase_cuts(cuts, [0, SECOND], [5 * SECOND, 9 * SECOND]))


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.quick = None
        self.projects = ep.EditorProjects(FakeAudio(os.path.join(self.tmp, "audio")),
                                          read_take_document=lambda take: take_document(),
                                          find_quick_edit=lambda take, song, v: self.quick,
                                          edits_dir=os.path.join(self.tmp, "edits"), clock=lambda: 123.0)

    def test_new_project_starts_on_the_sync_server(self):
        p = self.projects.project(TAKE, "track-1", 1)
        self.assertEqual(p["edit_source"], "new")
        self.assertEqual(p["edit"]["cuts"], [{"at_ns": 0, "camera": "cam1", "by": "auto"}])
        self.assertEqual(p["bounce"]["url"], "/media-audio/%s/track-1/v1" % TAKE)
        self.assertEqual(p["cameras"][0]["file_url"], "/media/%s/cam1.mov" % TAKE)
        self.assertEqual(p["bounce"]["sync"]["confidence"], "high")

    def test_starts_from_the_quick_mix_even_after_a_resync(self):
        rng = self.projects.project(TAKE, "track-1", 1)["range_ns"]
        self.quick = {"range_ns": [rng[0], rng[1] + 6_500_000],                   # rendered with an older sync
                      "cuts": [{"at_ns": 0, "camera": "cam1"}, {"at_ns": 10 * SECOND, "camera": "cam2"}]}
        p = self.projects.project(TAKE, "track-1", 1)
        self.assertEqual(p["edit_source"], "quick mix")
        self.assertEqual([c["camera"] for c in p["edit"]["cuts"]], ["cam1", "cam2"])

    def test_save_then_load(self):
        saved = self.projects.save_edit(TAKE, "track-1", 1, {"cuts": [{"at_ns": 0, "camera": "cam2"},
                                                                      {"at_ns": 2 * SECOND, "camera": "pixel-9a"}],
                                                            "dissolve_s": 0.25})
        self.assertEqual(saved["made_by"], "user")
        p = self.projects.project(TAKE, "track-1", 1)
        self.assertEqual(p["edit_source"], "saved")
        self.assertEqual([c["camera"] for c in p["edit"]["cuts"]], ["cam2", "pixel-9a"])
        self.assertEqual(p["edit"]["dissolve_s"], 0.25)

    def test_bad_requests(self):
        with self.assertRaises(ep.ProjectError):
            self.projects.project("../etc", "track-1", 1)
        with self.assertRaises(ep.ProjectError):
            self.projects.project(TAKE, "nope", 1)
        with self.assertRaises(ep.ProjectError):
            self.projects.save_edit(TAKE, "track-1", 1, {"cuts": [{"at_ns": 0, "camera": "cam3"}]})

    def test_unsynced_song_is_refused(self):
        projects = ep.EditorProjects(FakeAudio(os.path.join(self.tmp, "audio2"), synced=False),
                                     read_take_document=lambda take: take_document(),
                                     find_quick_edit=lambda *a: None, edits_dir=self.tmp)
        with self.assertRaises(ep.ProjectError):
            projects.project(TAKE, "track-1", 1)


if __name__ == "__main__":
    unittest.main()
