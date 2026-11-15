import os
import sys
import tempfile
import threading
import unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import hubd  # noqa: E402

GOOD = {"mode": "idle", "clock_synced": True, "clock_offset_ms": 0.1, "temp_c": 45.0,
        "free_hours": 10.0, "wifi_powersave": False, "throttled": 0x50000}


class FakeRig:
    """Simulates camnode agents: clients record once the server is armed."""
    def __init__(self, names):
        self.st = {n: dict(GOOD) for n in names}
        self.offline, self.no_sync, self.fail_arm = set(), set(), set()
        self.calls = []
        self.diff = {}
        self.lock = threading.Lock()

    def get(self, n, path, timeout=None):
        if n in self.offline:
            raise OSError("timeout")
        with self.lock:
            return dict(self.st[n])

    def post(self, n, path, body=None, timeout=None):
        with self.lock:
            self.calls.append((n, path, (body or {}).get("role")))
            s = self.st[n]
            if path == "/take/arm":
                if n in self.fail_arm:
                    raise hubd.NodeError("%s: camera busy" % n)
                s["mode"], s["role"], s["take_id"] = "armed", body["role"], body["take_id"]
                s["take"] = {"take_id": body["take_id"], "role": body["role"],
                             "sync_difference_us": self.diff.get(n, 2)}
                if body["role"] == "server":
                    for m, o in self.st.items():
                        if o["mode"] == "armed" and m not in self.no_sync:
                            o["mode"] = "recording"
                return {"state": "armed"}
            if path == "/take/stop":
                s["mode"] = "idle"
                s["take"] = None
                return {"take_id": s["take_id"], "frames": 900, "sync_difference_us": self.diff.get(n, 2),
                        "duration_s": 30.0, "dropped_frames": 0,
                        "exit_code": 0, "pts_source": "save-pts", "sync_role": s["role"],
                        "first_frame_wallclock_ns": 1_000_000_000 + (7000 if n == "cam3" else 0)}
            if path == "/live/start":
                s["mode"] = "live"
                return {"mode": "live"}
            if path == "/live/stop":
                s["mode"] = "idle"
                return {"mode": "idle"}
        raise AssertionError(path)


class HubTest(unittest.TestCase):
    def setUp(self):
        self.cfg = hubd.load_config(None)
        self.cfg.update(state_dir=tempfile.mkdtemp(), client_head_start_s=0, sync_timeout_s=1)
        self.rig = FakeRig(self.cfg["nodes"])
        self.hub = hubd.Hub(self.cfg, nodes=self.rig, sleep=lambda s: None)

    def test_rec_stop_order_and_summary(self):
        t = self.hub.rec()
        self.assertEqual(t["server"], "cam1")
        arms = [(n, r) for n, p, r in self.rig.calls if p == "/take/arm"]
        self.assertEqual(arms[-1], ("cam1", "server"))          # server last
        self.assertEqual(sorted(n for n, r in arms[:-1]), ["cam2", "cam3", "cam4"])
        self.assertEqual(self.hub.snapshot()["state"], "recording")
        s = self.hub.stop()
        self.assertTrue(s["all_clean"])
        self.assertEqual(s["start_spread_us"], 7.0)
        self.assertEqual(s["frame_count_spread"], 0)
        self.assertEqual(self.hub.snapshot()["state"], "idle")
        self.assertEqual(self.hub.read_log()[-1]["take_id"], t["take_id"])

    def test_rec_skips_offline_cameras(self):
        self.rig.offline.update(["cam2", "cam3", "cam4"])
        t = self.hub.rec()
        self.assertEqual(t["nodes"], ["cam1"])
        self.assertEqual(t["missing"], ["cam2", "cam3", "cam4"])
        arms = [(n, r) for n, p, r in self.rig.calls if p == "/take/arm"]
        self.assertEqual(arms, [("cam1", "server")])
        self.assertIn("cam1 only: cam2, cam3, cam4 offline", self.hub.snapshot()["message"])
        s = self.hub.stop()
        self.assertEqual(s["missing"], ["cam2", "cam3", "cam4"])
        self.assertEqual(self.hub.read_log()[-1]["missing"], ["cam2", "cam3", "cam4"])

    def test_rec_server_falls_back_when_sync_server_offline(self):
        self.rig.offline.add("cam1")
        t = self.hub.rec()
        self.assertEqual(t["server"], "cam2")
        self.assertNotIn("cam1", t["nodes"])

    def test_rec_refused_when_all_offline(self):
        self.rig.offline.update(self.cfg["nodes"])
        with self.assertRaises(hubd.Refused) as cm:
            self.hub.rec()
        self.assertEqual(cm.exception.reasons, ["no cameras online"])

    def test_explicit_names_stay_strict(self):
        self.rig.offline.add("cam2")
        with self.assertRaises(hubd.Refused) as cm:
            self.hub.rec(["cam1", "cam2"])
        self.assertIn("cam2 offline", cm.exception.reasons)

    def test_stale_poll_after_stop_does_not_readopt(self):
        # Regression (2026-11-15): a background poll that read "recording" just
        # before STOP finished re-adopted the ended take as "Hub restarted during take".
        self.rig.offline.update(["cam2", "cam3", "cam4"])
        self.hub.rec()
        read, release = threading.Event(), threading.Event()
        orig = self.rig.get

        def stale_get(n, path, timeout=None):
            st = orig(n, path, timeout)      # reads "recording"...
            if n == "cam1" and not read.is_set():
                read.set()
                release.wait(5)              # ...and answers after STOP is done
            return st
        self.rig.get = stale_get
        t = threading.Thread(target=self.hub.poll_once)
        t.start()
        self.assertTrue(read.wait(5))
        self.rig.get = orig          # STOP's own polls see the truth
        self.hub.stop()
        release.set()
        t.join(5)
        snap = self.hub.snapshot()
        self.assertEqual(snap["state"], "idle")
        self.assertIsNone(snap["take"])
        self.assertEqual(len(self.hub.read_log()), 1)

    def test_refused_with_reasons(self):
        self.rig.offline.add("cam2")
        self.rig.st["cam3"]["temp_c"] = 75.0
        self.rig.st["cam4"]["wifi_powersave"] = True
        self.rig.st["cam1"]["clock_offset_ms"] = -9.0
        with self.assertRaises(hubd.Refused) as cm:
            self.hub.rec()
        r = " | ".join(cm.exception.reasons)
        self.assertNotIn("cam2 offline", r)    # offline is skipped, not a blocker
        for want in ("cam3 temperature", "cam4 Wi-Fi power save", "cam1 clock offset"):
            self.assertIn(want, r)
        self.assertFalse(any(p == "/take/arm" for _, p, _ in self.rig.calls))
        self.assertEqual(self.hub.snapshot()["state"], "idle")

    def test_throttle_now_refused_but_history_ok(self):
        self.rig.st["cam2"]["throttled"] = 0x50005
        with self.assertRaises(hubd.Refused) as cm:
            self.hub.rec()
        self.assertEqual(cm.exception.reasons, ["cam2 throttled (0x50005)"])

    def test_live_stopped_before_rec(self):
        self.hub.live(True)
        self.assertTrue(all(s["mode"] == "live" for s in self.rig.st.values()))
        self.hub.rec()
        paths = [p for _, p, _ in self.rig.calls]
        self.assertLess(max(i for i, p in enumerate(paths) if p == "/live/stop"),
                        paths.index("/take/arm"))

    def test_fallback_server(self):
        self.rig.offline.add("cam1")
        t = self.hub.rec(["cam2", "cam3", "cam4"])
        self.assertEqual(t["server"], "cam2")

    def test_sync_timeout_aborts_and_stops(self):
        self.rig.no_sync.add("cam3")
        with self.assertRaises(hubd.NodeError) as cm:
            self.hub.rec()
        self.assertIn("cam3", str(cm.exception))
        self.assertTrue(all(s["mode"] == "idle" for s in self.rig.st.values()))
        snap = self.hub.snapshot()
        self.assertEqual(snap["state"], "idle")
        self.assertIn("aborted", snap["message"])
        self.assertIn("no sync", self.hub.read_log()[-1]["aborted"])

    def test_arm_failure_aborts(self):
        self.rig.fail_arm.add("cam4")
        with self.assertRaises(hubd.NodeError):
            self.hub.rec()
        self.assertFalse(any(r == "server" for _, p, r in self.rig.calls if p == "/take/arm"))
        self.assertTrue(all(s["mode"] == "idle" for s in self.rig.st.values()))

    def test_double_rec_and_stop_when_idle(self):
        self.hub.rec()
        with self.assertRaises(hubd.Refused):
            self.hub.rec()
        with self.assertRaises(hubd.Refused):
            self.hub.live(True)
        self.hub.stop()
        with self.assertRaises(hubd.Refused):
            self.hub.stop()

    def test_late_client_warns(self):
        self.rig.diff["cam4"] = -183870
        t = self.hub.rec()
        self.assertEqual(t["sync_warnings"], ["cam4 started 183.9 ms late (5.5 frames)"])
        self.assertIn("cam4 started", self.hub.snapshot()["message"])
        s = self.hub.stop()
        self.assertFalse(s["all_clean"])
        self.assertEqual(len(s["sync_warnings"]), 1)

    def test_dropped_frames_not_clean(self):
        self.hub.rec()
        orig = self.rig.post

        def post(n, path, body=None, timeout=None):
            r = orig(n, path, body, timeout)
            if path == "/take/stop" and n == "cam1":
                r["dropped_frames"] = 27
            return r
        self.rig.post = post
        s = self.hub.stop()
        self.assertFalse(s["all_clean"])
        self.assertEqual(s["dropped_frames_total"], 27)

    def test_adopts_running_take_after_restart(self):
        self.hub.rec()
        hub2 = hubd.Hub(self.cfg, nodes=self.rig, sleep=lambda s: None)   # "restarted" hub
        self.assertEqual(hub2.snapshot()["state"], "idle")
        hub2.poll_once()
        snap = hub2.snapshot()
        self.assertEqual(snap["state"], "recording")
        self.assertTrue(snap["take"]["adopted"])
        self.assertEqual(snap["take"]["server"], "cam1")
        self.assertEqual(sorted(snap["take"]["nodes"]), ["cam1", "cam2", "cam3", "cam4"])
        s = hub2.stop()
        self.assertTrue(s["all_clean"])
        self.assertTrue(all(st["mode"] == "idle" for st in self.rig.st.values()))

    def test_ingest_files(self):
        self.cfg["ingest_dir"] = os.path.join(self.cfg["state_dir"], "ingest")
        self.assertIn("not running", self.hub.ingest_state()["status"])
        self.hub.ingest_command("start")
        self.hub.ingest_command("eject")
        cmds = sorted(os.listdir(os.path.join(self.cfg["ingest_dir"], "commands")))
        self.assertEqual([c.split("-", 1)[1] for c in cmds], ["start.json", "eject.json"])

    def _media_server(self, mounted=True):
        import http.client
        self.cfg["ingest_dir"] = os.path.join(self.cfg["state_dir"], "ingest")
        os.makedirs(self.cfg["ingest_dir"], exist_ok=True)
        self.cfg["drive_mount"] = os.path.join(self.cfg["state_dir"], "studio")
        d = os.path.join(self.cfg["drive_mount"], "takes", "2026-11-15_20261115-171313")
        os.makedirs(d)
        self.data = bytes(range(256)) * 40                  # 10240 bytes
        with open(os.path.join(d, "cam1.mov"), "wb") as f:
            f.write(self.data)
        self.hub.drive_mounted = lambda: mounted
        hubd.Handler.hub = self.hub
        srv = hubd.ThreadingHTTPServer(("127.0.0.1", 0), hubd.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)

        def get(path, headers=None, method="GET"):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=15)
            c.request(method, path, headers=headers or {})
            r = c.getresponse()
            try:
                return r.status, dict(r.getheaders()), r.read()
            finally:
                c.close()
        return get

    def test_media_full_and_ranges(self):
        get = self._media_server()
        url = "/media/20261115-171313/cam1.mov"
        code, h, body = get(url)
        self.assertEqual((code, body, h["Accept-Ranges"]), (200, self.data, "bytes"))
        code, h, body = get(url, {"Range": "bytes=100-199"})
        self.assertEqual((code, body, h["Content-Range"]), (206, self.data[100:200], "bytes 100-199/10240"))
        code, h, body = get(url, {"Range": "bytes=10000-"})
        self.assertEqual((code, body), (206, self.data[10000:]))
        code, h, body = get(url, {"Range": "bytes=-40"})
        self.assertEqual((code, body), (206, self.data[-40:]))
        code, h, body = get(url, {"Range": "bytes=20000-"})
        self.assertEqual(code, 416)
        code, h, body = get(url, method="HEAD")
        self.assertEqual((code, h["Content-Length"], body), (200, "10240", b""))
        self.assertTrue(os.path.exists(os.path.join(self.cfg["ingest_dir"], "playback.lease")))

    def test_media_missing_and_bad_paths(self):
        get = self._media_server()
        self.assertEqual(get("/media/20261115-171313/cam2.mov")[0], 404)
        self.assertEqual(get("/media/../etc/passwd")[0], 404)
        self.assertEqual(get("/media/20261115-171313/../../x.mov")[0], 404)

    def test_media_drive_not_mounted(self):
        get = self._media_server(mounted=False)
        orig = self.hub.media_file
        self.hub.media_file = lambda t, c: orig(t, c, wait_s=0.3)
        self.assertEqual(get("/media/20261115-171313/cam1.mov")[0], 503)

    def test_one_missed_poll_is_slow_not_offline(self):
        self.hub.poll_once()
        self.rig.offline.add("cam2")
        self.hub.poll_once()
        n = self.hub.snapshot()["nodes"]["cam2"]
        self.assertTrue(n["online"])
        self.assertTrue(n["slow"])
        self.rig.offline.discard("cam2")
        self.hub.poll_once()
        n = self.hub.snapshot()["nodes"]["cam2"]
        self.assertEqual((n["online"], n["slow"]), (True, False))

    def test_offline_after_three_missed_polls(self):
        self.hub.poll_once()
        self.rig.offline.add("cam2")
        for i in range(3):
            self.hub.poll_once()
            self.assertEqual(self.hub.snapshot()["nodes"]["cam2"]["online"], i < 2)

    def test_rec_keeps_a_camera_that_missed_one_poll(self):
        self.hub.poll_once()
        self.rig.offline.add("cam3")          # misses the poll REC makes...
        orig = self.rig.post

        def post(n, path, body=None, timeout=None):
            self.rig.offline.discard("cam3")  # ...but answers the arm request
            return orig(n, path, body, timeout)
        self.rig.post = post
        t = self.hub.rec()
        self.assertEqual(sorted(t["nodes"]), ["cam1", "cam2", "cam3", "cam4"])
        self.assertEqual(t["missing"], [])

    def test_kept_status_never_readopts_a_take(self):
        self.hub.rec()
        self.hub.poll_once()                  # every camera seen recording
        self.hub.stop()
        self.rig.offline.add("cam1")          # cam1's last kept status says "recording"
        self.hub.status["cam1"] = dict(self.hub.status["cam1"], mode="recording",
                                       take={"take_id": "x", "role": "server"})
        self.hub.poll_once()
        self.assertEqual(self.hub.snapshot()["state"], "idle")

    def test_hub_health_reads_sysfs(self):
        root = tempfile.mkdtemp()

        def put(rel, text):
            os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
            with open(os.path.join(root, rel), "w") as f:
                f.write(text)
        put("class/thermal/thermal_zone0/temp", "60050\n")
        put("class/hwmon/hwmon2/name", "pwmfan\n")
        put("class/hwmon/hwmon2/fan1_input", "2680\n")
        put("class/hwmon/hwmon0/name", "cpu_thermal\n")
        put("devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "2400000\n")
        h = hubd.hub_health(root, sysfs=root, vcgencmd="/nonexistent/vcgencmd")
        self.assertEqual((h["temp_c"], h["fan_rpm"], h["cpu_mhz"], h["throttled"]), (60.0, 2680, 2400, None))
        self.assertIsNotNone(h["free_gb"])
        empty = hubd.hub_health(root, sysfs=os.path.join(root, "missing"), vcgencmd="/nonexistent/vcgencmd")
        self.assertIsNone(empty["temp_c"])

    def test_snapshot_shows_problems(self):
        self.rig.offline.add("cam4")
        self.hub.poll_once()
        snap = self.hub.snapshot()
        self.assertFalse(snap["nodes"]["cam4"]["online"])
        self.assertEqual(snap["nodes"]["cam4"]["problems"], ["cam4 offline"])
        self.assertEqual(snap["nodes"]["cam1"]["problems"], [])


if __name__ == "__main__":
    unittest.main()
