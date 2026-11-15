import os, sys, tempfile, threading, unittest
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
                return {"take_id": s["take_id"], "frames": 900, "sync_difference_us": self.diff.get(n, 2), "duration_s": 30.0, "dropped_frames": 0,
                        "exit_code": 0, "pts_source": "save-pts", "sync_role": s["role"],
                        "first_frame_wallclock_ns": 1_000_000_000 + (7000 if n == "cam3" else 0)}
            if path == "/live/start":
                s["mode"] = "live"; return {"mode": "live"}
            if path == "/live/stop":
                s["mode"] = "idle"; return {"mode": "idle"}
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

    def test_snapshot_shows_problems(self):
        self.rig.offline.add("cam4")
        self.hub.poll_once()
        snap = self.hub.snapshot()
        self.assertFalse(snap["nodes"]["cam4"]["online"])
        self.assertEqual(snap["nodes"]["cam4"]["problems"], ["cam4 offline"])
        self.assertEqual(snap["nodes"]["cam1"]["problems"], [])


if __name__ == "__main__":
    unittest.main()
