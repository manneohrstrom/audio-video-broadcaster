import json
import os
import shutil
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
        self.calls, self.bodies = [], []
        self.diff = {}
        self.lock = threading.Lock()

    def still(self, n, body, timeout=None):
        with self.lock:
            self.bodies.append((n, "/snapshot", body))
            if self.st[n]["mode"] != "idle":
                raise hubd.NodeError("%s: camera is %s" % (n, self.st[n]["mode"]))
            meta = {"settings": body["settings"], "AnalogueGain": 12.0, "ColourGains": [1.9, 2.6]}
            return b"\xff\xd8snap " + json.dumps(body).encode(), meta

    def get(self, n, path, timeout=None):
        if n in self.offline:
            raise OSError("timeout")
        with self.lock:
            return dict(self.st[n])

    def post(self, n, path, body=None, timeout=None, raw=False):
        with self.lock:
            self.calls.append((n, path, (body or {}).get("role")))
            self.bodies.append((n, path, body))
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
            if path == "/live/settings":
                if s["mode"] != "live":
                    raise hubd.NodeError("%s: live mode is off" % n)
                s.setdefault("live_settings", {}).update(body["settings"])
                return {"applied": body["settings"]}
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
        look, why = self.hub.lamp.light.decide(*self.hub.lamp_inputs())
        self.assertEqual(look, "error")                          # recording light flashes
        self.assertIn("no cameras online", why)

    def test_rec_and_stop_drive_the_scratch_mic(self):
        calls = []

        class Fake:
            def start(f, take_id):
                calls.append(("start", take_id, self.hub.state))
            def stop(f):
                calls.append(("stop", self.hub.state))
                return {"device": "hw:1", "rate": 48000, "channels": 1, "samples": 4800, "problems": [], "command": []}
            def status(f):
                return {"state": "ready"}
        self.hub.scratch = Fake()
        t = self.hub.rec()
        self.assertEqual(calls[0], ("start", t["take_id"], "starting"))     # before the cameras arm
        self.hub.stop()
        self.assertEqual(calls[1], ("stop", "stopping"))                    # after the cameras stopped
        self.assertEqual(self.hub.read_log()[-1]["scratch"]["samples"], 4800)
        self.assertNotIn("command", self.hub.read_log()[-1]["scratch"])

    def test_no_mic_take_still_records(self):
        self.hub.scratch.find_device = lambda: None
        self.hub.rec()
        self.hub.stop()
        self.assertIsNone(self.hub.read_log()[-1]["scratch"])
        self.assertEqual(self.hub.snapshot()["scratch"]["state"], "no mic")

    def test_audio_upload_only_for_real_takes_and_waits_during_a_take(self):
        import io
        with self.assertRaises(ValueError):
            self.hub.audio.new_song("20990101-000000")
        t = self.hub.rec()
        self.hub.stop()
        s = self.hub.audio.new_song(t["take_id"], "Song")
        v = self.hub.audio.new_version(t["take_id"], s["id"], "mix.wav", 10)
        self.hub.rec()
        with self.assertRaises(hubd.audio.Busy):
            self.hub.audio.put(t["take_id"], s["id"], v["version"], 0, io.BytesIO(b"x" * 10).read, 10)
        self.hub.stop()
        self.assertEqual(self.hub.ingest_state()["takes"][t["take_id"]]["audio"]["songs"][0]["name"], "Song")

    def test_lamp_follows_rig_state(self):
        decide = lambda: self.hub.lamp.light.decide(*self.hub.lamp_inputs())[0]  # noqa: E731
        self.hub.poll_once()
        self.assertEqual(decide(), "ready")
        self.hub.rec()
        self.assertEqual(decide(), "recording")
        with self.assertRaises(hubd.Refused):
            self.hub.rec()                                       # second press: no alert
        self.assertEqual(decide(), "recording")
        self.hub.stop()
        self.assertEqual(decide(), "ready")
        self.assertFalse(self.hub.snapshot()["lamp"]["enabled"])

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

    def test_out_of_sync_start_restarts_by_itself(self):
        self.rig.diff["cam3"] = -8403876            # sync packets 8.4 s late (2026-11-16)
        orig, arms = self.rig.post, []

        def post(n, path, body=None, **kw):
            r = orig(n, path, body, **kw)
            if path == "/take/arm" and body["role"] == "server":
                arms.append(body["take_id"])
                self.rig.diff.pop("cam3", None)      # in sync from the second try on
            return r
        self.rig.post = post
        clock = [1794855195.0]
        self.hub.clock = lambda: clock[0]
        self.hub.sleep = lambda s: clock.__setitem__(0, clock[0] + max(s, 1.0))
        t = self.hub.rec()
        self.assertEqual(len(arms), 2)
        self.assertEqual(t["take_id"], arms[1])
        self.assertNotEqual(arms[0], arms[1])
        self.assertEqual(t["sync_warnings"], [])
        self.assertEqual(len(t["restarts"]), 1)
        self.assertIn("cam3 started 8403.9 ms late", t["restarts"][0]["out_of_sync"][0])
        self.assertIn("Restarted 1×", self.hub.snapshot()["message"])
        self.assertEqual(self.hub.snapshot()["state"], "recording")
        log = self.hub.read_log()
        self.assertEqual(log[-1]["take_id"], arms[0])
        self.assertIn("restarted: out of sync", log[-1]["aborted"])
        self.assertNotIn(arms[0], self.hub.take_ids())         # the thrown-away start is no take
        s = self.hub.stop()
        self.assertTrue(s["all_clean"])
        self.assertEqual(s["take_id"], arms[1])

    def test_still_out_of_sync_records_with_warning_and_light(self):
        self.rig.diff["cam4"] = -21192397
        alerts = []
        self.hub.lamp.alert = alerts.append
        t = self.hub.rec()
        self.assertEqual(len(t["restarts"]), self.cfg["resync_retries"])
        self.assertEqual(self.hub.snapshot()["state"], "recording")
        self.assertIn("Out of sync after 3 tries", self.hub.snapshot()["message"])
        self.assertTrue(alerts and "cam4" in alerts[0])

    def test_small_offsets_dont_restart(self):
        self.rig.diff["cam2"] = -5000                # 5 ms: warned, not restarted
        t = self.hub.rec()
        self.assertEqual(t["restarts"], [])
        self.assertEqual(len(t["sync_warnings"]), 1)

    def test_exit_crash_after_complete_write_is_fine(self):
        self.assertTrue(hubd.exit_ok({"exit_code": -6, "pts_source": "save-pts"}))
        self.assertFalse(hubd.exit_ok({"exit_code": -6, "pts_source": "rebuilt-from-metadata"}))
        self.assertFalse(hubd.exit_ok({"exit_code": 1, "pts_source": "save-pts"}))
        self.assertTrue(hubd.exit_ok({"exit_code": 0}))

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

    def test_media_serves_rendered_videos_only_from_edits(self):
        get = self._media_server()
        d = os.path.join(self.cfg["drive_mount"], "takes", "2026-11-15_20261115-171313", "edits", "song")
        os.makedirs(d)
        with open(os.path.join(d, "song-v1-quick.mp4"), "wb") as f:
            f.write(b"mp4data")
        self.assertEqual(get("/media/20261115-171313/edits/song/song-v1-quick.mp4")[2], b"mp4data")
        self.assertEqual(get("/media/20261115-171313/edits/../cam1.mov")[0], 404)
        self.assertEqual(get("/media/20261115-171313/take.json")[0], 404)
        self.assertEqual(get("/media/20261115-171313/edits/song/x.json")[0], 404)

    def test_render_request_needs_a_synced_finished_version(self):
        import io
        self.cfg["ingest_dir"] = os.path.join(self.cfg["state_dir"], "ingest")
        t = self.hub.rec()
        self.hub.stop()
        tid = t["take_id"]
        s = self.hub.audio.new_song(tid, "Song")
        v = self.hub.audio.new_version(tid, s["id"], "mix.wav", 4)
        with self.assertRaises(ValueError):
            self.hub.request_render(tid, "song", 1)                      # not uploaded yet
        self.hub.audio.put(tid, "song", 1, 0, io.BytesIO(b"abcd").read, 4)
        import audio as A
        orig = A.probe
        A.probe = lambda p, ff="ffprobe": {"duration_s": 1.0, "sample_rate": 48000, "channels": 2, "codec": "pcm"}
        try:
            self.hub.audio.finish(tid, "song", v["version"])
        finally:
            A.probe = orig
        with self.assertRaises(ValueError) as e:
            self.hub.request_render(tid, "song", 1)                      # no sync.json yet
        self.assertIn("synced", str(e.exception))
        with open(os.path.join(self.hub.audio.root, tid, "song", "v1", "sync.json"), "w") as f:
            f.write("{}")
        r = self.hub.request_render(tid, "song", 1)
        self.assertEqual(r["queued"], "render")
        cmd = os.listdir(os.path.join(self.cfg["ingest_dir"], "commands"))[0]
        with open(os.path.join(self.cfg["ingest_dir"], "commands", cmd)) as f:
            self.assertEqual(json.load(f), {"cmd": "render", "take": tid, "song": "song", "version": 1})
        songs = self.hub.ingest_state()["takes"][tid]["audio"]["songs"]
        self.assertTrue(songs[0]["versions"][0]["synced"])

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

    def test_ingest_list_includes_takes_only_on_cameras(self):
        self.cfg["ingest_dir"] = os.path.join(self.cfg["state_dir"], "ingest")
        os.makedirs(self.cfg["ingest_dir"])
        with open(os.path.join(self.cfg["ingest_dir"], "state.json"), "w") as f:
            json.dump({"status": "waiting", "takes": {"20261115-100000": {"cam1": {"done": ["pulled"]}}}}, f)
        t = self.hub.rec()
        self.hub.stop()
        st = self.hub.ingest_state()
        self.assertEqual(st["takes"]["20261115-100000"]["cam1"]["done"], ["pulled"])
        rec = st["takes"][t["take_id"]]
        self.assertEqual(sorted(rec), ["recorded"])              # no ingest steps yet
        self.assertEqual(sorted(rec["recorded"]["cameras"]), ["cam1", "cam2", "cam3", "cam4"])
        self.assertEqual(rec["recorded"]["length_s"], 30.0)
        self.assertEqual(rec["recorded"]["cameras"]["cam1"]["frames"], 900)
        self.assertEqual(rec["recorded"]["problems"], [])

    def test_ingest_list_flags_a_take_that_was_not_clean(self):
        self.cfg["ingest_dir"] = os.path.join(self.cfg["state_dir"], "ingest")
        self.hub.append_log({"take_id": "20261115-120000", "all_clean": False, "errors": {"cam3": "stop timed out"},
                             "sync_warnings": ["cam2 started 2 ms late"],
                             "cameras": {"cam1": {"frames": 10, "exit_code": 0},
                                         "cam3": {"frames": 9, "exit_code": -6}}})
        p = self.hub.ingest_state()["takes"]["20261115-120000"]["recorded"]["problems"]
        self.assertEqual(p, ["cam2 started 2 ms late", "cam3: stop timed out", "cam3 exited with code -6"])

    def test_snapshot_shows_problems(self):
        self.rig.offline.add("cam4")
        self.hub.poll_once()
        snap = self.hub.snapshot()
        self.assertFalse(snap["nodes"]["cam4"]["online"])
        self.assertEqual(snap["nodes"]["cam4"]["problems"], ["cam4 offline"])
        self.assertEqual(snap["nodes"]["cam1"]["problems"], [])

    def test_a_still_being_taken_is_no_problem_on_the_page_but_rec_stays_strict(self):
        status = dict(self.rig.st["cam2"], mode="snapshot")
        self.assertEqual([p for p in hubd.arm_problems("cam2", status, self.hub.cfg["checks"], still_is_idle=True)
                          if "snapshot" in p], [])
        self.assertIn("cam2 is snapshot", hubd.arm_problems("cam2", status, self.hub.cfg["checks"]))


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.cfg = hubd.load_config(None)
        self.cfg.update(state_dir=tempfile.mkdtemp(), client_head_start_s=0, sync_timeout_s=1)
        self.rig = FakeRig(self.cfg["nodes"])
        self.hub = hubd.Hub(self.cfg, nodes=self.rig, sleep=lambda s: None)
        self.hub.poll_once()

    def test_snapshot_uses_hub_settings_and_overrides(self):
        self.hub.set_camera_settings("cam2", {"lens_position": 0.8})
        jpg, info = self.hub.snap("cam2")
        self.assertIn(b'"lens_position": 0.8', jpg)
        self.assertEqual((info["source"], info["settings"]), ("still", {"lens_position": 0.8}))
        jpg, _ = self.hub.snap("cam2", {"lens_position": 1.4})
        self.assertIn(b'"lens_position": 1.4', jpg)
        self.assertEqual(self.hub.cam_settings["cam2"], {"lens_position": 0.8})   # a try isn't saved

    def test_settings_validated_persisted_and_sent_at_arm(self):
        for bad in ({"lens_position": 99}, {"gain": 99}, {"shutter_us": 1}, {"awb_gains": [1.0]}):
            with self.assertRaises(ValueError):
                self.hub.set_camera_settings("cam1", bad)
        with self.assertRaises(ValueError):
            self.hub.set_camera_settings("cam9", {"lens_position": 1})
        self.hub.set_camera_settings("cam1", {"lens_position": 0.5})
        again = hubd.Hub(self.cfg, nodes=self.rig, sleep=lambda s: None)
        self.assertEqual(again.cam_settings, {"cam1": {"lens_position": 0.5}})
        self.hub.rec()
        arms = {n: b for n, p, b in self.rig.bodies if p == "/take/arm"}
        self.assertEqual(arms["cam1"]["settings"], {"lens_position": 0.5})
        self.assertNotIn("settings", arms["cam2"])
        with self.assertRaises(hubd.Refused):
            self.hub.snap("cam1")                      # never during a take
        self.hub.stop()

    def test_offline_and_unknown_cameras(self):
        with self.assertRaises(ValueError):
            self.hub.snap("cam9")
        self.rig.offline.add("cam3")
        for _ in range(3):
            self.hub.poll_once()
        with self.assertRaises(hubd.Refused):
            self.hub.snap("cam3")

    def test_rec_waits_for_a_snapshot_in_flight(self):
        self.hub.snaps_in_flight = 1
        threading.Timer(0.2, lambda: setattr(self.hub, "snaps_in_flight", 0)).start()
        t0 = __import__("time").monotonic()
        self.hub.rec()
        self.assertGreater(__import__("time").monotonic() - t0, 0.15)
        self.hub.stop()

    def test_no_new_stills_while_rec_or_live_takes_the_cameras(self):
        with self.hub.holding_cameras():
            with self.assertRaises(hubd.Refused):
                self.hub.still("cam1", auto=True)
        self.hub.still("cam1", auto=True)          # free again afterwards

    def test_phone_stills(self):
        ph = self.hub.phones
        body = {"device": "phone-pp01", "page": "abcdefgh1", "state": "ready"}
        self.assertFalse(self.hub.phone_poll(body)["snap"])
        ph.want_snaps()
        self.assertTrue(self.hub.phone_poll(body)["snap"])
        with self.assertRaises(ValueError):
            ph.put_snap("phone-pp01", b"not a jpeg")
        ph.put_snap("phone-pp01", b"\xff\xd8x")
        self.assertFalse(self.hub.phone_poll(body)["snap"])     # got it: don't ask again
        self.assertEqual(ph.get_snap("phone-pp01")[0], b"\xff\xd8x")


class SyncRequestTest(unittest.TestCase):
    def test_finished_upload_queues_a_sync(self):
        cfg = hubd.load_config(None)
        tmp = tempfile.mkdtemp()
        cfg.update(state_dir=tmp, ingest_dir=os.path.join(tmp, "ingest"), client_head_start_s=0, sync_timeout_s=1)
        hub = hubd.Hub(cfg, nodes=FakeRig(cfg["nodes"]), sleep=lambda s: None)
        hub.audio.version_info = lambda t, s, v: {"complete": True}
        hub.request_sync("20261116-185403", "track-1", 1)
        d = os.path.join(tmp, "ingest", "commands")
        cmds = [json.load(open(os.path.join(d, n))) for n in os.listdir(d)]
        self.assertEqual(cmds, [{"cmd": "sync", "take": "20261116-185403", "song": "track-1", "version": 1}])
        hub.audio.version_info = lambda t, s, v: {"complete": False}
        with self.assertRaises(ValueError):
            hub.request_sync("20261116-185403", "track-1", 2)


class YouTubeTest(unittest.TestCase):
    """The editor's "Upload to YouTube": only when the hub is signed in; the upload's progress
    comes back with the render's."""

    def setUp(self):
        cfg = hubd.load_config(None)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.credentials = os.path.join(self.tmp, "youtube", "credentials.json")
        self.spool = os.path.join(self.tmp, "youtube", "uploads")
        cfg.update(state_dir=self.tmp, ingest_dir=os.path.join(self.tmp, "ingest"), client_head_start_s=0,
                   sync_timeout_s=1, youtube_credentials_path=self.credentials, youtube_spool_dir=self.spool)
        self.hub = hubd.Hub(cfg, nodes=FakeRig(cfg["nodes"]), sleep=lambda s: None)
        self.hub.audio.version_info = lambda t, s, v: {"synced": True}
        self.hub.editor.working_copy_path = lambda t, s, v: os.path.join(self.tmp, "edit.json")
        self.hub.editor._song_and_version = lambda t, s, v: ({"name": "Blue in Green"}, {}, {})
        with open(os.path.join(self.tmp, "edit.json"), "w") as f:
            f.write("{}")

    def commands(self):
        d = os.path.join(self.tmp, "ingest", "commands")
        return [json.load(open(os.path.join(d, n))) for n in sorted(os.listdir(d))]

    def sign_in(self):
        os.makedirs(os.path.dirname(self.credentials), exist_ok=True)
        with open(self.credentials, "w") as f:
            json.dump({"client_id": "a", "client_secret": "b", "refresh_token": "c"}, f)

    def test_only_when_signed_in(self):
        self.assertEqual(self.hub.youtube_status(), {"signed_in": False})
        self.hub.request_edit_render("20261117-201741", "track-1", 1, youtube=True)
        self.sign_in()
        self.assertEqual(self.hub.youtube_status(), {"signed_in": True})
        self.hub.request_edit_render("20261117-201741", "track-1", 1, youtube=True)
        self.hub.request_edit_render("20261117-201741", "track-1", 1, youtube=False)
        self.assertEqual([(c["youtube"], c["song_name"]) for c in self.commands()],
                         [(False, "Blue in Green"), (True, "Blue in Green"), (False, "Blue in Green")])

    def test_the_takes_name_goes_with_the_render(self):
        self.hub.request_edit_render("20261117-201741", "track-1", 1)
        self.hub.names.set("takes", "20261117-201741", "Sunday jam")
        self.hub.request_edit_render("20261117-201741", "track-1", 1)
        self.assertEqual([c["take_name"] for c in self.commands()], [None, "Sunday jam"])
        self.assertEqual(self.hub.snapshot()["names"]["takes"], {"20261117-201741": "Sunday jam"})

    def test_the_upload_comes_back_with_the_render(self):
        os.makedirs(os.path.join(self.tmp, "ingest"))
        with open(os.path.join(self.tmp, "ingest", "state.json"), "w") as f:
            json.dump({"renders": {"20261117-201741/track-1:edit": {
                "status": "done", "video_number": 2, "youtube": True, "upload": "20261117-201741_track-1_video2"}}}, f)
        self.assertIsNone(self.hub.edit_render_status("20261117-201741", "track-1", 1)["upload"])
        os.makedirs(self.spool)
        with open(os.path.join(self.spool, "20261117-201741_track-1_video2.json"), "w") as f:
            json.dump({"status": "uploading", "progress": 0.4, "session_uri": "secret-ish", "video_number": 2}, f)
        upload = self.hub.edit_render_status("20261117-201741", "track-1", 1)["upload"]
        self.assertEqual((upload["status"], upload["progress"]), ("uploading", 0.4))
        self.assertNotIn("session_uri", upload)                    # the page doesn't need YouTube's session

    def test_the_archive_shows_the_upload_as_a_step_and_links_finished_videos(self):
        os.makedirs(self.spool)
        for number, status in ((1, "done"), (2, "uploading")):
            with open(os.path.join(self.spool, "20261117-201741_track-1_video%d.json" % number), "w") as f:
                json.dump({"status": status, "progress": 0.5, "session_uri": "x",
                           "studio_url": "https://studio.youtube.com/video/abc/edit" if status == "done" else None}, f)
        song = {"id": "track-1", "render": {"status": "done"}, "videos": [{"number": 1}, {"number": 2}]}
        self.hub.add_youtube("20261117-201741", song, {"status": "done", "youtube": True,
                                                       "upload": "20261117-201741_track-1_video2"})
        self.assertTrue(song["render"]["youtube"])
        self.assertEqual((song["render"]["upload"]["status"], song["render"]["upload"]["progress"]), ("uploading", 0.5))
        self.assertEqual(song["videos"][0]["youtube"]["studio_url"], "https://studio.youtube.com/video/abc/edit")
        self.assertNotIn("session_uri", song["videos"][1]["youtube"])
        quiet = {"id": "track-2", "render": None, "videos": [{"number": 1}]}
        self.hub.add_youtube("20261117-201741", quiet, {})
        self.assertIsNone(quiet["render"])
        self.assertIsNone(quiet["videos"][0]["youtube"])


class DriveViewTest(unittest.TestCase):
    """The take list fitted to the STUDIO drive (docs/stick-db-plan.md, S3)."""

    def takes(self):
        return {"20261117-010000": {"complete": True, "drive_id": "STICK"},
                "20261117-020000": {"complete": True, "drive_id": "SSD"},
                "20261117-030000": {"cam1": {"done": ["pulled"]}}}          # in flight

    def test_takes_on_another_drive_become_a_count(self):
        takes = self.takes()
        view = hubd.drive_view(takes, {"drive_id": "SSD", "present": True})
        self.assertEqual(sorted(takes), ["20261117-020000", "20261117-030000"])
        self.assertEqual(view, {"drive_id": "SSD", "present": True, "other_drive_takes": 1})
        self.assertNotIn("drive_absent", takes["20261117-020000"])

    def test_with_the_drive_out_its_takes_are_greyed(self):
        takes = self.takes()
        view = hubd.drive_view(takes, {"drive_id": "SSD", "present": False})
        self.assertFalse(view["present"])
        self.assertTrue(takes["20261117-020000"]["drive_absent"])
        self.assertNotIn("drive_absent", takes["20261117-030000"])      # in flight: still on the cameras

    def test_no_index_yet_lists_everything(self):
        takes = self.takes()
        self.assertEqual(hubd.drive_view(takes, {})["other_drive_takes"], 0)
        self.assertEqual(len(takes), 3)


if __name__ == "__main__":
    unittest.main()
