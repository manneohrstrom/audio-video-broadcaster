"""The STUDIO drive as the record of every take (docs/stick-db-plan.md): the take-log
record in take.json, the drive index, working cuts on the drive, takes adopted from a drive."""
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import drive_records  # noqa: E402
from test_ingest import Base, TAKE  # noqa: E402

RECORD = {"take_id": TAKE, "started_at": 1794688178.1, "stopped_at": 1794688188.4,
          "sync_warnings": ["cam2 started 40 ms late"], "calibration": {"cam1": {"gain": 2.0}}}


class DriveRecordsBase(Base):
    def setUp(self):
        super().setUp()
        self.hubd = os.path.join(self.tmp, "hubd")
        os.makedirs(self.hubd)
        self.ing.cfg.update(hubd_state_dir=self.hubd, audio_dir=os.path.join(self.tmp, "audio"),
                            thumbs_dir=os.path.join(self.tmp, "thumbs"), proxies_dir=os.path.join(self.tmp, "proxies"))

    def write_log(self, *entries):
        with open(os.path.join(self.hubd, "takes.json"), "w") as f:
            json.dump(list(entries), f)

    def ingest_one(self):
        self.rig.add_take("cam1")
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        self.assertTrue(self.ing.state["takes"][TAKE]["complete"])

    def look(self):
        """The periodic look at the drive, due now."""
        self.now[0] += self.ing.cfg["removed_check_s"] + 1
        self.ing.look_at_drive()

    def save_edit(self, song, edit, age_s):
        folder = os.path.join(self.hubd, "edits", TAKE)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, song + ".json")
        with open(path, "w") as f:
            json.dump(edit, f)
        os.utime(path, (self.now[0] - age_s, self.now[0] - age_s))

    def drive_edit(self, song):
        path = os.path.join(self.folder(), "edits", song, drive_records.WORKING_EDIT)
        return drive_records.read_json(path)


class RecordingTest(DriveRecordsBase):
    def test_take_json_gets_the_take_log_record(self):
        self.write_log({"take_id": "20261114-100000", "aborted": True}, RECORD)
        self.ingest_one()
        self.assertEqual(self.load_take()["recording"], RECORD)

    def test_a_take_ingested_before_gets_the_record_later(self):
        self.ingest_one()                                  # no take log yet
        self.assertNotIn("recording", self.load_take())
        self.look()
        self.write_log(RECORD)
        self.assertEqual(self.ing.update_drive_records(), 1)
        self.assertEqual(self.load_take()["recording"], RECORD)
        self.assertEqual(self.ing.update_drive_records(), 0)   # written once

    def test_nothing_is_written_while_the_rig_is_busy(self):
        self.ingest_one()
        self.look()
        self.write_log(RECORD)
        self.rig.busy = True
        self.assertEqual(self.ing.update_drive_records(), 0)
        self.assertNotIn("recording", self.load_take())


class IndexTest(DriveRecordsBase):
    def test_the_index_lists_takes_and_their_videos(self):
        self.ingest_one()
        song_dir = os.path.join(self.folder(), "edits", "song-a")
        os.makedirs(song_dir)
        with open(os.path.join(song_dir, "song-a-video2.mp4"), "wb") as f:
            f.write(b"x" * 10)
        with open(os.path.join(song_dir, "song-a-video2.json"), "w") as f:
            json.dump({"song_version": 3}, f)
        self.look()
        index = self.ing.state["drive_index"]
        self.assertEqual(index["drive_id"], "6AF9-DC1A")
        self.assertTrue(index["present"])
        self.assertEqual(index["takes"][TAKE]["videos"],
                         {"song-a": [{"number": 2, "file": "edits/song-a/song-a-video2.mp4",
                                      "bounce_version": 3, "bytes": 10}]})

    def test_a_new_take_is_indexed_when_written(self):
        self.look()                                        # empty drive indexed
        self.ingest_one()
        self.assertIn(TAKE, self.ing.state["drive_index"]["takes"])

    def test_the_index_notes_the_drive_going_away(self):
        self.ingest_one()
        self.look()
        self.drive.is_present = False
        self.ing.look_at_drive()
        self.assertFalse(self.ing.state["drive_index"]["present"])
        self.assertIn(TAKE, self.ing.state["drive_index"]["takes"])   # the page shows them greyed

    def test_another_drive_is_indexed_at_once(self):
        self.ingest_one()
        self.look()
        self.drive.uuid = "1TB-NEW-DRIVE"
        shutil.rmtree(os.path.join(self.drive.mp, "takes"))
        self.ing.look_at_drive()                           # not due by time: due because the drive changed
        self.assertEqual(self.ing.state["drive_index"]["drive_id"], "1TB-NEW-DRIVE")
        self.assertEqual(self.ing.state["drive_index"]["takes"], {})
        self.assertIn(TAKE, self.ing.state["takes"])        # still known: it's on the other drive


class WorkingEditTest(DriveRecordsBase):
    def setUp(self):
        super().setUp()
        self.ingest_one()
        self.look()

    def test_a_working_cut_goes_to_the_drive_after_a_pause(self):
        self.save_edit("song-a", {"cuts": [1]}, age_s=10)
        self.assertEqual(self.ing.update_drive_records(), 0)      # still being edited
        self.save_edit("song-a", {"cuts": [1]}, age_s=200)
        self.assertEqual(self.ing.update_drive_records(), 1)
        self.assertEqual(self.drive_edit("song-a"), {"cuts": [1]})
        self.assertEqual(self.ing.update_drive_records(), 0)      # unchanged: not again

    def test_a_changed_cut_is_written_again(self):
        self.save_edit("song-a", {"cuts": [1]}, age_s=200)
        self.ing.update_drive_records()
        self.save_edit("song-a", {"cuts": [1, 2]}, age_s=200)
        self.assertEqual(self.ing.update_drive_records(), 1)
        self.assertEqual(self.drive_edit("song-a"), {"cuts": [1, 2]})

    def test_the_index_knows_the_drive_copy_after_a_new_look(self):
        self.save_edit("song-a", {"cuts": [1]}, age_s=200)
        self.ing.update_drive_records()
        self.look()
        self.assertEqual(self.ing.update_drive_records(), 0)

    def test_no_writes_to_a_drive_that_isnt_the_indexed_one(self):
        self.drive.uuid = "1TB-NEW-DRIVE"                  # swapped, not looked at yet
        self.save_edit("song-a", {"cuts": [1]}, age_s=200)
        self.assertEqual(self.ing.update_drive_records(), 0)


class AdoptTest(DriveRecordsBase):
    """The drive goes into a hub that has never seen its takes."""

    def setUp(self):
        super().setUp()
        self.write_log(RECORD)
        self.ingest_one()
        audio = os.path.join(self.folder(), "audio", "song-a", "v1")
        os.makedirs(audio)
        for name, body in (("sync.json", "{}"), ("bounce.mp3", "mp3")):
            with open(os.path.join(audio, name), "w") as f:
                f.write(body)
        shutil.rmtree(self.ing.cfg["state_dir"])          # a fresh hub: no ingest state
        cfg = self.ing.cfg
        self.make()
        self.ing.cfg.update(hubd_state_dir=cfg["hubd_state_dir"], audio_dir=cfg["audio_dir"],
                            thumbs_dir=cfg["thumbs_dir"], proxies_dir=cfg["proxies_dir"])
        self.look()

    def test_the_take_is_listed_as_ingested(self):
        take = self.ing.state["takes"][TAKE]
        self.assertTrue(take["complete"] and take["adopted"])
        self.assertIn("marked", take["cam1"]["done"])
        self.assertEqual(self.ing.state["drive_index"]["takes"][TAKE]["recording"], RECORD)

    def test_the_cameras_are_not_pulled_again(self):
        self.assertEqual(self.ing.pending(), {})

    def test_its_audio_is_staged_again_and_not_copied_back(self):
        staged = os.path.join(self.ing.cfg["audio_dir"], TAKE, "song-a", "v1", "bounce.mp3")
        self.assertTrue(os.path.exists(staged))
        self.assertEqual(self.ing.audio_pending(), {})

    def test_caches_wait_until_the_editor_opens_it(self):
        self.assertEqual(self.ing.thumbnails_pending(), [])
        with open(os.path.join(self.ing.cmd_dir, "1.json"), "w") as f:
            json.dump({"cmd": "thumbs", "take": TAKE}, f)
        self.ing.handle_commands()
        self.assertEqual(self.ing.thumbnails_pending(), [(TAKE, "cam1")])


if __name__ == "__main__":
    unittest.main()
