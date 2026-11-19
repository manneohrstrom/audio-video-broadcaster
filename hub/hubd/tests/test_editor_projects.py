"""Editor projects: cameras on the hub clock, the song's range, the edit's load and save."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import editor_projects as ep  # noqa: E402
import names  # noqa: E402

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
    def __init__(self, root, synced=True, existing=False):
        self.root = root
        d = os.path.join(root, TAKE, "track-1", "v1")
        os.makedirs(d, exist_ok=existing)
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

    def test_new_project_starts_from_the_rotation_until_beats_are_analysed(self):
        p = self.projects.project(TAKE, "track-1", 1)
        self.assertEqual(p["edit_source"], "automatic cut (no beats yet)")
        self.assertEqual(p["edit"]["made_by"], "auto-v1")
        cuts = p["edit"]["cuts"]
        self.assertEqual(cuts[0], {"at_ns": 0, "camera": "cam1", "by": "auto"})     # starts on the sync server
        self.assertEqual(cuts[1]["camera"], "cam2")
        self.assertTrue(all(c["camera"] in ("cam1", "cam2") for c in cuts))          # Pi cameras only
        self.assertEqual(p["bounce_versions"], [{"version": 1, "file_name": "mix.wav"}])
        self.assertEqual(p["bounce"]["url"], "/media-audio/%s/track-1/v1" % TAKE)
        self.assertEqual(p["cameras"][0]["file_url"], "/media/%s/cam1.mov" % TAKE)
        self.assertEqual(p["bounce"]["sync"]["confidence"], "high")
        self.assertIsNone(p["take_name"])

    def test_the_users_names_for_cameras_and_the_take(self):
        self.projects.names = names.Names(os.path.join(self.tmp, "names.json"))
        self.projects.names.set("cameras", "cam2", "Drum kit")
        self.projects.names.set("takes", TAKE, "Sunday jam")
        p = self.projects.project(TAKE, "track-1", 1)
        self.assertEqual([c["label"] for c in p["cameras"]], ["cam1", "Drum kit", "Pixel 9a"])
        self.assertEqual(p["take_name"], "Sunday jam")

    def test_starts_from_the_rules_once_beats_are_analysed(self):
        version_dir = os.path.join(self.tmp, "audio", TAKE, "track-1", "v1")
        step = 0.6                                                    # 100 BPM from the bounce's start
        times = [round(i * step, 3) for i in range(200)]
        with open(os.path.join(version_dir, "beats.json"), "w") as f:
            json.dump({"beats_s": times, "downbeats_s": times[::4], "confidence": [1.0] * 200,
                       "hit": [1.0] * 200}, f)
        os.makedirs(os.path.join(self.tmp, "proxies", TAKE))
        with open(os.path.join(self.tmp, "proxies", TAKE, "cam2.motion.json"), "w") as f:
            json.dump({"times_s": [i * 0.5 for i in range(1, 160)], "motion": [3.0] * 159}, f)
        self.quick = {"range_ns": [0, 10 ** 12], "cuts": [{"at_ns": 0, "camera": "cam2"}]}   # the rules win
        projects = ep.EditorProjects(FakeAudio(os.path.join(self.tmp, "audio"), existing=True),
                                     read_take_document=lambda take: take_document(),
                                     find_quick_edit=lambda take, song, v: self.quick,
                                     edits_dir=os.path.join(self.tmp, "edits"),
                                     proxies_dir=os.path.join(self.tmp, "proxies"), clock=lambda: 123.0)
        p = projects.project(TAKE, "track-1", 1)
        self.assertEqual((p["edit_source"], p["edit"]["made_by"]), ("automatic cut", "rules-v1"))
        cuts = p["edit"]["cuts"]
        self.assertEqual(cuts[0]["camera"], "cam1")                   # the wide camera (sync server) first
        self.assertTrue({c["camera"] for c in cuts} <= {"cam1", "cam2"})
        song_start = p["range_ns"][0]
        bounce_start = p["bounce"]["start_ns"]
        beat_ns = {bounce_start + int(round(t * SECOND * (1 + 20e-6))) - song_start for t in times}
        self.assertTrue(all(c["at_ns"] in beat_ns for c in cuts[1:]))  # on beats, drift included
        rough = (FIRST_FRAME + 10 * SECOND - song_start, FIRST_FRAME + 11 * SECOND - song_start)
        ends = [c["at_ns"] for c in cuts[1:]] + [p["range_ns"][1] - song_start]
        for cut, end in zip(cuts, ends):
            if cut["camera"] == "cam2":
                self.assertTrue(end <= rough[0] or cut["at_ns"] >= rough[1])

    def test_starts_from_the_quick_mix_even_after_a_resync(self):
        rng = self.projects.project(TAKE, "track-1", 1)["range_ns"]
        self.quick = {"range_ns": [rng[0], rng[1] + 6_500_000],                   # rendered with an older sync
                      "cuts": [{"at_ns": 0, "camera": "cam1"}, {"at_ns": 10 * SECOND, "camera": "cam2"}]}
        p = self.projects.project(TAKE, "track-1", 1)
        self.assertEqual(p["edit_source"], "quick mix")
        self.assertEqual([c["camera"] for c in p["edit"]["cuts"]], ["cam1", "cam2"])

    def test_the_edit_belongs_to_the_song_and_older_per_version_copies_are_read(self):
        older = os.path.join(self.tmp, "edits", TAKE, "track-1", "v1.json")
        os.makedirs(os.path.dirname(older))
        rng = self.projects.project(TAKE, "track-1", 1)["range_ns"]
        with open(older, "w") as f:
            json.dump({"range_ns": rng, "cuts": [{"at_ns": 0, "camera": "cam2"}]}, f)
        self.assertEqual(self.projects.project(TAKE, "track-1", 1)["edit"]["cuts"][0]["camera"], "cam2")
        self.projects.save_edit(TAKE, "track-1", 1, {"cuts": [{"at_ns": 0, "camera": "cam1"}]})
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "edits", TAKE, "track-1.json")))

    def test_save_then_load(self):
        saved = self.projects.save_edit(TAKE, "track-1", 1, {"cuts": [{"at_ns": 0, "camera": "cam2"},
                                                                      {"at_ns": 2 * SECOND, "camera": "pixel-9a"}],
                                                            "dissolve_s": 0.25})
        self.assertEqual(saved["made_by"], "user")
        p = self.projects.project(TAKE, "track-1", 1)
        self.assertEqual(p["edit_source"], "saved")
        self.assertEqual([c["camera"] for c in p["edit"]["cuts"]], ["cam2", "pixel-9a"])
        self.assertEqual(p["edit"]["dissolve_s"], 0.25)

    def test_branding_is_saved_and_off_by_default(self):
        cut = {"cuts": [{"at_ns": 0, "camera": "cam2"}]}
        self.assertFalse(self.projects.save_edit(TAKE, "track-1", 1, cut)["branding"])
        self.projects.save_edit(TAKE, "track-1", 1, dict(cut, branding=True))
        self.assertTrue(self.projects.project(TAKE, "track-1", 1)["edit"]["branding"])
        with self.assertRaises(ep.ProjectError):
            self.projects.save_edit(TAKE, "track-1", 1, dict(cut, branding="yes"))
        self.assertEqual(self.projects.project(TAKE, "track-1", 1)["edit"]["branding_colour"], "#ffffff")
        self.assertEqual(self.projects.save_edit(TAKE, "track-1", 1, dict(cut, branding_colour="#C0182A"))["branding_colour"],
                         "#c0182a")
        with self.assertRaises(ep.ProjectError):
            self.projects.save_edit(TAKE, "track-1", 1, dict(cut, branding_colour="red"))

    def test_title_is_saved_and_checked(self):
        cut = {"cuts": [{"at_ns": 0, "camera": "cam2"}]}
        self.assertNotIn("title", self.projects.save_edit(TAKE, "track-1", 1, cut))
        title = {"on": True, "text": "Brockley Live", "lines": ["Anna, piano", ""], "colour": "#F4E7CC",
                 "start_s": 2, "duration_s": 6.5}
        saved = self.projects.save_edit(TAKE, "track-1", 1, dict(cut, title=title))["title"]
        self.assertEqual(saved, dict(title, colour="#f4e7cc", start_s=2.0))
        self.assertEqual(self.projects.project(TAKE, "track-1", 1)["edit"]["title"]["text"], "Brockley Live")
        for bad in ({"on": "yes"}, {"text": "two\nlines"}, {"text": "x" * 81}, {"lines": ["a"] * 9},
                    {"lines": "Anna"}, {"colour": "white"}, {"start_s": -1}, {"start_s": 10 ** 6},
                    {"duration_s": 0.5}, {"duration_s": 61}, {"duration_s": True}):
            with self.assertRaises(ep.ProjectError, msg=bad):
                self.projects.save_edit(TAKE, "track-1", 1, dict(cut, title=dict(title, **bad)))

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


class SyncByHandTest(unittest.TestCase):
    """Sync mode (docs/editor-plan.md section 4b): place the bounce by hand, synced or not."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.job = None
        self.make(synced=True)

    def make(self, synced):
        root = os.path.join(self.tmp, "audio-%s" % synced)
        self.projects = ep.EditorProjects(FakeAudio(root, synced=synced),
                                          read_take_document=lambda take: take_document(),
                                          find_quick_edit=lambda *a: None, edits_dir=os.path.join(self.tmp, "edits"),
                                          clock=lambda: 200.0, sync_job=lambda *a: self.job)
        self.version_dir = os.path.join(root, TAKE, "track-1", "v1")

    def read(self, name):
        with open(os.path.join(self.version_dir, name)) as f:
            return json.load(f)

    def test_sync_mode_spans_the_take_and_starts_from_the_automatic_sync(self):
        p = self.projects.project(TAKE, "track-1", 1, mode="sync")
        self.assertEqual(p["mode"], "sync")
        self.assertEqual(p["range_ns"], [FIRST_FRAME, FIRST_FRAME + 35_000 + 76 * SECOND])     # the Pi cameras
        self.assertEqual(p["bounce"]["start_ns"], FIRST_FRAME - 5 * SECOND)
        self.assertEqual(p["sync_start"]["source"], "automatic")
        self.assertEqual(p["edit"]["cuts"], [{"at_ns": 0, "camera": "cam1", "by": "user"}])   # the sync server

    def test_save_keeps_the_automatic_result_and_its_drift(self):
        saved = self.projects.save_sync(TAKE, "track-1", 1, FIRST_FRAME - 4 * SECOND)
        self.assertEqual(saved["anchor"], "by hand")
        self.assertEqual(saved["drift_ppm"], 20)
        self.assertEqual(saved["moved_from_automatic_s"], 1.0)
        self.assertEqual(self.read("sync-auto.json")["anchor"], "hub mic: music")
        # Saved again: the automatic result stays the one kept, not the first hand placement.
        self.projects.save_sync(TAKE, "track-1", 1, FIRST_FRAME - 3 * SECOND)
        self.assertEqual(self.read("sync-auto.json")["bounce_start_ns"], FIRST_FRAME - 5 * SECOND)
        p = self.projects.project(TAKE, "track-1", 1, mode="sync")
        self.assertEqual(p["sync_start"]["source"], "by hand")
        self.assertEqual(p["sync_start"]["automatic_start_ns"], FIRST_FRAME - 5 * SECOND)
        # The editor opens the song with the new placement.
        edit = self.projects.project(TAKE, "track-1", 1)
        self.assertEqual(edit["bounce"]["start_ns"], FIRST_FRAME - 3 * SECOND)
        self.assertEqual(edit["bounce"]["sync"]["anchor"], "by hand")

    def test_an_unsynced_bounce_starts_from_the_failed_syncs_best_guess(self):
        self.make(synced=False)
        os.makedirs(os.path.join(self.version_dir, "..", "..", "scratch"))
        with open(os.path.join(self.version_dir, "..", "..", "scratch", "scratch.times.tsv"), "w") as f:
            f.write("0 %d\n4800 %d\n" % (FIRST_FRAME - 10 * SECOND, FIRST_FRAME - 9 * SECOND))
        with open(os.path.join(self.version_dir, "sync_failed.json"), "w") as f:
            json.dump({"reason": "the match isn't clear enough", "details": {"whole": {"offset_s": 3.074}}}, f)
        p = self.projects.project(TAKE, "track-1", 1, mode="sync")
        self.assertEqual(p["sync_start"]["source"], "best guess")
        self.assertEqual(p["bounce"]["start_ns"], FIRST_FRAME - 7 * SECOND)
        self.projects.save_sync(TAKE, "track-1", 1, FIRST_FRAME - 7 * SECOND)
        self.assertEqual(self.read("sync.json")["drift_ppm"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.version_dir, "sync-auto.json")))

    def test_without_any_sync_it_starts_at_the_take(self):
        self.make(synced=False)
        p = self.projects.project(TAKE, "track-1", 1, mode="sync")
        self.assertEqual((p["sync_start"]["source"], p["bounce"]["start_ns"]), ("take start", FIRST_FRAME))

    def test_refused_while_syncing_or_off_the_video(self):
        self.job = {"status": "syncing"}
        with self.assertRaises(ep.ProjectError):
            self.projects.save_sync(TAKE, "track-1", 1, FIRST_FRAME)
        self.job = {"status": "needs sync"}
        with self.assertRaises(ep.ProjectError):
            self.projects.save_sync(TAKE, "track-1", 1, FIRST_FRAME + 500 * SECOND)        # after the take
        with self.assertRaises(ep.ProjectError):
            self.projects.save_sync(TAKE, "track-1", 1, "soon")
        self.assertEqual(self.read("sync.json")["anchor"], "hub mic: music")              # untouched


class DriveEditTest(unittest.TestCase):
    """The working cut kept on the drive (docs/stick-db-plan.md, S4)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.on_drive = None
        self.projects = ep.EditorProjects(FakeAudio(os.path.join(self.tmp, "audio")),
                                          read_take_document=lambda take: take_document(),
                                          find_quick_edit=lambda *a: None,
                                          edits_dir=os.path.join(self.tmp, "edits"), clock=lambda: 100.0,
                                          read_drive_edit=lambda take, song: self.on_drive)
        self.range_ns = self.projects.project(TAKE, "track-1", 1)["range_ns"]

    def drive_copy(self, camera, saved_at):
        return {"range_ns": self.range_ns, "cuts": [{"at_ns": 0, "camera": camera}], "saved_at": saved_at}

    def first_camera(self):
        return self.projects.project(TAKE, "track-1", 1)["edit"]["cuts"][0]["camera"]

    def test_a_hub_without_its_own_copy_opens_the_drives(self):
        self.on_drive = self.drive_copy("cam2", saved_at=50.0)
        self.assertEqual(self.first_camera(), "cam2")
        hub_copy = os.path.join(self.tmp, "edits", TAKE, "track-1.json")
        with open(hub_copy) as f:
            self.assertEqual(json.load(f)["cuts"][0]["camera"], "cam2")   # the render reads this one

    def test_the_newer_copy_wins(self):
        self.projects.save_edit(TAKE, "track-1", 1, {"cuts": [{"at_ns": 0, "camera": "cam1"}]})   # saved_at 100
        self.on_drive = self.drive_copy("cam2", saved_at=50.0)
        self.assertEqual(self.first_camera(), "cam1")
        self.on_drive = self.drive_copy("cam2", saved_at=150.0)
        self.assertEqual(self.first_camera(), "cam2")



class WaveformTest(unittest.TestCase):
    def test_peaks_from_the_bounce_and_cached(self):
        import math
        import struct
        import wave
        tmp = tempfile.mkdtemp()
        audio = FakeAudio(os.path.join(tmp, "audio"))
        path = os.path.join(audio.take_dir(TAKE), "track-1", "v1", "mix.wav")
        with wave.open(path, "wb") as w:                      # 1 s silence, then 1 s loud
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            tone = [int(20000 * math.sin(2 * math.pi * 200 * i / 8000)) for i in range(8000)]   # 200 Hz
            w.writeframes(struct.pack("<8000h", *([0] * 8000)) + struct.pack("<8000h", *tone))
        projects = ep.EditorProjects(audio, read_take_document=lambda t: take_document(),
                                     find_quick_edit=lambda *a: None, edits_dir=os.path.join(tmp, "edits"),
                                     waveforms_dir=os.path.join(tmp, "waves"))
        wf = projects.waveform(TAKE, "track-1", 1)
        self.assertEqual(wf["rate"], 100)
        self.assertAlmostEqual(len(wf["peaks"]), 200, delta=2)
        self.assertLess(max(wf["peaks"][:90]), 5)
        self.assertGreater(min(wf["peaks"][110:190]), 100)
        os.remove(path)                                       # cached: no need for the file now
        self.assertEqual(projects.waveform(TAKE, "track-1", 1), wf)


if __name__ == "__main__":
    unittest.main()
