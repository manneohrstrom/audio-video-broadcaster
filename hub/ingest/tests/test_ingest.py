import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import ingest  # noqa: E402

TAKE = "20261114-202934"
WC0 = 1794688178_123456789          # some first-frame wall clock (ns)


def sha(b):
    return hashlib.sha256(b).hexdigest()


class FakeRig:
    """Cameras (take folders on disk), hubd status and camnode HTTP."""

    def __init__(self, root):
        self.root = root
        self.busy = False
        self.offline = set()
        self.verified = {}

    def add_take(self, cam, take=TAKE, frames=300, gaps=(), hashed=True, wc0=WC0):
        d = os.path.join(self.root, cam, take)
        os.makedirs(d, exist_ok=True)
        video = (cam + take).encode() * 1000
        with open(os.path.join(d, "video.h264"), "wb") as f:
            f.write(video)
        pts, t = [], 0.0
        for i in range(frames):
            pts.append(t)
            t += 33.32 * (2 if i in gaps else 1)
        with open(os.path.join(d, "video.pts"), "w") as f:
            f.write("# timecode format v2\n" + "".join("%.3f\n" % p for p in pts))
        man = {"camera": cam, "take_id": take, "first_frame_wallclock_ns": wc0, "frames": frames,
               "dropped_frames": len(gaps), "sha256": {"video.h264": sha(video)} if hashed else None}
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(man, f)
        return man

    # HttpJson interface
    def get(self, url, timeout=None):
        if url.endswith("/api/status"):
            mode = "recording" if self.busy else "idle"
            return {"state": mode, "nodes": {c: {"status": {"mode": mode}} for c in os.listdir(self.root)}}
        cam = url.split("//")[1].split(".")[0]
        if cam in self.offline:
            raise OSError("offline")
        out = []
        for take in sorted(os.listdir(os.path.join(self.root, cam))):
            with open(os.path.join(self.root, cam, take, "manifest.json")) as f:
                m = json.load(f)
            if (cam, take) in self.verified:
                m["verified"] = self.verified[(cam, take)]
            out.append(m)
        return out

    def post(self, url, body, timeout=None):
        cam = url.split("//")[1].split(".")[0]
        take = url.split("/takes/")[1].split("/")[0]
        self.verified[(cam, take)] = body
        return {"ok": True}


class FakeRunner:
    """Stands in for rsync/mkvmerge/ffmpeg/ffprobe."""

    def __init__(self, rig, ing_ref):
        self.rig, self.ing_ref = rig, ing_ref
        self.calls = []
        self.frames_override = None
        self.pause_on = None          # tool name that finds the rig busy

    def run(self, cmd, pause="kill", nice=19, timeout=None, on_poll=None):
        tool = cmd[0]
        if on_poll:
            on_poll()
        self.calls.append(tool)
        if self.pause_on == tool or (self.rig.busy and tool == "rsync"):
            raise ingest.Paused()
        if tool == "rsync":
            src, dst = cmd[-2], cmd[-1]
            cam, take = src.split(".local:")[0], src.split(":")[1].strip("/")
            for name in ("video.h264", "video.pts", "manifest.json"):
                shutil.copy(os.path.join(self.rig.root, cam, take, name), dst)
            return ""
        if tool == "mkvmerge":
            with open(cmd[cmd.index("-o") + 1], "wb") as f:
                f.write(b"mkv")
            return ""
        if tool == "ffmpeg" and "-f" in cmd and cmd[cmd.index("-f") + 1] == "mov":
            out = cmd[-1]
            with open(out, "wb") as f:
                f.write(b"MOV" + out.encode())
            self.last_tc = cmd[cmd.index("-timecode") + 1]
            return ""
        if tool == "ffmpeg":                       # decode check
            return ""
        if tool == "ffprobe":
            ing = self.ing_ref[0]
            mov = cmd[-1]
            cam = os.path.basename(mov)[:-4]
            take = os.path.basename(os.path.dirname(mov))
            with open(os.path.join(ing.work, take, cam, "manifest.json")) as f:
                man = json.load(f)
            tc, origin, residual = ingest.grid_origin(man["first_frame_wallclock_ns"])
            pts = ingest.read_pts(os.path.join(ing.work, take, cam, "video.pts"))
            n = self.frames_override or ingest.expected_frames(residual, pts[-1])
            return json.dumps({"streams": [{"nb_read_packets": str(n), "avg_frame_rate": "30/1",
                                            "tags": {"timecode": tc}}]})
        raise AssertionError(cmd)


class FakeDrive:
    def __init__(self, mp):
        self.mp, self.is_present, self.is_mounted, self.ejected, self.clean = mp, True, False, 0, True

    def present(self):
        return self.is_present

    def check(self):
        return self.clean

    def mounted(self):
        return self.is_mounted

    def mode(self):
        return self.mode_ if self.is_mounted else None

    def mount(self, rw=True):
        assert self.is_present
        self.is_mounted, self.mode_ = True, ("rw" if rw else "ro")
        self.mounts = getattr(self, "mounts", []) + [self.mode_]

    def unmount(self):
        self.is_mounted = False

    def eject(self):
        self.is_mounted = False
        self.ejected += 1

    def free_gb(self):
        return 100.0


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.rig = FakeRig(os.path.join(self.tmp, "cams"))
        os.makedirs(self.rig.root)
        for c in ("cam1", "cam2"):
            os.makedirs(os.path.join(self.rig.root, c))
        self.drive = FakeDrive(os.path.join(self.tmp, "studio"))
        os.makedirs(self.drive.mp)
        self.now = [1_000_000.0]
        self.make()

    def make(self):
        cfg = ingest.load_config(None)
        cfg.update(state_dir=os.path.join(self.tmp, "state"), nodes=["cam1", "cam2"])
        ref = []
        self.runner = FakeRunner(self.rig, ref)
        self.ing = ingest.Ingest(cfg, runner=self.runner, drive=self.drive, http=self.rig,
                                 clock=lambda: self.now[0])
        self.ing.runner = self.runner
        ref.append(self.ing)

    def folder(self, take=TAKE):
        return os.path.join(self.drive.mp, "takes", "%s-%s-%s_%s" % (take[:4], take[4:6], take[6:8], take))

    def load_take(self):
        with open(os.path.join(self.folder(), "take.json")) as f:
            return json.load(f)

    def command(self, cmd):
        with open(os.path.join(self.ing.cmd_dir, "%f.json" % time.time()), "w") as f:
            json.dump({"cmd": cmd}, f)


class IngestTest(Base):
    # ---- maths
    def test_grid_origin(self):
        tc, origin, residual = ingest.grid_origin(5 * 10**9 + 50_000_000, tz_localtime=time.gmtime)
        self.assertEqual(tc, "00:00:05:01")                     # 50 ms -> frame 1
        self.assertEqual(origin, 5 * 10**9 + 33_333_333)
        self.assertEqual(residual, 50_000_000 - 33_333_333)

    def test_expected_frames_matches_measured(self):
        # Hub test 2026-11-14: 607 input frames, last pts 20192.6 ms -> 608 conformed frames.
        tc, o, r = ingest.grid_origin(1794678660_850000000)     # residual ~ 16.7 ms
        self.assertIn(ingest.expected_frames(r, 20192.6), (607, 608, 609))
        # A 900 ms gap (27 frames) adds frames to the grid, never shifts later ones.
        self.assertEqual(ingest.expected_frames(0, 1000.0 + 900), ingest.expected_frames(0, 1900.0))

    # ---- flow
    def test_waits_for_idle_period(self):
        self.rig.add_take("cam1")
        self.ing.last_busy = self.now[0] - 60
        self.assertIn("waiting", self.ing.tick())
        self.now[0] += 31 * 60
        self.assertEqual(self.ing.tick(), "ingested")

    def test_full_take_two_cameras(self):
        self.rig.add_take("cam1")
        self.rig.add_take("cam2", gaps=(10, 200))
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        f = self.folder()
        self.assertEqual(sorted(os.listdir(f)), ["cam1.mov", "cam2.mov", "take.json"])
        doc = self.load_take()
        self.assertEqual(sorted(doc["cameras"]), ["cam1", "cam2"])
        self.assertEqual(doc["cameras"]["cam2"]["dropped"], 2)
        self.assertGreater(doc["cameras"]["cam2"]["repeated"], 0)          # gaps filled
        self.assertEqual(doc["cameras"]["cam1"]["timecode"], doc["cameras"]["cam2"]["timecode"])
        self.assertEqual(set(self.rig.verified), {("cam1", TAKE), ("cam2", TAKE)})
        self.assertFalse(os.path.exists(os.path.join(self.ing.work, TAKE)))           # work cleaned
        self.assertFalse(self.drive.is_mounted)                                       # unmounted after
        self.assertEqual(self.ing.tick(), "nothing to ingest")

    def test_busy_pauses_and_resumes(self):
        self.rig.add_take("cam1")
        self.command("start")
        self.runner.pause_on = "ffmpeg"
        self.assertEqual(self.ing.tick(), "paused")
        self.assertFalse(os.path.exists(os.path.join(self.folder(), "cam1.mov")))
        self.assertIn("pulled", self.ing.cam_state(TAKE, "cam1")["done"])
        self.runner.pause_on = None
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        self.assertEqual(self.runner.calls.count("rsync"), 1)                # not re-pulled

    def test_rig_busy_blocks_start(self):
        self.rig.add_take("cam1")
        self.rig.busy = True
        self.command("start")
        self.assertEqual(self.ing.tick(), "rig busy")
        self.assertEqual(self.runner.calls, [])

    def test_sha_mismatch_repulls(self):
        man = self.rig.add_take("cam1")
        man["sha256"]["video.h264"] = "0" * 64
        with open(os.path.join(self.rig.root, "cam1", TAKE, "manifest.json"), "w") as fh:
            json.dump(man, fh)
        self.command("start")
        self.ing.tick()
        cs = self.ing.cam_state(TAKE, "cam1")
        self.assertIn("mismatch", cs["error"])
        self.assertEqual(cs["done"], [])
        self.assertFalse(os.path.exists(os.path.join(self.folder(), "cam1.mov")))
        self.assertEqual(self.rig.verified, {})

    def test_waits_for_camera_hash(self):
        self.rig.add_take("cam1", hashed=False)
        self.command("start")
        self.ing.tick()
        self.assertIn("hashing", self.ing.cam_state(TAKE, "cam1")["error"])
        self.rig.add_take("cam1", hashed=True)
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        self.assertTrue(os.path.exists(os.path.join(self.folder(), "cam1.mov")))

    def test_verify_failure_keeps_take_on_camera(self):
        self.rig.add_take("cam1")
        self.runner.frames_override = 5
        self.command("start")
        self.ing.tick()
        self.assertIn("verify failed", self.ing.cam_state(TAKE, "cam1")["error"])
        self.assertEqual(self.rig.verified, {})
        self.assertFalse(os.path.exists(self.folder()))

    def test_offline_camera_merged_later(self):
        self.rig.add_take("cam1")
        self.rig.add_take("cam2")
        self.rig.offline.add("cam2")
        self.command("start")
        self.ing.tick()
        doc = self.load_take()
        self.assertEqual(sorted(doc["cameras"]), ["cam1"])
        self.rig.offline.clear()
        self.command("start")
        self.ing.tick()
        doc = self.load_take()
        self.assertEqual(sorted(doc["cameras"]), ["cam1", "cam2"])

    def test_failing_camera_does_not_hold_back_others(self):
        self.rig.add_take("cam1")
        self.rig.add_take("cam2")
        self.command("start")
        orig = self.ing.conform

        def conform(take_id, cam, man):
            if cam == "cam2" and fail[0]:
                raise RuntimeError("ffmpeg failed (1): ")
            orig(take_id, cam, man)
        fail = [True]
        self.ing.conform = conform
        self.ing.tick()
        self.assertEqual(set(self.rig.verified), {("cam1", TAKE)})
        self.assertFalse(os.path.exists(os.path.join(self.folder(), "take.json")))   # not complete
        self.assertEqual(set(self.ing.pending()[TAKE]), {"cam2"})
        fail[0] = False                                  # cam2 fixed: take completes later
        self.command("start")
        self.ing.tick()
        doc = self.load_take()
        self.assertEqual(sorted(doc["cameras"]), ["cam1", "cam2"])
        self.assertEqual(doc["manifests"]["cam1"]["camera"], "cam1")      # kept on the hub
        self.assertTrue(self.ing.state["takes"][TAKE]["complete"])
        self.assertFalse(os.path.exists(os.path.join(self.ing.work, TAKE)))

    def test_last_camera_waits_for_take_json(self):
        self.rig.add_take("cam1")
        self.rig.add_take("cam2")
        self.command("start")
        orig = self.ing.write_take_json

        def broken(take_id, cams):
            raise RuntimeError("drive gone")
        self.ing.write_take_json = broken
        with self.assertRaises(RuntimeError):
            self.ing.tick()
        self.assertEqual(set(self.rig.verified), {("cam1", TAKE)})    # cam2 is last: waits
        self.ing.write_take_json = orig
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        self.assertEqual(sorted(self.load_take()["cameras"]), ["cam1", "cam2"])
        self.assertEqual(set(self.rig.verified), {("cam1", TAKE), ("cam2", TAKE)})

    def test_no_drive_and_eject(self):
        self.rig.add_take("cam1")
        self.drive.is_present = False
        self.command("start")
        self.assertIn("no STUDIO drive", self.ing.tick())
        self.drive.is_present = True
        self.command("eject")
        self.ing.tick()
        self.assertEqual(self.drive.ejected, 1)

    def test_dirty_drive_not_written(self):
        self.rig.add_take("cam1")
        self.drive.clean = False
        self.command("start")
        self.assertIn("repair", self.ing.tick())
        self.assertFalse(os.path.exists(self.folder()))

    def test_timecode_from_first_frame(self):
        self.rig.add_take("cam1")
        self.command("start")
        self.ing.tick()
        tc, _, _ = ingest.grid_origin(WC0)
        self.assertEqual(self.runner.last_tc, tc)

    def test_state_survives_restart(self):
        self.rig.add_take("cam1")
        self.command("start")
        self.runner.pause_on = "ffmpeg"
        self.ing.tick()
        self.make()                                   # service restarted
        self.assertIn("pulled", self.ing.cam_state(TAKE, "cam1")["done"])


class ProgressTest(Base):
    """Step progress and ETA shown in the UI."""

    def test_ffmpeg_progress_reads_last_out_time(self):
        p = os.path.join(self.tmp, "progress.txt")
        with open(p, "w") as f:
            f.write("frame=10\nout_time_us=N/A\nprogress=continue\n"
                    "frame=300\nout_time_us=10000000\nprogress=continue\n")
        self.assertAlmostEqual(ingest.ffmpeg_progress(p, 40.0), 0.25)
        self.assertIsNone(ingest.ffmpeg_progress(p + ".missing", 40.0))
        self.assertIsNone(ingest.ffmpeg_progress(p, 0))

    def test_queue_listed_with_eta_before_work_starts(self):
        self.rig.add_take("cam1")
        self.rig.add_take("cam2")
        todo = self.ing.pending()
        self.ing.register(todo)
        self.ing.save()
        cams = self.ing.state["takes"][TAKE]
        self.assertEqual(set(cams), {"cam1", "cam2"})
        self.assertEqual(cams["cam1"]["done"], [])
        self.assertAlmostEqual(cams["cam1"]["duration_s"], 10.0)      # 300 frames, no duration_s
        # Every step at its default rate plus overhead, for both cameras.
        per_cam = sum(ingest.DEFAULT_RATES.values()) * 10.0 + 6 * ingest.STEP_OVERHEAD_S
        self.assertAlmostEqual(self.ing.state["eta_s"], round(2 * per_cam), delta=1)

    def test_eta_uses_progress_of_current_step(self):
        cs = self.ing.cam_state(TAKE, "cam1")
        cs.update(duration_s=600.0, done=["pulled", "checked"])
        self.ing.begin(TAKE, "cam1", "conformed")
        self.now[0] += 100
        self.ing.progress(TAKE, "cam1", 0.25)          # 100 s for 25% -> 300 s left
        later = (ingest.DEFAULT_RATES["verified"] + ingest.DEFAULT_RATES["written"]) * 600 \
            + 3 * ingest.STEP_OVERHEAD_S
        self.assertAlmostEqual(self.ing.cam_eta(cs), 300 + later, delta=1)

    def test_rates_learned_and_skipped_when_rig_was_busy(self):
        cs = self.ing.cam_state(TAKE, "cam1")
        cs["duration_s"] = 100.0
        self.ing.begin(TAKE, "cam1", "conformed")
        self.now[0] += 52                               # 50 s of work + 2 s overhead -> 0.5
        self.ing.mark(TAKE, "cam1", "conformed")
        self.assertAlmostEqual(self.ing.rates()["conformed"], 0.6 * 1.0 + 0.4 * 0.5)
        before = self.ing.rates()["verified"]
        self.ing.begin(TAKE, "cam1", "verified")
        self.now[0] += 500
        self.ing.last_busy = self.now[0] - 1            # paused part-way: don't learn
        self.ing.mark(TAKE, "cam1", "verified")
        self.assertEqual(self.ing.rates()["verified"], before)
        self.assertIsNone(cs.get("step"))

    def test_full_run_ends_with_no_eta_and_no_current_step(self):
        self.rig.add_take("cam1")
        self.command("start")
        self.ing.tick()
        cs = self.ing.state["takes"][TAKE]["cam1"]
        self.assertIn("marked", cs["done"])
        self.assertIsNone(cs.get("step"))
        self.assertIsNone(self.ing.state["eta_s"])
        self.assertEqual(cs["mov_bytes"], os.path.getsize(cs["path"]))


class EjectTest(Base):
    def test_ejected_flag_cleared_when_drive_returns(self):
        self.command("eject")
        self.drive.is_present = False
        self.ing.tick()
        self.assertEqual(self.ing.state["drive"], "ejected")
        self.assertEqual(self.drive.ejected, 1)
        self.drive.is_present = True
        self.ing.tick()
        self.assertIsNone(self.ing.state["drive"])


class RunnerTest(unittest.TestCase):
    def test_sigstop_reports_pause_and_resume(self):
        busy, events = [True], []

        def is_busy():
            if len(events) >= 1:
                busy[0] = False          # busy at first, idle after the pause was reported
            return busy[0]
        r = ingest.Runner(is_busy, poll=0.1, on_pause=events.append)
        r.run([sys.executable, "-c", "import time; time.sleep(0.6)"], pause="stop", nice=0)
        self.assertEqual(events, [True, False])


class PlaybackTest(Base):
    """Read-only mount while the hub UI plays a file back."""

    def lease(self, age_s=0):
        with open(self.ing.lease, "w"):
            pass
        os.utime(self.ing.lease, (self.now[0] - age_s, self.now[0] - age_s))

    def test_fresh_lease_mounts_read_only_stale_unmounts(self):
        self.lease()
        self.ing.settle_drive()
        self.assertEqual(self.drive.mode(), "ro")
        self.lease(age_s=self.ing.cfg["playback_idle_s"] + 1)
        self.ing.settle_drive()
        self.assertIsNone(self.drive.mode())

    def test_no_lease_means_never_mounted_when_idle(self):
        self.ing.tick()
        self.assertFalse(self.drive.mounted())

    def test_write_while_viewing_goes_rw_then_back_to_ro(self):
        self.rig.add_take("cam1")
        self.lease()
        self.command("start")
        self.ing.tick()
        self.assertIn("marked", self.ing.state["takes"][TAKE]["cam1"]["done"])
        self.assertIn("rw", self.drive.mounts)
        self.assertEqual(self.drive.mode(), "ro")          # still viewing afterwards

    def test_write_without_viewer_unmounts_afterwards(self):
        self.rig.add_take("cam1")
        self.command("start")
        self.ing.tick()
        self.assertIn("rw", self.drive.mounts)
        self.assertFalse(self.drive.mounted())

    def test_keeper_leaves_drive_alone_during_a_write(self):
        self.lease(age_s=10_000)                            # stale: keeper would unmount
        with self.ing.writing():
            self.ing.settle_drive()
            self.assertEqual(self.drive.mode(), "rw")
        self.assertFalse(self.drive.mounted())

    def test_eject_drops_lease_and_stays_unmounted(self):
        self.lease()
        self.ing.settle_drive()
        self.command("eject")
        self.ing.tick()
        self.assertFalse(os.path.exists(self.ing.lease))
        self.assertFalse(self.drive.mounted())


class ReplugTest(Base):
    def test_drive_damaged_mid_round_is_not_written(self):
        """Pulled mid-write and plugged back in damaged while the next camera converts."""
        self.rig.add_take("cam1")
        self.rig.add_take("cam2")
        self.command("start")
        orig = self.ing.conform

        def conform(take_id, cam, man):
            orig(take_id, cam, man)
            if cam == "cam2":
                self.drive.clean = False      # replugged, fsck now fails
        self.ing.conform = conform
        self.ing.tick()
        cams = self.ing.state["takes"][TAKE]
        self.assertIn("written", cams["cam1"]["done"])
        self.assertNotIn("written", cams["cam2"]["done"])
        self.assertIn("needs repair", cams["cam2"]["error"])
        self.assertFalse(os.path.exists(os.path.join(self.folder(), "cam2.mov")))
        self.assertNotIn("complete", self.ing.state["takes"][TAKE])
        self.assertIn("marked", cams["cam1"]["done"])       # cam1 is safe: not held back


class RestartTest(Base):
    def test_restart_clears_step_markers_and_retry_clears_error(self):
        cs = self.ing.cam_state(TAKE, "cam1")
        cs.update(done=["pulled", "checked"], error="ffmpeg failed (255): ")
        self.ing.begin(TAKE, "cam2", "checked")      # left behind by a restart
        self.ing.save()
        self.make()                                   # new process, same state dir
        self.assertIsNone(self.ing.cam_state(TAKE, "cam2")["step"])
        self.assertIsNone(self.ing.state["current"])
        self.ing.begin(TAKE, "cam1", "conformed")
        self.assertIsNone(self.ing.cam_state(TAKE, "cam1")["error"])


class DriveTest(unittest.TestCase):
    def test_free_gb_none_when_not_mounted(self):
        # The bare mount point lives on the hub's SD card; its free space must
        # not be shown as the drive's (seen on the rig 2026-11-15: 24.9 GB).
        with tempfile.TemporaryDirectory() as mp:
            d = ingest.Drive({"drive_label": "STUDIO", "mount_point": mp})
            self.assertFalse(d.mounted())
            self.assertIsNone(d.free_gb())

    def test_fsck_reruns_after_replug(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev = os.path.join(tmp, "sda1")
            open(dev, "w").close()
            calls = []
            d = ingest.Drive({"drive_label": "STUDIO", "mount_point": tmp},
                             sh=lambda cmd: calls.append(cmd) or type("R", (), {"returncode": 0})())
            d.dev_path = dev
            self.assertTrue(d.check())
            self.assertTrue(d.check())
            self.assertEqual(len(calls), 1)                 # same plug-in: cached
            os.remove(dev)                                  # unplug, replug: new device node
            time.sleep(0.01)
            open(dev, "w").close()
            self.assertTrue(d.check())
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
