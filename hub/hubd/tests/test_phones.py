import io
import json
import os
import sys
import tempfile
import unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import hubd  # noqa: E402
import phones  # noqa: E402
from test_hubd import FakeRig  # noqa: E402

T0 = 1_794_779_424_000_000_000          # hub ns at page perf 0


def clock_samples(n=10, rtt=8.0):
    return [{"perf_ms": 5000.0 + 1000 * k, "hub_ns": T0 + (5000 + 1000 * k) * 1_000_000, "rtt_ms": rtt + k % 3}
            for k in range(n)]


def chunks(ph, take, part, n=3, per=60, drop_at=None, cam_offset_ms=-800_000.0, delivery_ms=4.0):
    """Upload n chunks; frame i captured at perf 6000 + i*33.333 ms."""
    i = 0
    for s in range(n):
        fr = []
        for _ in range(per):
            if drop_at is not None and i == drop_at:
                i += 2                                   # two frames lost
            cap = 6000 + i * 1000 / 30
            fr.append({"ts_us": round((cap + cam_offset_ms) * 1000), "arr_ms": cap + delivery_ms, "key": not fr})
            i += 1
        h = b"\x00\x00\x00\x01" + bytes([s]) * 50
        ph.put_chunk(take, part, s, "h264", io.BytesIO(h).read, len(h))
        j = json.dumps({"frames": fr}).encode()
        ph.put_chunk(take, part, s, "json", io.BytesIO(j).read, len(j))


class PhonesTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        cfg = hubd.load_config(None)
        cfg.update(state_dir=tempfile.mkdtemp())
        self.ph = phones.Phones(cfg, clock=lambda: self.now[0])
        self.rig = {"state": "idle", "take_id": None}

    def poll(self, name="phone1", page="pageAAAAAAAA", **st):
        return self.ph.poll(dict({"phone": name, "page": page, "state": "ready"}, **st), self.rig)

    def test_fit_clock_offset_and_drift(self):
        fn, info = phones.fit_clock(clock_samples())
        self.assertEqual(fn(6000.0), T0 + 6_000_000_000)
        self.assertEqual(info["drift_ppm"], 0.0)
        drift = [dict(s, hub_ns=s["hub_ns"] + int(s["perf_ms"] * 10)) for s in clock_samples(200)]   # +10 ppm
        _, info = phones.fit_clock(drift)
        self.assertAlmostEqual(info["drift_ppm"], 10.0, delta=0.5)
        with self.assertRaises(phones.PhoneError):
            phones.fit_clock([])

    def test_assemble_corrects_delay_and_finds_gaps(self):
        chunks(self.ph, "20261116-100000", "phone1", drop_at=100)
        man = self.ph.finish("20261116-100000", "phone1", {"clock": clock_samples(), "chunks": 3,
                                                           "info": {"model": "Pixel 3"}})
        self.assertTrue(man["complete"])
        self.assertEqual(man["frames"], 180)
        self.assertEqual(man["dropped_frames"], 2)
        self.assertEqual(man["gaps"][0]["after_frame"], 99)
        # first frame captured at perf 6000 ms; the earliest arrival is 4 ms later, and the
        # fixed 40 ms capture delay is subtracted on top of that.
        self.assertAlmostEqual(man["first_frame_wallclock_raw_ns"], T0 + 6_004_000_000, delta=1000)   # µs-rounded test frames
        self.assertEqual(man["first_frame_wallclock_raw_ns"] - man["first_frame_wallclock_ns"], 40_000_000)
        d = self.ph.take_dir("20261116-100000", "phone1")
        self.assertFalse(os.path.exists(os.path.join(d, "chunks")))         # joined, then deleted
        with open(os.path.join(d, "video.pts")) as f:
            lines = f.read().split("\n")
        self.assertEqual(lines[0], "# timecode format v2")
        self.assertEqual(len([l for l in lines[1:] if l]), 180)
        self.assertEqual(os.path.getsize(os.path.join(d, "video.h264")), 3 * 54)
        # finishing again (a retry from the phone) returns the same result
        self.assertEqual(self.ph.finish("20261116-100000", "phone1", {})["frames"], 180)
        self.assertTrue(self.ph.have("20261116-100000", "phone1")["complete"])

    def test_delay_per_model(self):
        self.assertEqual(phones.capture_delay_ms({"default": 40, "Pixel 9": 25}, "Pixel 9"), 25)
        self.assertEqual(phones.capture_delay_ms({"default": 40}, None), 40)

    def test_missing_chunks_keep_the_rest_until_complete(self):
        chunks(self.ph, "20261116-100000", "phone1", n=3)
        os.remove(os.path.join(self.ph.take_dir("20261116-100000", "phone1"), "chunks", "000001.json"))
        man = self.ph.finish("20261116-100000", "phone1", {"clock": clock_samples(), "chunks": 3})
        self.assertFalse(man["complete"])
        self.assertEqual(man["missing_chunks"], [1])
        self.assertEqual(self.ph.have("20261116-100000", "phone1"), {"have": [0, 2], "complete": False})
        chunks(self.ph, "20261116-100000", "phone1", n=3)                    # the phone resends
        self.assertTrue(self.ph.finish("20261116-100000", "phone1", {"clock": clock_samples(), "chunks": 3})["complete"])

    def test_bad_input(self):
        for take, part in (("../x", "phone1"), ("20261116-100000", "cam1"), ("20261116-100000", "phone1/..")):
            with self.assertRaises(phones.PhoneError):
                self.ph.take_dir(take, part)
        with self.assertRaises(phones.PhoneError):
            self.ph.put_chunk("20261116-100000", "phone1", 0, "h264", None, phones.MAX_CHUNK + 1)
        with self.assertRaises(phones.PhoneError):
            self.ph.put_chunk("20261116-100000", "phone1", 0, "json", io.BytesIO(b"{no").read, 3)
        with self.assertRaises(phones.PhoneError):
            self.poll(name="cam1")

    def test_reconcile_rec_and_stop(self):
        self.assertIsNone(self.poll()["cmd"])
        self.rig = {"state": "starting", "take_id": "20261116-100000"}
        cmd = self.poll()["cmd"]
        self.assertEqual((cmd["action"], cmd["take_id"], cmd["part"]), ("rec", "20261116-100000", "phone1"))
        self.assertEqual(self.poll()["cmd"]["id"], cmd["id"])                # same command until acked
        self.assertIsNone(self.poll(state="recording", take="20261116-100000", done_cmd=cmd["id"])["cmd"])
        self.rig = {"state": "idle", "take_id": None}
        stop = self.poll(state="recording", take="20261116-100000", done_cmd=cmd["id"])["cmd"]
        self.assertEqual((stop["action"], stop["take_id"]), ("stop", "20261116-100000"))
        self.assertIsNone(self.poll(state="uploading", take="20261116-100000", done_cmd=stop["id"])["cmd"])
        self.assertEqual(self.ph.phones_in("20261116-100000"), ["phone1"])

    def test_late_join_and_rejoin_get_their_own_part(self):
        self.rig = {"state": "recording", "take_id": "20261116-100000"}
        c1 = self.poll()["cmd"]
        self.assertEqual(c1["part"], "phone1")
        self.poll(state="recording", take="20261116-100000", done_cmd=c1["id"])
        # the page reloads mid-take: a new page id, ready again
        self.now[0] += 10
        c2 = self.poll(page="pageBBBBBBBB")["cmd"]
        self.assertEqual((c2["action"], c2["part"]), ("rec", "phone1-part2"))

    def test_labels_become_safe_ids(self):
        self.assertEqual(phones.phone_id("Paul's Pixel"), "pauls-pixel")
        self.assertEqual(phones.phone_id("  Pixel 9 (left)  "), "pixel-9-left")
        self.assertEqual(phones.phone_id("phone1"), "phone1")
        for bad in ("", "cam1", "!!!", "x" * 25):
            with self.assertRaises(phones.PhoneError):
                phones.phone_id(bad)
        r = self.poll(name="Paul's Pixel")
        self.assertEqual(r["id"], "pauls-pixel")
        self.assertEqual(self.ph.snapshot(None)["pauls-pixel"]["label"], "Paul's Pixel")
        with self.assertRaises(phones.PhoneError):            # same id from another phone
            self.poll(name="Pauls pixel", page="pageBBBBBBBB")
        self.rig = {"state": "recording", "take_id": "20261116-100000"}
        self.assertEqual(self.poll(name="Paul's Pixel")["cmd"]["part"], "pauls-pixel")

    def test_name_in_use_by_another_page(self):
        self.poll()
        with self.assertRaises(phones.PhoneError):
            self.poll(page="pageBBBBBBBB")
        self.now[0] += 10                                                     # the first one went away
        self.poll(page="pageBBBBBBBB")

    def test_problems_are_warnings(self):
        self.poll(charging=False, battery=30, visible="hidden", locked=False, rtt_ms=90, storage_free_gb=2)
        p = self.ph.snapshot(free_gb=3.0)["phone1"]["problems"]
        text = " | ".join(p)
        for want in ("not charging", "not in front", "not calibrated", "Wi-Fi slow", "hub has room for 0.4 h", "phone storage low"):
            self.assertIn(want, text)
        self.now[0] += 30
        snap = self.ph.snapshot(free_gb=3.0)["phone1"]
        self.assertFalse(snap["online"])
        self.assertEqual(snap["problems"], [])


class HubPhonesTest(unittest.TestCase):
    def setUp(self):
        self.cfg = hubd.load_config(None)
        self.cfg.update(state_dir=tempfile.mkdtemp(), client_head_start_s=0, sync_timeout_s=1)
        self.rig = FakeRig(self.cfg["nodes"])
        self.hub = hubd.Hub(self.cfg, nodes=self.rig, sleep=lambda s: None)

    def test_phone_follows_rec_and_stop_and_is_logged(self):
        body = {"phone": "phone1", "page": "pageAAAAAAAA", "state": "ready"}
        self.assertIsNone(self.hub.phone_poll(body)["cmd"])
        take = self.hub.rec()
        cmd = self.hub.phone_poll(body)["cmd"]
        self.assertEqual((cmd["action"], cmd["take_id"]), ("rec", take["take_id"]))
        summary = self.hub.stop()
        self.assertEqual(summary["phones"], ["phone1"])
        self.assertEqual(self.hub.phone_poll(dict(body, state="recording", take=take["take_id"], done_cmd=cmd["id"]))["cmd"]["action"], "stop")
        self.assertIn("phone1", self.hub.snapshot()["phones"])
        # take list: uploading until the phone finishes, then frames
        cams = self.hub.ingest_state()["takes"][take["take_id"]]["recorded"]["cameras"]
        self.assertTrue(cams["phone1"]["uploading"])
        chunks(self.hub.phones, take["take_id"], "phone1")
        self.hub.phones.finish(take["take_id"], "phone1", {"clock": clock_samples(), "chunks": 3})
        cams = self.hub.ingest_state()["takes"][take["take_id"]]["recorded"]["cameras"]
        self.assertEqual((cams["phone1"]["frames"], cams["phone1"]["uploading"]), (180, False))
        self.assertIn("cam1", cams)

    def test_phones_never_block_rec(self):
        self.hub.phone_poll({"phone": "phone1", "page": "pageAAAAAAAA", "state": "off", "charging": False})
        self.assertEqual(self.hub.rec()["nodes"], self.cfg["nodes"])


if __name__ == "__main__":
    unittest.main()
