import json, os, sys, tempfile, time, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import camnode  # noqa: E402

FAKE = os.path.join(HERE, "fake_rpicam_vid.py")


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
        for want in ("--sync client", "-t 0", "--denoise cdn_off", "--shutter 20000",
                     "--awbgains 1.5,1.5", "--autofocus-mode manual", "--bitrate 16000000",
                     "--mode 2304:1296:10:P", "--metadata-format json"):
            self.assertIn(want, s)


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.join(tempfile.mkdtemp(), "takes")
        os.makedirs(self.tmp)
        self.cfg = camnode.load_config(None)
        self.cfg.update(takes_dir=self.tmp, rpicam_vid=FAKE, stop_timeout_s=3, min_free_gb=0)
        self.live = False
        self.ready_after = 0
        self.agent = self.make()

    def make(self):
        return camnode.Agent(self.cfg, probe=lambda d: {"temp_c": 40.0},
                             live_active=lambda: self.live,
                             live_ctl=lambda action: (setattr(self, "live", action == "start") or True, ""),
                             live_ready=lambda: self.live and self.ready_after <= time.time())

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
        d = os.path.join(self.tmp, "orphan"); os.makedirs(d)
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

    def test_bad_sync_difference_recorded(self):
        man = self.take(env={"FAKE_SYNC_DIFF": "-183870"})
        self.assertEqual(man["sync_difference_us"], -183870)

    def test_status_responsive_during_live_switch(self):
        import threading
        gate = threading.Event()
        self.agent._live_ctl = lambda action: (gate.wait(2), "")
        t = threading.Thread(target=self.agent.live, args=(False,)); t.start()
        time.sleep(0.1)
        t0 = time.time()
        self.assertEqual(self.agent.status()["mode"], "switching")
        self.assertLess(time.time() - t0, 0.5)
        with self.assertRaises(camnode.Busy):
            self.agent.arm("t1", "client")
        gate.set(); t.join()
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

    def test_verified_and_lazy_delete(self):
        for t in ("t1", "t2", "t3"):
            self.take(t, run=0.2)
        with self.assertRaises(ValueError):
            self.agent.mark_verified("nope", {})
        self.agent.mark_verified("t1", {"mov_sha256": "ab"})
        self.agent.mark_verified("t2", {"mov_sha256": "cd"})
        listed = {m["take_id"]: m.get("verified") for m in self.agent.list_takes()}
        self.assertEqual(listed["t1"]["mov_sha256"], "ab")
        self.assertIsNone(listed["t3"])
        # Plenty of space: nothing deleted.
        self.agent.free_gb = lambda: 50.0
        self.assertEqual(self.agent.lazy_delete(), [])
        # Low space: oldest verified first, one at a time, until above threshold.
        frees = iter([5.0, 5.0, 12.0, 12.0])
        self.agent.free_gb = lambda: next(frees)
        self.assertEqual(self.agent.lazy_delete(), ["t1"])
        # Still low with only an unverified take left besides t2: t2 goes, t3 never.
        self.agent.free_gb = lambda: 1.0
        self.assertEqual(self.agent.lazy_delete(), ["t2"])
        self.assertEqual(sorted(os.listdir(self.tmp)), ["t3"])
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(self.tmp), "deleted.jsonl")))

    def test_live_toggle(self):
        self.assertEqual(self.agent.live(True), "live")
        self.assertEqual(self.agent.live(False), "idle")


if __name__ == "__main__":
    unittest.main()
