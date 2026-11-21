import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import camnode  # noqa: E402

FAKE = os.path.join(HERE, "fake_rpicam_vid.py")
FAKE_STILL = os.path.join(HERE, "fake_rpicam_still.py")


class Helpers(unittest.TestCase):
    def test_gaps(self):
        pts = [i * 33.333 for i in range(10)]
        self.assertEqual(camnode.find_gaps(pts), [])
        pts = [0, 33.3, 66.7, 133.3, 166.7]          # one frame missing
        g = camnode.find_gaps(pts)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0]["after_frame"], 2)
        self.assertEqual(g[0]["missing"], 1)
        g = camnode.find_gaps([0, 33.3, 333.3])       # 300 ms stall
        self.assertEqual(g[0]["missing"], 8)

    def test_parse_pts(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("# timecode format v2\n0.000\n33.319\n")
        self.assertEqual(camnode.parse_pts(f.name), [0.0, 33.319])
        open(f.name, "w").close()
        self.assertIsNone(camnode.parse_pts(f.name))
        self.assertIsNone(camnode.parse_pts("/nonexistent"))

    def test_scan_truncated_metadata(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write('[\n{\n "SensorTimestamp": 1000000,\n "FrameWallClock": 5\n},\n'
                    '{\n "SensorTimestamp": 34333333,\n "FrameWallClock": 6\n},\n{\n "Sens')
        m = camnode.scan_metadata(f.name)
        self.assertEqual((m["frames"], m["first_wc_ns"], m["last_wc_ns"]), (2, 5, 6))
        self.assertEqual(camnode.pts_from_sensor_ts(m["sensor_ts_ns"]), [0.0, 33.333333])

    def test_live_gap_counter_incremental(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            p = f.name
        per = int(1e9 / 30)

        def frame(i):
            return '{\n "FrameWallClock": %d,\n "SensorTimestamp": 0\n},\n' % (1000 + i * per)
        with open(p, "w") as f:
            f.write("[\n" + "".join(frame(i) for i in range(10)) + '{\n "FrameWall')   # partial line
        c = camnode.LiveGapCounter(p).update()
        self.assertEqual((c.frames, c.dropped), (10, 0))
        with open(p, "a") as f:
            f.write('Clock": %d,\n},\n' % (1000 + 10 * per) + frame(13) + frame(14))  # 2 missing
        c.update()
        self.assertEqual((c.frames, c.dropped, c.gaps), (13, 2, 1))

    def test_cmd_has_locked_settings(self):
        cfg = camnode.load_config(None)
        cmd = camnode.build_cmd(cfg, "client", "/x", cfg["settings"])
        s = " ".join(cmd)
        for want in ("--sync client", "-t 0", "--denoise cdn_off", "--shutter 30000",
                     "--awbgains 1.5,1.5", "--autofocus-mode manual", "--bitrate 16000000",
                     "--mode 2304:1296:10:P", "--metadata-format json"):
            self.assertIn(want, s)


class SyncPortTest(unittest.TestCase):
    def test_tuning_copy_gets_the_port(self):
        d = tempfile.mkdtemp()
        base = os.path.join(d, "base.json")
        with open(base, "w") as f:
            json.dump({"version": 2.0, "algorithms": [{"rpi.black_level": {"black_level": 4096}},
                                                      {"rpi.sync": {}}]}, f)
        path = camnode.sync_tuning(base, 24620, d)
        with open(path) as f:
            t = json.load(f)
        self.assertEqual(t["algorithms"][1], {"rpi.sync": {"port": 24620}})
        self.assertEqual(t["algorithms"][0], {"rpi.black_level": {"black_level": 4096}})
        cmd = camnode.build_cmd(camnode.load_config(None), "client", d, camnode.load_config(None)["settings"], path)
        self.assertEqual(cmd[1:3], ["--tuning-file", path])

    def test_missing_base_falls_back(self):
        self.assertIsNone(camnode.sync_tuning("/nonexistent.json", 24620, tempfile.mkdtemp()))
        cmd = camnode.build_cmd(camnode.load_config(None), "client", "/x", camnode.load_config(None)["settings"])
        self.assertNotIn("--tuning-file", cmd)


class LedTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for name, v in (("trigger", "none timer [actpwr] heartbeat"), ("brightness", "0")):
            with open(os.path.join(self.d, name), "w") as f:
                f.write(v)

    def read(self, name):
        with open(os.path.join(self.d, name)) as f:
            return f.read()

    def test_states_drive_led(self):
        led = camnode.StatusLed(self.d)
        self.assertTrue(led.ok)
        led.set("recording")
        time.sleep(0.1)
        self.assertEqual(self.read("trigger"), "none")
        seen = set()
        for _ in range(30):
            seen.add(self.read("brightness"))
            time.sleep(0.03)
        seen.discard("")                       # read while the LED thread was rewriting the file
        self.assertEqual(seen, {"0", "1"})     # actually blinking
        led.set("idle")
        time.sleep(0.4)
        self.assertEqual(self.read("trigger"), "actpwr")

    def test_missing_led_is_noop(self):
        led = camnode.StatusLed(os.path.join(self.d, "nope"))
        self.assertFalse(led.ok)
        led.set("recording")
        led.restore()


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(tempfile.mkdtemp(), "takes")
        os.makedirs(self.tmp)
        self.cfg = camnode.load_config(None)
        self.cfg.update(takes_dir=self.tmp, rpicam_vid=FAKE, rpicam_still=FAKE_STILL, still_meta_dir=self.tmp, stop_timeout_s=3, min_free_gb=0, led_path="")
        self.live = False
        self.ready_after = 0
        self.patched = []
        self.agent = self.make()

    def make(self):
        return camnode.Agent(self.cfg, probe=lambda d: {"temp_c": 40.0},
                             live_active=lambda: self.live,
                             live_ctl=lambda action: (setattr(self, "live", action == "start") or True, ""),
                             live_ready=lambda: self.live and self.ready_after <= time.time(),
                             live_patch=lambda params: self.patched.append(params))

    def test_snapshot_uses_take_settings_and_overrides(self):
        jpg, meta = self.agent.snapshot()
        self.assertIn(b"lens=1.0", jpg)
        self.assertEqual(meta["AnalogueGain"], 2.0)
        self.assertEqual(meta["settings"]["gain"], 2.0)
        self.assertIn(b"lens=2.5", self.agent.snapshot({"lens_position": 2.5})[0])
        jpg, meta = self.agent.snapshot(auto=True)     # calibration: the camera meters
        self.assertEqual((meta["AnalogueGain"], meta["ColourGains"]), (12.5, [1.9, 2.6]))
        self.assertNotIn("gain", meta["settings"])
        self.assertEqual(self.agent.status()["mode"], "idle")
        with self.assertRaises(ValueError):
            self.agent.snapshot({"bitrate": 1})

    def test_snapshot_failure_reports_and_frees_the_camera(self):
        with self.assertRaises(RuntimeError) as e:
            self.agent.snapshot({"lens_position": 99})
        self.assertIn("no cameras available", str(e.exception))
        self.assertEqual(self.agent.status()["mode"], "idle")

    def test_snapshot_that_hangs_times_out(self):
        self.cfg["snapshot_timeout_s"] = 0.5
        with self.assertRaises(RuntimeError) as e:
            self.agent.snapshot({"lens_position": 97})
        self.assertIn("took over", str(e.exception))     # killable: camera free again
        self.assertEqual(self.agent.status()["mode"], "idle")

    def test_snapshot_blocks_arm_and_is_refused_when_busy(self):
        import threading
        t = threading.Thread(target=self.agent.snapshot, args=({"lens_position": 98},))
        t.start()
        self.wait_state("snapshot")
        with self.assertRaises(camnode.Busy):
            self.agent.arm("20261116-120000", "server")
        with self.assertRaises(camnode.Busy):
            self.agent.snapshot()
        t.join()
        self.live = True
        with self.assertRaises(camnode.Busy):
            self.agent.snapshot()

    def test_live_settings_patch_mediamtx(self):
        with self.assertRaises(camnode.Busy):
            self.agent.live_settings({"gain": 4})          # not live
        self.live = True
        r = self.agent.live_settings({"gain": 4.5, "awb_gains": [1.4, 3.6], "lens_position": 0.8})
        self.assertEqual(self.patched[-1], {"rpiCameraGain": 4.5, "rpiCameraAWBGains": [1.4, 3.6],
                                            "rpiCameraLensPosition": 0.8})
        self.assertEqual(r["applied"], self.patched[-1])
        with self.assertRaises(ValueError):
            self.agent.live_settings({"shutter_us": 10000})
        self.assertEqual(self.agent.status()["settings"]["gain"], 2.0)

    def wait_state(self, want, timeout=3):
        end = time.time() + timeout
        while time.time() < end:
            if self.agent.status()["mode"] == want:
                return
            time.sleep(0.02)
        self.fail("never reached %s (now %s)" % (want, self.agent.status()["mode"]))

    def take(self, take_id="t1", run=0.5, env=None):
        with mock.patch.dict(os.environ, env or {}):
            self.agent.arm(take_id, "client")
            self.assertEqual(self.agent.status()["mode"], "armed")
            self.wait_state("recording")
            time.sleep(run)
            return self.agent.stop()

    def test_clean_take(self):
        man = self.take()
        self.assertEqual(man["exit_code"], 0)
        self.assertEqual(man["pts_source"], "save-pts")
        self.assertGreater(man["frames"], 10)
        self.assertEqual(man["frames"], man["metadata_frames"])
        self.assertEqual(man["dropped_frames"], 0)
        self.assertIsNotNone(man["first_frame_wallclock_ns"])
        self.assertEqual(man["sync_difference_us"], 3)
        with open(os.path.join(self.tmp, "t1", "manifest.json")) as f:
            self.assertEqual(json.load(f)["take_id"], "t1")
        self.assertEqual(self.agent.status()["mode"], "idle")

    def test_crash_rebuilds_pts(self):
        man = self.take(env={"FAKE_CRASH": "1"})
        self.assertEqual(man["exit_code"], 134)
        self.assertEqual(man["pts_source"], "rebuilt-from-metadata")
        self.assertEqual(man["frames"], man["metadata_frames"])
        self.assertTrue(camnode.parse_pts(os.path.join(self.tmp, "t1", "video.pts")))

    def test_dropped_frames_reported(self):
        with mock.patch.dict(os.environ, {"FAKE_DROP": "5,6,20"}):
            self.agent.arm("t0", "client")
            self.wait_state("recording")
            time.sleep(0.5)
            live = self.agent.status()["take"]
            self.assertEqual(live["dropped_so_far"], 3)
            self.agent.stop()
        man = self.take(env={"FAKE_DROP": "5,6,20"})
        self.assertEqual(man["dropped_frames"], 3)
        self.assertEqual([g["missing"] for g in man["gaps"]], [2, 1])

    def test_refusals(self):
        self.live = True
        with self.assertRaises(camnode.Busy):
            self.agent.arm("t1", "client")
        self.live = False
        with self.assertRaises(ValueError):
            self.agent.arm("../evil", "client")
        with self.assertRaises(ValueError):
            self.agent.arm("t1", "boss")
        with self.assertRaises(camnode.Busy):
            self.agent.stop()
        self.agent.arm("t1", "client")
        with self.assertRaises(camnode.Busy):
            self.agent.arm("t2", "client")
        with self.assertRaises(camnode.Busy):
            self.agent.live(True)
        self.agent.stop()
        with self.assertRaises(camnode.Busy):
            self.agent.arm("t1", "client")        # take id already used

    def test_unexpected_exit(self):
        with mock.patch.dict(os.environ, {"FAKE_DIE_AFTER": "0.3"}):
            self.agent.arm("t1", "client")
        time.sleep(1)
        st = self.agent.status()
        self.assertEqual(st["mode"], "idle")
        self.assertIn("unexpectedly", st["last_error"])
        self.assertEqual(st["last_take"]["exit_code"], 1)

    def test_stop_timeout_kills(self):
        self.cfg["stop_timeout_s"] = 0.5
        man = self.take(env={"FAKE_IGNORE_SIGNAL": "1"})
        self.assertEqual(man["stop_reason"], "stop-killed-after-timeout")
        self.assertEqual(man["pts_source"], "rebuilt-from-metadata")

    def test_recover_after_restart(self):
        d = os.path.join(self.tmp, "orphan")
        os.makedirs(d)
        camnode.write_json_atomic(os.path.join(d, "take.json"), {
            "camera": "cam9", "take_id": "orphan", "role": "server", "settings": {}, "armed_at": 1})
        with open(os.path.join(d, "metadata.json"), "w") as f:
            f.write('[\n{\n "SensorTimestamp": 0,\n "FrameWallClock": 7\n}')
        agent = self.make()
        self.assertEqual(agent.last_take["stop_reason"], "recovered-after-agent-restart")
        self.assertEqual(agent.last_take["frames"], 1)

    def test_hash_written(self):
        self.take()
        p = os.path.join(self.tmp, "t1", "manifest.json")
        for _ in range(100):
            with open(p) as f:
                if json.load(f)["sha256"]:
                    return
            time.sleep(0.02)
        self.fail("sha256 never written")

    def test_hash_finished_after_restart(self):
        """A take whose hashing was cut short by a restart is hashed when the agent starts."""
        d = os.path.join(self.tmp, "cut-short")
        os.makedirs(d)
        with open(os.path.join(d, "video.h264"), "wb") as f:
            f.write(b"frames")
        camnode.write_json_atomic(os.path.join(d, "manifest.json"), {"take_id": "cut-short", "sha256": None})
        self.make()
        for _ in range(100):
            with open(os.path.join(d, "manifest.json")) as f:
                if json.load(f)["sha256"]:
                    return
            time.sleep(0.02)
        self.fail("sha256 never written after restart")

    def test_take_files_are_flushed_at_stop(self):
        synced = []
        real_fsync = camnode.os.fsync
        camnode.os.fsync = lambda fd: synced.append(fd) or real_fsync(fd)
        try:
            self.take()
        finally:
            camnode.os.fsync = real_fsync
        self.assertGreaterEqual(len(synced), 2)          # the take's files and its folder

    def test_bad_sync_difference_recorded(self):
        man = self.take(env={"FAKE_SYNC_DIFF": "-183870"})
        self.assertEqual(man["sync_difference_us"], -183870)

    def test_status_responsive_during_live_switch(self):
        import threading
        gate = threading.Event()
        self.agent._live_ctl = lambda action: (gate.wait(2), "")
        t = threading.Thread(target=self.agent.live, args=(False,))
        t.start()
        time.sleep(0.1)
        t0 = time.time()
        self.assertEqual(self.agent.status()["mode"], "switching")
        self.assertLess(time.time() - t0, 0.5)
        with self.assertRaises(camnode.Busy):
            self.agent.arm("t1", "client")
        gate.set()
        t.join()
        self.assertEqual(self.agent.status()["mode"], "idle")

    def test_live_waits_for_stream(self):
        self.ready_after = time.time() + 0.5
        t0 = time.time()
        self.assertEqual(self.agent.live(True), "live")
        self.assertGreaterEqual(time.time() - t0, 0.45)

    def test_live_not_ready_raises(self):
        self.cfg["live_ready_timeout_s"] = 0.3
        self.ready_after = time.time() + 60
        with self.assertRaises(RuntimeError):
            self.agent.live(True)
        self.assertEqual(self.agent.status()["mode"], "live-starting")

    def test_verified_take_is_deleted_at_once(self):
        for t in ("t1", "t2"):
            self.take(t, run=0.2)
        with self.assertRaises(ValueError):
            self.agent.mark_verified("nope", {})
        rec = self.agent.mark_verified("t1", {"mov_sha256": "ab"})
        self.assertEqual(rec["mov_sha256"], "ab")
        self.assertEqual(sorted(os.listdir(self.tmp)), ["t2"])          # t1 gone, t2 (not verified) kept
        self.assertEqual([m["take_id"] for m in self.agent.list_takes()], ["t2"])
        with open(os.path.join(os.path.dirname(self.tmp), "deleted.jsonl")) as f:
            self.assertEqual(json.loads(f.readlines()[-1])["take_id"], "t1")

    def test_verified_while_recording_waits_for_idle(self):
        self.take("t1", run=0.2)
        self.agent._state = "recording"                 # as if the next take were running
        self.agent.mark_verified("t1", {})
        self.assertIn("t1", os.listdir(self.tmp))
        self.agent._state = "idle"
        self.assertEqual(self.agent.delete_verified(), ["t1"])

    def test_takes_verified_earlier_are_deleted_at_start(self):
        self.take("t1", run=0.2)
        camnode.write_json_atomic(os.path.join(self.tmp, "t1", "verified.json"), {"at": 1})
        self.make()
        self.assertNotIn("t1", os.listdir(self.tmp))

    def test_delete_take_from_the_hub_page(self):
        self.take("t1", run=0.2)                         # not verified: a take whose ingest failed
        with self.assertRaises(ValueError):
            self.agent.delete_take("../x")
        self.assertEqual(self.agent.delete_take("t1"), {"deleted": True})
        self.assertNotIn("t1", os.listdir(self.tmp))
        self.assertEqual(self.agent.delete_take("t1"), {"deleted": False})     # gone already: fine
        with open(os.path.join(os.path.dirname(self.tmp), "deleted.jsonl")) as f:
            self.assertEqual(json.loads(f.readlines()[-1])["why"], "deleted on the hub page")

    def test_delete_take_refuses_the_take_being_recorded(self):
        self.take("t1", run=0.2)
        self.agent.take = {"take_id": "t1"}
        with self.assertRaises(camnode.Busy):
            self.agent.delete_take("t1")
        self.assertIn("t1", os.listdir(self.tmp))

    def test_live_toggle(self):
        self.assertEqual(self.agent.live(True), "live")
        self.assertEqual(self.agent.live(False), "idle")


if __name__ == "__main__":
    unittest.main()
