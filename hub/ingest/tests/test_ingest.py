import hashlib
import json
import subprocess
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
        self.node_mode = None      # overrides the cameras' mode (e.g. "snapshot")
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
            return {"state": mode, "nodes": {c: {"status": {"mode": self.node_mode or mode}} for c in os.listdir(self.root)}}
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
        self.cmds = getattr(self, "cmds", []) + list(cmd)
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
        if tool == "ffprobe" and "packet=pts_time" in cmd:     # rewrap verify: frame times
            man, pts, residual = self._source(cmd[-1])
            times = [(p + residual / 1e6 + self.time_shift_ms) / 1000 for p in pts]
            if self.frames_override:
                times = times[:self.frames_override]
            return "".join("%.6f\n" % t for t in times)
        if tool == "ffprobe" and "stream_tags=timecode" in cmd:  # rewrap verify: timecode
            man, pts, residual = self._source(cmd[-1])
            return json.dumps({"streams": [{"tags": {"timecode": ingest.grid_origin(
                man["first_frame_wallclock_ns"])[0]}}]})
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

    time_shift_ms = 0.0

    def _source(self, mov):
        ing = self.ing_ref[0]
        cam, take = os.path.basename(mov)[:-4], os.path.basename(os.path.dirname(mov))
        with open(os.path.join(ing.work, take, cam, "manifest.json")) as f:
            man = json.load(f)
        pts = ingest.read_pts(os.path.join(ing.work, take, cam, "video.pts"))
        return man, pts, ingest.grid_origin(man["first_frame_wallclock_ns"])[2]


class FakeDrive:
    def __init__(self, mp):
        self.mp, self.is_present, self.is_mounted, self.ejected, self.clean = mp, True, False, 0, True
        self.uuid = "6AF9-DC1A"

    def filesystem_id(self):
        return self.uuid

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
        cfg.update(state_dir=os.path.join(self.tmp, "state"), nodes=["cam1", "cam2"],
                   phones_dir=os.path.join(self.tmp, "phones"))
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

    def test_rough_sections(self):
        g = lambda at, missing: {"at_s": at, "gap_ms": 33.3 * (missing + 1), "missing": missing}
        self.assertEqual(ingest.rough_sections([]), [])
        self.assertEqual(ingest.rough_sections([g(5, 1), g(30, 2)]), [])           # sparse: fine
        r = ingest.rough_sections([g(10, 1), g(10.5, 1), g(11.9, 1), g(40, 10)], first_wc_ns=10**18)
        self.assertEqual([(x["start_s"], x["missing"]) for x in r], [(10, 3), (40, 10)])
        self.assertAlmostEqual(r[1]["end_s"], 40.366, places=2)
        self.assertEqual(r[0]["start_wallclock_ns"], 10**18 + 10 * 10**9)
        # a run of drops merges into one section
        run = [g(100 + 0.5 * k, 1) for k in range(20)]
        r = ingest.rough_sections(run)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["missing"], 20)

    # ---- flow
    def test_waits_for_idle_period(self):
        self.rig.add_take("cam1")
        self.ing.last_busy = self.now[0] - 60
        self.assertIn("waiting", self.ing.tick())
        self.now[0] += 31 * 60
        self.assertEqual(self.ing.tick(), "ingested")

    def test_full_take_two_cameras(self):
        self.ing.cfg["video_timing"] = "conform"        # the Phase 3 path; "real" in RealTimingTest
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

    def test_snapshots_dont_block_ingest(self):
        self.rig.add_take("cam1")
        self.rig.node_mode = "snapshot"          # calibration watcher taking a still
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")

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

    def test_gives_up_after_repeated_verify_failures(self):
        """A source that can never verify (its end lost in a camera reboot) is tried
        GIVE_UP_AFTER times, then left alone: no convert-and-verify every round."""
        self.rig.add_take("cam1")
        self.runner.frames_override = 5
        for _ in range(ingest.GIVE_UP_AFTER):
            self.command("start")
            self.ing.tick()
        cs = self.ing.cam_state(TAKE, "cam1")
        self.assertTrue(cs["gave_up"])
        self.assertIn("gave up after 3 tries: verify failed", cs["error"])
        self.assertNotIn(TAKE, self.ing.pending())           # not picked up again
        self.assertEqual(self.rig.verified, {})               # and still kept on the camera

    def test_a_success_resets_the_count(self):
        cs = {"done": []}
        self.ing.record_failure(cs, "verified", RuntimeError("verify failed: x"))
        self.ing.record_failure(cs, "verified", RuntimeError("verify failed: x"))
        self.ing.state["takes"][TAKE] = {"cam1": cs}
        self.ing.mark(TAKE, "cam1", "verified")
        self.ing.record_failure(cs, "verified", RuntimeError("verify failed: x"))
        self.assertNotIn("gave_up", cs)                        # 1 in a row, not 3

    def test_transient_failures_never_give_up(self):
        cs = {"done": []}
        for _ in range(5):
            self.ing.record_failure(cs, "written", RuntimeError("drive full"))
        self.assertNotIn("gave_up", cs)

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


class AudioCopyTest(Base):
    def setUp(self):
        super().setUp()
        self.ing.cfg["audio_dir"] = self.adir = os.path.join(self.tmp, "audio")
        self.ing.state["takes"][TAKE] = {}

    def put(self, take, rel, data):
        p = os.path.join(self.adir, take, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_finished_files_of_known_takes_are_copied_once(self):
        self.put(TAKE, "songs.json", b'{"songs": []}')
        self.put(TAKE, "track-1/v1/mix.wav", b"RIFF" + b"\x00" * 100)
        self.put(TAKE, "track-1/v2/mix2.wav.part", b"half")              # still uploading
        self.put("20990101-000000", "songs.json", b"{}")                  # aborted / unknown take
        todo = self.ing.audio_pending()
        self.assertEqual(sorted(r for r, _, _ in todo[TAKE]), ["songs.json", "track-1/v1/mix.wav"])
        self.assertNotIn("20990101-000000", todo)
        self.ing.copy_audio(todo)
        dst = os.path.join(self.folder(), "audio", "track-1", "v1", "mix.wav")
        with open(dst, "rb") as f:
            self.assertEqual(f.read(), b"RIFF" + b"\x00" * 100)
        self.assertEqual(self.ing.audio_pending(), {})
        # songs.json changes when a version is added: copied again
        p = self.put(TAKE, "songs.json", b'{"songs": [1]}')
        os.utime(p, ns=(1, 2))
        self.assertEqual([r for r, _, _ in self.ing.audio_pending()[TAKE]], ["songs.json"])

    def test_tick_copies_audio_when_there_is_no_video_to_ingest(self):
        self.put(TAKE, "scratch/scratch.wav", b"RIFF" + b"\x01" * 10)
        self.ing.manual = True
        self.ing.tick()
        self.assertTrue(os.path.exists(os.path.join(self.folder(), "audio", "scratch", "scratch.wav")))
        self.assertFalse(self.drive.is_mounted)                            # unmounted after writing

    def test_no_copy_while_rig_busy(self):
        self.put(TAKE, "songs.json", b"{}")
        self.rig.busy = True
        self.ing.manual = True
        self.ing.tick()
        self.assertFalse(os.path.exists(os.path.join(self.folder(), "audio")))


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class RenderFixture:
    """A take with two 6 s cameras on the drive and a synced 8 s bounce (real ffmpeg)."""
    ORIGIN = 1_794_688_180_200_000_000          # on the 30 fps grid

    def make_take_and_bounce(self):
        self.ing.cfg.update(audio_dir=os.path.join(self.tmp, "audio"), quick_shot_s=2.0)
        folder = self.folder()
        os.makedirs(folder)
        for cam, src in (("cam1", "testsrc2"), ("cam2", "smptehdbars")):
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "%s=size=640x360:rate=30" % src,
                            "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-y",
                            os.path.join(folder, cam + ".mov")], check=True)
        man = lambda: {"first_frame_wallclock_ns": self.ORIGIN + 10_000_000,          # noqa: E731
                       "last_frame_wallclock_ns": self.ORIGIN + 5_990_000_000, "gaps": []}
        with open(os.path.join(folder, "take.json"), "w") as f:
            json.dump({"take_id": TAKE, "cameras": {}, "manifests": {"cam1": man(), "cam2": man()}}, f)
        d = os.path.join(self.ing.cfg["audio_dir"], TAKE, "song", "v1")
        os.makedirs(d)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-ar", "48000",
                        "-y", os.path.join(d, "mix.wav")], check=True)
        with open(os.path.join(d, "version.json"), "w") as f:
            json.dump({"filename": "mix.wav", "duration_s": 8.0, "song": "Song"}, f)
        with open(os.path.join(d, "sync.json"), "w") as f:      # bounce starts 1 s before the cameras
            json.dump({"bounce_start_ns": self.ORIGIN - 1_000_000_000, "anchor": "test"}, f)
        self.ing.state["takes"][TAKE] = {"complete": True}


class RenderTest(RenderFixture, Base):
    """The quick mix end to end with real ffmpeg: two 6 s cameras, 2 s shots."""

    def setUp(self):
        super().setUp()
        self.make_take_and_bounce()

    def queue(self):
        with open(os.path.join(self.ing.cmd_dir, "1-render.json"), "w") as f:
            json.dump({"cmd": "render", "take": TAKE, "song": "song", "version": 1}, f)

    def test_render_written_to_drive_and_checked(self):
        # Like the real drive: its files are out of reach once it's unmounted.
        real_getsize = os.path.getsize

        def getsize(p):
            if p.startswith(self.drive.mp) and not self.drive.is_mounted:
                raise FileNotFoundError(p)
            return real_getsize(p)
        os.path.getsize = getsize
        self.addCleanup(setattr, os.path, "getsize", real_getsize)
        self.queue()
        self.ing.tick()
        job = self.ing.state["renders"][TAKE + "/song/v1"]
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(job["file"], "edits/song/song-v1-quick.mp4")
        self.assertEqual(job["checked"]["frames"], job["frames"])
        self.assertGreaterEqual(job["checked"]["spot_checks"], 2)
        with open(os.path.join(self.folder(), "edits", "song", "song-v1-quick.json")) as f:
            edit = json.load(f)
        self.assertEqual([c["camera"] for c in edit["cuts"]], ["cam1", "cam2", "cam1"])
        self.assertEqual(edit["made_by"], "quick-v1")
        self.assertFalse(self.drive.is_mounted)
        # Again: a second file, the first one is never replaced
        self.queue()
        self.ing.tick()
        self.assertTrue(os.path.exists(os.path.join(self.folder(), "edits", "song", "song-v1-quick-2.mp4")))

    def test_waits_for_the_take_and_needs_sync(self):
        self.ing.state["takes"][TAKE] = {}
        self.queue()
        self.ing.tick()                      # copies the take's audio; the render waits
        self.ing.tick()
        self.assertEqual(self.ing.state["renders"][TAKE + "/song/v1"]["status"], "queued")
        self.assertNotEqual(self.ing.state["status"], "ingesting")   # waiting isn't working
        self.ing.state["takes"][TAKE] = {"complete": True}
        os.remove(os.path.join(self.ing.cfg["audio_dir"], TAKE, "song", "v1", "sync.json"))
        self.ing.tick()
        job = self.ing.state["renders"][TAKE + "/song/v1"]
        self.assertEqual(job["status"], "error")
        self.assertIn("not synced", job["error"])


class EditRenderYouTubeTest(RenderFixture, Base):
    """The editor's video with "Upload to YouTube": after the drive has it, hub-ingest's copy
    goes to the upload folder with a record for hub-upload. The render itself is stubbed."""

    def setUp(self):
        super().setUp()
        self.make_take_and_bounce()
        state = os.path.join(self.tmp, "hubd")
        self.spool = os.path.join(self.tmp, "youtube", "uploads")
        static = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "hubd", "static")
        self.ing.cfg.update(hubd_state_dir=state, youtube_spool_dir=self.spool,
                            looks_path=os.path.join(static, "editor", "looks.json"),
                            fonts_dir=os.path.join(static, "shared", "vendor", "fonts"))
        os.makedirs(os.path.join(state, "edits", TAKE))
        with open(os.path.join(state, "edits", TAKE, "song.json"), "w") as f:
            json.dump({"cuts": [{"at_ns": 0, "camera": "cam1"}],
                       "title": {"on": True, "text": "Blue in Green", "lines": ["Anna, piano"]}}, f)

        class FakeRenderer:
            def __init__(self, *args, **kwargs):
                self.total_frames, self.shots = 30, [None]

            def run(self, out):
                with open(out, "wb") as f:
                    f.write(b"\0" * 5000)
        originals = (ingest.edit_render.EditRenderer, ingest.render.probe_output)
        ingest.edit_render.EditRenderer = FakeRenderer
        ingest.render.probe_output = lambda path: {"frames": 30, "rate": "30/1", "size": (1920, 1080), "duration_s": 1.0}
        self.addCleanup(setattr, ingest.edit_render, "EditRenderer", originals[0])
        self.addCleanup(setattr, ingest.render, "probe_output", originals[1])

    def queue_edit(self, youtube, take_name=None):
        with open(os.path.join(self.ing.cmd_dir, "1-render.json"), "w") as f:
            json.dump({"cmd": "render", "take": TAKE, "song": "song", "version": 1, "kind": "edit",
                       "youtube": youtube, "song_name": "Song name", "take_name": take_name}, f)

    def test_the_takes_name_goes_into_the_description(self):
        self.queue_edit(True, take_name="Sunday jam")
        self.ing.tick()
        job = self.ing.state["renders"][TAKE + "/song:edit"]
        with open(os.path.join(self.spool, job["upload"] + ".json")) as f:
            description = json.load(f)["metadata"]["snippet"]["description"]
        self.assertTrue(description.endswith("Sunday jam, recorded %s-%s-%s." % (TAKE[:4], TAKE[4:6], TAKE[6:8])), description)

    def test_the_video_goes_to_the_upload_folder(self):
        self.queue_edit(True)
        self.ing.tick()
        job = self.ing.state["renders"][TAKE + "/song:edit"]
        self.assertEqual(job["status"], "done", job.get("error"))
        self.assertEqual(job["upload"], TAKE + "_song_video1")
        self.assertTrue(os.path.exists(os.path.join(self.folder(), "edits", "song", "song-video1.mp4")))
        with open(os.path.join(self.spool, job["upload"] + ".json")) as f:
            record = json.load(f)
        self.assertEqual(record["status"], "queued")
        self.assertEqual(record["metadata"]["snippet"]["title"], "Blue in Green")
        self.assertEqual(record["metadata"]["status"]["privacyStatus"], "private")
        self.assertEqual(record["file"], "edits/song/song-video1.mp4")
        self.assertEqual(os.path.getsize(os.path.join(self.spool, job["upload"] + ".mp4")), 5000)
        self.assertEqual(os.listdir(os.path.join(self.ing.work, "renders", TAKE)), [])     # nothing left behind

    def test_without_youtube_nothing_is_queued(self):
        self.queue_edit(False)
        self.ing.tick()
        job = self.ing.state["renders"][TAKE + "/song:edit"]
        self.assertEqual((job["status"], job["upload"]), ("done", None))
        self.assertFalse(os.path.exists(self.spool))


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

    def test_free_space_kept_while_drive_unmounted(self):
        """Like the real drive: free space is only readable while mounted."""
        self.drive.free_gb = lambda: 42.0 if self.drive.is_mounted else None
        self.drive.is_mounted = True
        self.assertEqual(self.ing.drive_free(), 42.0)
        self.drive.is_mounted = False
        self.ing.tick()                                  # idle tick: drive unmounted
        self.assertEqual(self.ing.state["drive_free_gb"], 42.0)
        self.drive.is_present = False                    # unplugged: forget it
        self.ing.tick()
        self.assertIsNone(self.ing.state["drive_free_gb"])


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



def add_phone(root, part="pauls-pixel", take=TAKE, frames=300, complete=True, wc0=WC0 + 5_000_000, gaps=()):
    """A phone recording as hubd stages it (docs/phone-plan.md)."""
    d = os.path.join(root, take, part)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "video.h264"), "wb") as f:
        f.write(part.encode() * 1000)
    pts, t = [], 0.0
    for i in range(frames):
        pts.append(t)
        t += 33.34 * (3 if i in gaps else 1)
    with open(os.path.join(d, "video.pts"), "w") as f:
        f.write("# timecode format v2\n" + "".join("%.3f\n" % p for p in pts))
    man = {"camera": part, "label": "Paul's Pixel", "take_id": take, "source": "phone", "complete": complete,
           "frames": frames if complete else None, "first_frame_wallclock_ns": wc0, "fps_measured": 29.99,
           "duration_s": pts[-1] / 1000, "dropped_frames": 2 * len(gaps), "files": {"video.h264": 1, "video.pts": 1}}
    with open(os.path.join(d, "manifest.json"), "w") as f:
        json.dump(man, f)
    return d


class PhoneIngestTest(Base):
    def test_phone_take_alongside_cameras(self):
        self.rig.add_take("cam1")
        src = add_phone(self.ing.cfg["phones_dir"], gaps=(50,))
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        self.assertEqual(sorted(os.listdir(self.folder())), ["cam1.mov", "pauls-pixel.mov", "take.json"])
        doc = self.load_take()
        self.assertEqual(doc["cameras"]["pauls-pixel"]["timecode"], doc["cameras"]["cam1"]["timecode"])
        self.assertEqual(doc["manifests"]["pauls-pixel"]["label"], "Paul's Pixel")
        self.assertEqual(doc["cameras"]["pauls-pixel"]["dropped"], 2)
        self.assertEqual(len(doc["cameras"]["pauls-pixel"]["rough"]), 0)              # one 2-frame gap: not rough
        self.assertEqual(self.runner.calls.count("rsync"), 1)                         # cam1 only; the phone is linked
        self.assertEqual(set(self.rig.verified), {("cam1", TAKE)})                    # no HTTP for the phone
        self.assertFalse(os.path.exists(src))                                          # staging copy freed
        self.assertFalse(any("setpts" in c for c in self.runner.cmds if isinstance(c, str)))   # see conform()
        self.assertFalse(os.path.exists(os.path.dirname(src)))
        self.assertEqual(self.ing.tick(), "nothing to ingest")

    def test_incomplete_phone_waits(self):
        add_phone(self.ing.cfg["phones_dir"], complete=False)
        self.command("start")
        self.assertEqual(self.ing.tick(), "nothing to ingest")
        add_phone(self.ing.cfg["phones_dir"])                                          # the phone sent the rest
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        self.assertIn("pauls-pixel.mov", os.listdir(self.folder()))

    def test_phone_frame_count_mismatch_is_an_error(self):
        d = add_phone(self.ing.cfg["phones_dir"])
        with open(os.path.join(d, "manifest.json")) as f:
            m = json.load(f)
        m["frames"] = 999
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(m, f)
        self.command("start")
        self.ing.tick()
        cs = self.ing.state["takes"][TAKE]["pauls-pixel"]
        self.assertIn("999", cs["error"])
        self.assertTrue(os.path.exists(d))                                             # kept for another try

    def test_rejoined_parts_are_separate_clips(self):
        add_phone(self.ing.cfg["phones_dir"])
        add_phone(self.ing.cfg["phones_dir"], part="pauls-pixel-part2", wc0=WC0 + 60 * 10**9)
        self.command("start")
        self.ing.tick()
        self.assertEqual(sorted(os.listdir(self.folder())), ["pauls-pixel-part2.mov", "pauls-pixel.mov", "take.json"])


if __name__ == "__main__":
    unittest.main()


class RealTimingTest(Base):
    """video_timing "real" (docs/editor-plan.md M-E1): -c copy, frames at their real times."""

    def setUp(self):
        super().setUp()
        self.ing.cfg["video_timing"] = "real"

    def test_rewrap_keeps_every_frame(self):
        self.rig.add_take("cam1", gaps=(10, 200))
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        cs = self.load_take()["cameras"]["cam1"]
        self.assertEqual(cs["timing"], "real")
        self.assertEqual((cs["frames_out"], cs["frames_in"], cs["repeated"]), (300, 300, 0))
        self.assertEqual(cs["timecode"], ingest.grid_origin(WC0)[0])
        self.assertIn("copy", self.runner.cmds)
        self.assertNotIn("libx264", self.runner.cmds)
        self.assertIn("-movie_timescale", self.runner.cmds)     # else the edit list rounds to ms
        i = self.runner.cmds.index("-itsoffset")
        self.assertAlmostEqual(float(self.runner.cmds[i + 1]), ingest.grid_origin(WC0)[2] / 1e9, places=6)
        self.assertIn(("cam1", TAKE), self.rig.verified)

    def test_frame_times_off_fails_verify(self):
        self.rig.add_take("cam1")
        self.runner.time_shift_ms = 1.5
        self.command("start")
        self.ing.tick()
        cs = self.ing.cam_state(TAKE, "cam1")
        self.assertIn("frame times off", cs["error"])
        self.assertNotIn("verified", cs["done"])
        self.assertEqual(self.rig.verified, {})

    def test_missing_frames_fail_verify(self):
        self.rig.add_take("cam1")
        self.runner.frames_override = 299
        self.command("start")
        self.ing.tick()
        self.assertIn("frames 299, expected 300", self.ing.cam_state(TAKE, "cam1")["error"])

    def test_camera_conformed_before_the_switch_verifies_as_conformed(self):
        self.rig.add_take("cam1")
        self.ing.cfg["video_timing"] = "conform"
        self.runner.pause_on = "ffprobe"            # stop after the conform, before verify
        self.command("start")
        self.ing.tick()
        self.assertEqual(self.ing.cam_state(TAKE, "cam1")["timing"], "conform")
        self.ing.cfg["video_timing"] = "real"
        self.runner.pause_on = None
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        cs = self.load_take()["cameras"]["cam1"]
        self.assertEqual(cs["timing"], "conform")
        self.assertNotIn("max_time_err_ms", cs)

    def test_phone_rewrapped(self):
        add_phone(self.ing.cfg["phones_dir"], gaps=(50,))
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        cs = self.load_take()["cameras"]["pauls-pixel"]
        self.assertEqual((cs["timing"], cs["repeated"]), ("real", 0))


class FakeSyncProc:
    """Stands in for `nice python3 audiosync.py ...`."""
    def __init__(self, out, polls=1):
        self.out, self.left, self.killed = out, polls, False

    def poll(self):
        if self.killed:
            return -9
        self.left -= 1
        return None if self.left >= 0 else 0

    def kill(self):
        self.killed = True

    def wait(self):
        return -9

    def communicate(self):
        return self.out, ""


class SyncJobTest(Base):
    def queue(self, v=1):
        with open(os.path.join(self.ing.cmd_dir, "%f-sync.json" % time.time()), "w") as f:
            json.dump({"cmd": "sync", "take": TAKE, "song": "track-1", "version": v}, f)
        self.ing.handle_commands()

    def test_synced(self):
        self.queue()
        cmds = []

        def popen(cmd, **kw):
            cmds.append(cmd)
            return FakeSyncProc(json.dumps({"bounce_start_ns": 1, "anchor": "hub mic: music", "confidence": "high"}))
        self.ing.run_syncs(popen=popen, poll=0)
        job = self.ing.state["syncs"]["%s/track-1/v1" % TAKE]
        self.assertEqual((job["status"], job["anchor"], job["confidence"]), ("synced", "hub mic: music", "high"))
        self.assertEqual(cmds[0][:3], ["nice", "-n", "19"])
        self.assertTrue(cmds[0][4].endswith("audiosync.py"))

    def test_needs_sync(self):
        self.queue()
        self.ing.run_syncs(popen=lambda cmd, **kw: FakeSyncProc(json.dumps({"failed": True, "reason": "no hub mic recording"})), poll=0)
        job = self.ing.state["syncs"]["%s/track-1/v1" % TAKE]
        self.assertEqual((job["status"], job["reason"]), ("needs sync", "no hub mic recording"))

    def test_rec_stops_the_sync_and_requeues_it(self):
        self.queue()
        procs = []

        def popen(cmd, **kw):
            procs.append(FakeSyncProc("{}", polls=5))
            return procs[-1]
        orig = self.ing.rig_busy
        calls = [0]

        def busy():
            calls[0] += 1
            return calls[0] > 1                    # idle at the start, then REC
        self.ing.rig_busy = busy
        self.ing.run_syncs(popen=popen, poll=0)
        self.assertTrue(procs[0].killed)
        self.assertEqual(self.ing.state["syncs"]["%s/track-1/v1" % TAKE]["status"], "queued")
        self.ing.rig_busy = orig
        self.ing.run_syncs(popen=lambda cmd, **kw: FakeSyncProc(json.dumps({"anchor": "a", "confidence": "high"})), poll=0)
        self.assertEqual(self.ing.state["syncs"]["%s/track-1/v1" % TAKE]["status"], "synced")

    def test_same_sync_not_queued_twice(self):
        self.queue()
        self.queue()
        self.assertEqual(len(self.ing.state["syncs"]), 1)


class ThumbnailStepTest(Base):
    def test_ingested_takes_get_thumbnails_and_failures_are_not_retried(self):
        self.ing.cfg["thumbs_dir"] = os.path.join(self.tmp, "thumbs")
        self.rig.add_take("cam1")
        self.rig.add_take("cam2")
        made = []

        def fake_make(mov, out_dir):
            made.append(os.path.basename(mov))
            if "cam2" in mov:
                raise RuntimeError("no keyframes")
            os.makedirs(out_dir)
            with open(os.path.join(out_dir, "index.json"), "w") as f:
                f.write("{}")
        orig = ingest.thumbnails.make_thumbnails
        ingest.thumbnails.make_thumbnails = fake_make
        try:
            self.command("start")
            self.assertEqual(self.ing.tick(), "ingested")
            self.assertEqual(sorted(made), ["cam1.mov", "cam2.mov"])
            self.assertEqual(self.ing.thumbnails_pending(), [])          # cam2 failed: not retried
            self.assertIn("%s/cam2" % TAKE, self.ing.state["thumbs_failed"])
        finally:
            ingest.thumbnails.make_thumbnails = orig


class ProxyStepTest(Base):
    def test_no_proxies_for_takes_without_a_synced_song(self):
        self.ing.cfg["audio_dir"] = os.path.join(self.tmp, "no-audio")
        self.ing.state["takes"] = {TAKE: {"complete": True, "cam1": {"frames_cam": 10}}}
        self.assertEqual(self.ing.proxies_pending(), [])

    def test_ingested_takes_get_proxies(self):
        self.ing.cfg["thumbs_dir"] = os.path.join(self.tmp, "thumbs")
        self.ing.cfg["proxies_dir"] = os.path.join(self.tmp, "proxies")
        self.ing.cfg["audio_dir"] = os.path.join(self.tmp, "audio")
        os.makedirs(os.path.join(self.tmp, "audio", TAKE, "song", "v1"))
        open(os.path.join(self.tmp, "audio", TAKE, "song", "v1", "sync.json"), "w").close()   # editable
        self.rig.add_take("cam1")
        made, seen = [], []

        def fake_make(source, destination, **kw):
            made.append(os.path.basename(destination))
            seen.append(dict(self.ing.state["activity"]))            # what the hub page shows meanwhile
            kw["progress"](0.5)
            seen.append(dict(self.ing.state["activity"]))
            kw["checking"]()
            seen.append(dict(self.ing.state["activity"]))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            open(destination, "w").close()
        orig_proxy, orig_thumbs = ingest.proxies.make_proxy, ingest.thumbnails.make_thumbnails
        ingest.proxies.make_proxy = fake_make
        ingest.thumbnails.make_thumbnails = lambda mov, out: (os.makedirs(out), open(os.path.join(out, "index.json"), "w").close())
        try:
            self.command("start")
            self.assertEqual(self.ing.tick(), "ingested")
            self.assertEqual(made, ["cam1.mp4"])
            self.assertEqual(self.ing.proxies_pending(), [])
            self.assertEqual({k: seen[0][k] for k in ("kind", "take", "camera", "index", "count", "phase", "progress")},
                             {"kind": "proxy", "take": TAKE, "camera": "cam1", "index": 1, "count": 1,
                              "phase": "encoding", "progress": None})
            self.assertEqual(seen[1]["progress"], 0.5)
            self.assertEqual((seen[2]["phase"], seen[2]["progress"]), ("checking", None))
            self.assertIsNone(self.ing.state["activity"])                   # cleared when the round is done
        finally:
            ingest.proxies.make_proxy, ingest.thumbnails.make_thumbnails = orig_proxy, orig_thumbs
        prep = self.ing.state["takes"][TAKE]["cam1"]["prep"]                 # brought up to date as it goes
        self.assertEqual((prep["thumbnails"], prep["proxy"]), ({"state": "done"}, {"state": "done"}))


class PrepStateTest(Base):
    """Each ingested camera's thumbnails, preview copy and movement, for the archive's step chips."""

    def setUp(self):
        super().setUp()
        for name in ("thumbs", "proxies", "audio"):
            self.ing.cfg[name + "_dir"] = os.path.join(self.tmp, name)
        self.ing.state["takes"] = {TAKE: {"complete": True, "cam1": {"frames_cam": 10}, "cam2": {"frames_cam": 10}},
                                   "20261101-120000": {"cam1": {"frames_cam": 10}}}           # still ingesting

    def prep(self, camera, take=TAKE):
        self.ing.update_prep()
        return {step: value["state"] for step, value in self.ing.state["takes"][take][camera]["prep"].items()}

    def test_preview_copies_wait_for_a_synced_song(self):
        self.assertEqual(self.prep("cam1"), {"thumbnails": "waiting", "proxy": "later", "motion": "later"})
        self.assertNotIn("prep", self.ing.state["takes"]["20261101-120000"]["cam1"])
        os.makedirs(os.path.join(self.tmp, "audio", TAKE, "song", "v1"))
        open(os.path.join(self.tmp, "audio", TAKE, "song", "v1", "sync.json"), "w").close()
        self.assertEqual(self.prep("cam1"), {"thumbnails": "waiting", "proxy": "waiting", "motion": "waiting"})

    def test_done_and_failed(self):
        os.makedirs(os.path.join(self.tmp, "audio", TAKE, "song", "v1"))
        open(os.path.join(self.tmp, "audio", TAKE, "song", "v1", "sync.json"), "w").close()
        os.makedirs(os.path.join(self.tmp, "thumbs", TAKE, "cam1"))
        open(os.path.join(self.tmp, "thumbs", TAKE, "cam1", "index.json"), "w").close()
        os.makedirs(os.path.join(self.tmp, "proxies", TAKE))
        open(os.path.join(self.tmp, "proxies", TAKE, "cam1.mp4"), "w").close()
        self.ing.state["proxies_failed"] = {"%s/cam2" % TAKE: ingest.failure("proxy has 9 frames, the original 10")}
        self.assertEqual(self.prep("cam1"), {"thumbnails": "done", "proxy": "done", "motion": "waiting"})
        self.assertEqual(self.prep("cam2")["proxy"], "failed")
        self.assertIn("9 frames", self.ing.state["takes"][TAKE]["cam2"]["prep"]["proxy"]["error"])

    def test_an_adopted_take_waits_for_the_editor(self):
        self.ing.state["takes"][TAKE]["adopted"] = True
        self.assertEqual(self.prep("cam1"), {"thumbnails": "later", "proxy": "later", "motion": "later"})
        self.ing.state["takes"][TAKE]["caches_wanted"] = True
        self.assertEqual(self.prep("cam1")["thumbnails"], "waiting")


class AnalysisStepTest(Base):
    """beats.json per bounce version and motion.json per proxied camera, for the automatic cut."""

    def add_version(self, song="song", version="v1", filename="bounce.wav", file_too=True):
        version_dir = os.path.join(self.tmp, "audio", TAKE, song, version)
        os.makedirs(version_dir)
        with open(os.path.join(version_dir, "version.json"), "w") as f:
            json.dump({"filename": filename}, f)
        if file_too:
            open(os.path.join(version_dir, filename), "w").close()
        return version_dir

    def test_every_staged_version_is_analysed_once(self):
        self.ing.cfg["audio_dir"] = os.path.join(self.tmp, "audio")
        done = self.add_version(version="v1")
        open(os.path.join(done, "beats.json"), "w").close()
        waiting = self.add_version(version="v2")
        self.add_version(version="v3", file_too=False)          # still uploading
        os.makedirs(os.path.join(self.tmp, "audio", TAKE, "scratch"))
        pending = self.ing.beats_pending()
        self.assertEqual([key for key, _, _ in pending], ["%s/song/v2" % TAKE])
        self.assertEqual(pending[0][2], os.path.join(waiting, "beats.json"))

    def test_analysis_runs_niced_and_a_failure_is_not_retried(self):
        self.ing.cfg["audio_dir"] = os.path.join(self.tmp, "audio")
        self.add_version()
        commands = []

        def popen(command, **kw):
            commands.append(command)
            process = FakeSyncProc("")
            process.returncode = 1
            process.communicate = lambda: ("", "too short to find a tempo")
            return process
        self.ing.run_beat_analyses(self.ing.beats_pending(), popen=popen, poll=0)
        self.assertEqual(commands[0][:3], ["nice", "-n", "19"])
        self.assertTrue(commands[0][4].endswith("beats.py"))
        self.assertIn("too short", self.ing.state["beats_failed"]["%s/song/v1" % TAKE]["error"])
        self.assertEqual(self.ing.beats_pending(), [])

    def test_rec_stops_the_analysis(self):
        self.ing.cfg["audio_dir"] = os.path.join(self.tmp, "audio")
        self.add_version()
        processes = []

        def popen(command, **kw):
            processes.append(FakeSyncProc("", polls=5))
            return processes[-1]
        calls = [0]

        def busy():
            calls[0] += 1
            return calls[0] > 1
        self.ing.rig_busy = busy
        self.ing.run_beat_analyses(self.ing.beats_pending(), popen=popen, poll=0)
        self.assertTrue(processes[0].killed)
        self.assertNotIn("beats_failed", self.ing.state)          # not a failure: tried again later

    def test_proxied_cameras_get_motion_readings(self):
        proxies_dir = os.path.join(self.tmp, "proxies")
        self.ing.cfg["proxies_dir"] = proxies_dir
        os.makedirs(os.path.join(proxies_dir, TAKE))
        for camera in ("cam1", "cam2"):
            open(os.path.join(proxies_dir, TAKE, camera + ".mp4"), "w").close()
        self.assertEqual(self.ing.motion_pending(), [(TAKE, "cam1"), (TAKE, "cam2")])
        original = ingest.motion.measure

        def fake_measure(path):
            if "cam2" in path:
                raise ValueError("too few keyframes")
            return {"version": 1, "times_s": [0.5], "motion": [1.0], "noise_floor": 0.0}
        ingest.motion.measure = fake_measure
        try:
            self.ing.measure_motion(self.ing.motion_pending())
        finally:
            ingest.motion.measure = original
        self.assertTrue(os.path.exists(os.path.join(proxies_dir, TAKE, "cam1.motion.json")))
        self.assertIn("%s/cam2" % TAKE, self.ing.state["motion_failed"])
        self.assertEqual(self.ing.motion_pending(), [])


class RemovedTakesTest(Base):
    def ingest_one(self):
        self.ing.cfg.update(thumbs_dir=os.path.join(self.tmp, "thumbs"), proxies_dir=os.path.join(self.tmp, "proxies"),
                            hubd_state_dir=os.path.join(self.tmp, "hubd"), audio_dir=os.path.join(self.tmp, "audio"))
        self.rig.add_take("cam1")
        self.command("start")
        self.assertEqual(self.ing.tick(), "ingested")
        self.assertTrue(self.ing.state["takes"][TAKE]["complete"])
        for folder in ("thumbs", "proxies", "hubd/edits", "audio"):
            os.makedirs(os.path.join(self.tmp, folder, TAKE), exist_ok=True)

    def look(self):
        self.drive.is_mounted = True
        self.now[0] += 10_000
        return self.ing.forget_removed_takes()

    def test_a_take_deleted_from_its_drive_is_forgotten_everywhere(self):
        self.ingest_one()
        self.assertEqual(self.look(), [])                        # still there: notes the drive
        self.assertEqual(self.ing.state["takes"][TAKE]["drive_id"], "6AF9-DC1A")
        shutil.rmtree(self.folder())                             # the user deletes it on the Mac
        self.assertEqual(self.look(), [TAKE])
        self.assertNotIn(TAKE, self.ing.state["takes"])
        self.assertEqual(self.ing.state["removed_takes"][TAKE]["why"], "deleted from the drive")
        for folder in ("thumbs", "proxies", "hubd/edits", "audio"):
            self.assertFalse(os.path.exists(os.path.join(self.tmp, folder, TAKE)))
        self.assertEqual(self.ing.pending(), {})                 # the camera copy is verified: never pulled again

    def test_another_drive_removes_nothing(self):
        self.ingest_one()
        self.look()
        shutil.rmtree(self.folder())
        self.drive.uuid = "1TB-NEW-DRIVE"                        # a different drive with the same label
        self.assertEqual(self.look(), [])
        self.assertIn(TAKE, self.ing.state["takes"])


class PulledBytesTest(unittest.TestCase):
    """The pull's progress (ingest.pulled_bytes): rsync's temporary replaces, not adds to,
    a partial file kept from a stopped pull."""

    def write(self, name, size):
        with open(os.path.join(self.folder, name), "wb") as f:
            f.write(b"\0" * size)

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)     # videos: /tmp is RAM on the hub

    def test_first_pull_counts_the_temporary(self):
        self.write("manifest.json", 10)
        self.write(".video.h264.Ab12Cd", 300)
        self.assertEqual(ingest.pulled_bytes(self.folder), 310)

    def test_resumed_pull_counts_the_partial_copy_once(self):
        self.write("manifest.json", 10)
        self.write("video.h264", 700)                 # kept from the stopped pull
        self.write(".video.h264.zEmXtj", 400)         # the new copy, built from it and the rest
        self.assertEqual(ingest.pulled_bytes(self.folder), 410)

    def test_finished_files_and_the_mkv_scratch(self):
        self.write("video.h264", 1000)
        self.write("video.pts", 50)
        self.write("ts.mkv", 999)                     # the conform's scratch file, not pulled
        self.assertEqual(ingest.pulled_bytes(self.folder), 1050)
