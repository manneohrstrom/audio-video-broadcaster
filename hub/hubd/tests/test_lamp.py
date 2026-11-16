import json
import os
import sys
import unittest
import urllib.parse
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import lamp  # noqa: E402

IDLE = {"mode": "idle"}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def rig(state="idle", nodes=None, take_id=None):
    return {"state": state, "take_id": take_id, "nodes": nodes if nodes is not None else {"cam1": IDLE, "cam2": IDLE}}


class Decide(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.cfg = lamp.merge_config({"type": "log"})
        self.l = lamp.RigLight(self.cfg, clock=self.clock)
        self.clock.t += 20          # past the startup grace

    def look(self, r=None, ing=None):
        return self.l.decide(r or rig(), ing or {})[0]

    def test_basic_states(self):
        self.assertEqual(self.look(), "ready")
        self.assertEqual(self.look(rig("starting")), "arming")
        self.assertEqual(self.look(rig("recording")), "recording")
        self.assertEqual(self.look(rig("stopping")), "recording")
        self.assertEqual(self.look(rig(nodes={"cam1": {"mode": "live"}, "cam2": IDLE})), "live")

    def test_offline_camera_is_an_error_after_grace(self):
        fresh = lamp.RigLight(self.cfg, clock=self.clock)
        r = rig(nodes={"cam1": IDLE, "cam2": None})
        self.assertEqual(fresh.decide(r, {})[0], "ready")      # hubd just started
        look, why = self.l.decide(r, {})
        self.assertEqual(look, "error")
        self.assertIn("cam2", why)

    def test_ingest_then_done_until_eject_or_take(self):
        self.assertEqual(self.look(ing={"status": "waiting for 30 min idle"}), "ready")
        self.assertEqual(self.look(ing={"status": "ingesting"}), "ingest")
        self.assertEqual(self.look(ing={"status": "idle: nothing to ingest"}), "ingest_done")
        self.assertEqual(self.look(ing={"status": "idle: nothing to ingest"}), "ingest_done")
        self.assertEqual(self.look(ing={"status": "idle: nothing to ingest", "drive": "ejected"}), "ready")
        self.assertEqual(self.look(ing={"status": "idle: nothing to ingest"}), "ready")
        # A new take also clears it.
        self.look(ing={"status": "ingesting"})
        self.look(ing={"status": "idle: nothing to ingest"})
        self.look(rig("recording"), {"status": "rig busy"})
        self.assertEqual(self.look(ing={"status": "idle: nothing to ingest"}), "ready")

    def test_paused_ingest_is_not_done(self):
        self.look(ing={"status": "ingesting"})
        self.assertEqual(self.look(rig("starting"), {"status": "paused: rig busy"}), "arming")
        self.assertEqual(self.look(ing={"status": "rig busy"}), "ready")

    def test_new_drops_flash_for_a_while(self):
        n = lambda d: {"cam1": {"mode": "recording", "take": {"dropped_so_far": d}}, "cam2": IDLE}  # noqa: E731
        self.assertEqual(self.look(rig("recording", n(0), "t1")), "recording")
        self.assertEqual(self.look(rig("recording", n(2), "t1")), "error")
        self.clock.t += 9
        self.assertEqual(self.look(rig("recording", n(2), "t1")), "recording")   # no new drops
        self.assertEqual(self.look(rig("recording", n(3), "t1")), "error")
        self.clock.t += 9
        self.assertEqual(self.look(rig("recording", n(1), "t2")), "error")       # new take starts at 0

    def test_drive_problems(self):
        self.assertEqual(self.look(ing={"status": "idle", "drive_free_gb": 1.0}), "error")
        self.assertEqual(self.look(ing={"status": "no STUDIO drive", "drive_free_gb": 1.0}), "ready")
        self.assertEqual(self.look(ing={"status": "drive needs repair (fsck)"}), "error")

    def test_alert_and_preview(self):
        self.l.alert("REC failed")
        self.assertEqual(self.l.decide(rig(), {}), ("error", "REC failed"))
        self.clock.t += 9
        self.assertEqual(self.look(), "ready")
        self.l.preview("live", 5)
        self.assertEqual(self.look(rig("recording")), "live")
        self.clock.t += 6
        self.assertEqual(self.look(rig("recording")), "recording")


class Frames(unittest.TestCase):
    def test_flash_and_pulse(self):
        looks = lamp.DEFAULT_LAMP["looks"]
        self.assertEqual(lamp.frame(looks["recording"], 0.3), ([255, 0, 0], 100, 0.0))
        self.assertEqual(lamp.frame(looks["error"], 0.3)[1], 100)
        self.assertEqual(lamp.frame(looks["error"], 1.3)[1], 0)
        self.assertEqual(lamp.frame(looks["ingest"], 0.5), ([0, 255, 255], 40, 2.0))
        self.assertEqual(lamp.frame(looks["ingest"], 2.5)[1], 10)

    def test_config_override_keeps_other_fields(self):
        cfg = lamp.merge_config({"type": "tasmota", "looks": {"ready": {"dim": 3}}})
        self.assertEqual(cfg["looks"]["ready"], {"rgb": [0, 255, 0], "dim": 3})
        self.assertEqual(cfg["looks"]["live"]["dim"], 60)


class Drivers(unittest.TestCase):
    def test_tasmota_url(self):
        got = []
        t = lamp.Tasmota(lamp.merge_config({"type": "tasmota", "address": "10.0.0.9"}),
                         opener=lambda u, timeout: got.append((u, timeout)) or b'{"POWER":"ON"}')
        t.send([255, 0, 0], 100, 0.0)
        t.send([0, 255, 255], 10, 2.0)
        q = [urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["cmnd"][0] for u, _ in got]
        self.assertTrue(got[0][0].startswith("http://10.0.0.9/cm?"))
        self.assertEqual(q[0], "Backlog0 Fade 0; Color2 255,0,0; Dimmer 100")
        self.assertEqual(q[1], "Backlog0 Fade 1; Speed 4; Color2 0,255,255; Dimmer 10")
        self.assertEqual(got[0][1], 1.0)

    def test_tasmota_rejects_unknown_command(self):
        t = lamp.Tasmota(lamp.merge_config({"address": "x"}), opener=lambda u, timeout: b'{"Command":"Unknown"}')
        with self.assertRaises(OSError):
            t.send([1, 2, 3], 50, 0)

    def test_esphome_requests(self):
        got = []
        e = lamp.ESPHome(lamp.merge_config({"type": "esphome", "address": "bulb.local", "light_id": "rgb_bulb"}),
                         opener=lambda r, timeout: got.append(r))
        e.send([150, 0, 255], 40, 0.0)
        e.send([255, 0, 0], 0, 0.0)
        self.assertEqual(got[0].get_method(), "POST")
        self.assertEqual(got[0].full_url, "http://bulb.local/light/rgb_bulb/turn_on?r=150&g=0&b=255&brightness=102&transition=0.0")
        self.assertEqual(got[1].full_url, "http://bulb.local/light/rgb_bulb/turn_off?transition=0.0")
        with self.assertRaises(ValueError):
            lamp.ESPHome(lamp.merge_config({"type": "esphome", "address": "x"}))


class Worker(unittest.TestCase):
    def setUp(self):
        self.clock, self.sent, self.logs = Clock(), [], []
        self.fail = False
        self.r, self.ing = rig(), {}

        class D:
            def send(d, *fr):
                if self.fail:
                    raise OSError("timed out")
                self.sent.append(fr)
        self.lamp = lamp.Lamp({"type": "log"}, lambda: (self.r, self.ing), self.logs.append, driver=D(), clock=self.clock)

    def test_sends_on_change_and_refresh_only(self):
        self.lamp.step()
        self.lamp.step()
        self.assertEqual(len(self.sent), 1)
        self.r = rig("recording")
        self.lamp.step()
        self.assertEqual(self.sent[-1][1], 100)
        self.clock.t += 31
        self.lamp.step()
        self.assertEqual(len(self.sent), 3)        # refresh
        self.assertEqual(self.lamp.status()["look"], "recording")

    def test_unreachable_bulb_logs_once_and_retries(self):
        self.fail = True
        for _ in range(3):
            self.lamp.step()
            self.clock.t += 1
        self.assertEqual(sum("unreachable" in m for m in self.logs), 1)
        self.assertFalse(self.lamp.status()["reachable"])
        self.fail = False
        self.clock.t += 5
        self.lamp.step()
        self.assertTrue(self.lamp.status()["reachable"])
        self.assertEqual(len(self.sent), 1)

    def test_preview_rejects_unknown_look(self):
        with self.assertRaises(ValueError):
            self.lamp.preview("disco")

    def test_disabled_without_type(self):
        l = lamp.Lamp({}, lambda: (rig(), {}), self.logs.append)
        self.assertFalse(l.enabled)
        self.assertEqual(json.loads(json.dumps(l.status()))["enabled"], False)


if __name__ == "__main__":
    unittest.main()
