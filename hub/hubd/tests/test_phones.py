import io
import json
import os
import sys
import tempfile
import threading
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
        cfg.update(state_dir=tempfile.mkdtemp(), phone_join_in_background=False)   # joins in the call (see below)
        self.cfg = cfg
        self.ph = phones.Phones(cfg, clock=lambda: self.now[0])
        self.rig = {"state": "idle", "take_id": None}

    def poll(self, name="phone-aaaa", page="pageAAAAAAAA", **st):
        return self.ph.poll(dict({"device": name, "page": page, "state": "ready"}, **st), self.rig)

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

    def test_finish_answers_at_once_and_joins_in_the_background(self):
        # user, 2026-11-21: the phone is done as soon as every piece is on the hub
        ph = phones.Phones(dict(self.cfg, phone_join_in_background=True), clock=lambda: self.now[0])
        chunks(ph, "20261116-100000", "phone1", drop_at=100)
        gate = threading.Event()
        real = phones.assemble
        phones.assemble = lambda *a: (gate.wait(5), real(*a))[1]
        try:
            quick = ph.finish("20261116-100000", "phone1", {"clock": clock_samples(), "chunks": 3})
            self.assertEqual((quick["complete"], quick["joining"], quick["frames"], quick["dropped_frames"]), (True, True, 180, 2))
            self.assertTrue(ph.is_joining("20261116-100000", "phone1"))
            self.assertIsNone(ph.manifest("20261116-100000", "phone1"))          # ingest waits for this
            self.assertTrue(ph.have("20261116-100000", "phone1")["complete"])    # the phone won't resend
            self.assertTrue(ph.finish("20261116-100000", "phone1", {})["joining"])   # a retry meanwhile
        finally:
            gate.set()
            ph.wait_joins(5)
            phones.assemble = real
        man = ph.manifest("20261116-100000", "phone1")
        self.assertEqual((man["complete"], man["frames"]), (True, 180))
        self.assertFalse(ph.is_joining("20261116-100000", "phone1"))

    def test_a_join_cut_off_by_a_restart_is_finished_on_start(self):
        chunks(self.ph, "20261116-100000", "phone1")
        d = self.ph.take_dir("20261116-100000", "phone1")
        phones.write_json(os.path.join(d, "finish.json"), {"clock": clock_samples(), "chunks": 3,
                                                           "phone": "phone1", "take_id": "20261116-100000"})
        ph = phones.Phones(dict(self.cfg, phone_join_in_background=True), clock=lambda: self.now[0])
        ph.wait_joins(5)
        self.assertEqual(ph.manifest("20261116-100000", "phone1")["frames"], 180)

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
        for bad in ("cam1", "Paul's Pixel", "phone-", "phone-AAAA", "phone-aaaa/..", ""):
            with self.assertRaises(phones.PhoneError):
                self.poll(name=bad)

    def test_reconcile_rec_and_stop(self):
        self.assertIsNone(self.poll()["cmd"])
        self.rig = {"state": "starting", "take_id": "20261116-100000"}
        cmd = self.poll()["cmd"]
        self.assertEqual((cmd["action"], cmd["take_id"], cmd["part"]), ("rec", "20261116-100000", "phone-aaaa"))
        self.assertEqual(self.poll()["cmd"]["id"], cmd["id"])                # same command until acked
        self.assertIsNone(self.poll(state="recording", take="20261116-100000", done_cmd=cmd["id"])["cmd"])
        self.rig = {"state": "idle", "take_id": None}
        stop = self.poll(state="recording", take="20261116-100000", done_cmd=cmd["id"])["cmd"]
        self.assertEqual((stop["action"], stop["take_id"]), ("stop", "20261116-100000"))
        self.assertIsNone(self.poll(state="uploading", take="20261116-100000", done_cmd=stop["id"])["cmd"])
        self.assertEqual(self.ph.phones_in("20261116-100000"), ["phone-aaaa"])

    def test_late_join_and_rejoin_get_their_own_part(self):
        self.rig = {"state": "recording", "take_id": "20261116-100000"}
        c1 = self.poll()["cmd"]
        self.assertEqual(c1["part"], "phone-aaaa")
        self.poll(state="recording", take="20261116-100000", done_cmd=c1["id"])
        # the page reloads mid-take: a new page id, ready again
        self.now[0] += 10
        c2 = self.poll(page="pageBBBBBBBB")["cmd"]
        self.assertEqual((c2["action"], c2["part"]), ("rec", "phone-aaaa-part2"))

    def test_a_phone_is_shown_by_its_id_until_named(self):
        r = self.poll()
        self.assertEqual(r["id"], "phone-aaaa")
        self.assertNotIn("colour", r)
        self.assertEqual(self.ph.snapshot(None)["phone-aaaa"]["label"], "phone-aaaa")

    def test_me_without_joining(self):
        self.assertEqual(self.ph.me("phone-aaaa"), {"id": "phone-aaaa"})
        self.assertEqual(self.ph.snapshot(None), {})                           # not on the hub page yet
        with self.assertRaises(phones.PhoneError):
            self.ph.me("../x")

    def test_tripod_page_means_focus_locked(self):
        self.ph.set_mount("phone-aaaa", "handheld")
        self.poll(mode="handheld")
        self.assertEqual(self.ph.snapshot(None)["phone-aaaa"]["mount"], "handheld")
        self.now[0] += 10
        r = self.poll(page="pageBBBBBBBB", mode="tripod", state="off")
        self.assertEqual(r["mount"], "tripod")
        self.assertEqual(self.ph.snapshot(None)["phone-aaaa"]["mode"], "tripod")
        self.ph.set_mount("phone-aaaa", "handheld")             # the hub page can still change it
        self.assertEqual(self.poll(page="pageBBBBBBBB", mode="tripod")["mount"], "handheld")

    def test_page_open_twice(self):
        self.poll()
        with self.assertRaises(phones.PhoneError):
            self.poll(page="pageBBBBBBBB")
        self.now[0] += 10                                                     # the first one went away
        self.poll(page="pageBBBBBBBB")

    def test_bye_lets_the_next_page_in_at_once(self):
        self.poll()
        self.ph.bye({"device": "phone-aaaa", "page": "pageXXXXXXXX"})        # someone else's page: ignored
        with self.assertRaises(phones.PhoneError):
            self.poll(page="pageBBBBBBBB")
        self.ph.bye({"device": "phone-aaaa", "page": "pageAAAAAAAA"})
        self.poll(page="pageBBBBBBBB")
        with self.assertRaises(phones.PhoneError):                           # and the new page holds it again
            self.poll(page="pageCCCCCCCC")

    def test_identify_flashes_for_a_few_seconds(self):
        with self.assertRaises(phones.PhoneError):
            self.ph.identify("phone-aaaa")
        self.assertFalse(self.poll()["identify"])
        self.ph.identify("phone-aaaa")
        self.assertTrue(self.poll()["identify"])
        self.now[0] += phones.IDENTIFY_S + 0.1
        self.assertFalse(self.poll()["identify"])

    def test_reload_only_between_takes(self):
        with self.assertRaises(phones.PhoneError):
            self.ph.reload("phone-aaaa")
        self.poll(state="recording", take="20261116-100000")
        with self.assertRaises(phones.PhoneError):
            self.ph.reload("phone-aaaa")
        self.poll(state="ready", queue=3)
        with self.assertRaises(phones.PhoneError):
            self.ph.reload("phone-aaaa")
        self.poll(state="ready", queue=0)
        self.ph.reload("phone-aaaa")
        self.assertTrue(self.poll()["reload"])
        self.assertFalse(self.poll()["reload"])                              # once
        self.poll(page="pageBBBBBBBB")                                       # the reloaded page takes over at once

    def test_problems_are_warnings(self):
        self.poll(charging=False, battery=30, visible="hidden", locked=False, rtt_ms=90, storage_free_gb=2)
        p = self.ph.snapshot(free_gb=3.0)["phone-aaaa"]["problems"]
        text = " | ".join(p)
        for want in ("phone-aaaa not charging", "not in front", "not calibrated", "Wi-Fi slow", "hub has room for 0.4 h", "phone storage low"):
            self.assertIn(want, text)
        self.ph.given_name = lambda name: "Left wide"
        self.assertIn("Left wide not charging", " | ".join(self.ph.snapshot(free_gb=3.0)["phone-aaaa"]["problems"]))
        self.now[0] += 30
        snap = self.ph.snapshot(free_gb=3.0)["phone-aaaa"]
        self.assertFalse(snap["online"])
        self.assertEqual(snap["problems"], [])


class HubPhonesTest(unittest.TestCase):
    def setUp(self):
        self.cfg = hubd.load_config(None)
        self.cfg.update(state_dir=tempfile.mkdtemp(), client_head_start_s=0, sync_timeout_s=1)
        self.rig = FakeRig(self.cfg["nodes"])
        self.hub = hubd.Hub(self.cfg, nodes=self.rig, sleep=lambda s: None)

    def test_poll_carries_the_name_given_on_the_hub_page(self):
        body = {"device": "phone-aaaa", "page": "pageAAAAAAAA", "state": "off", "mode": "tripod"}
        self.assertIsNone(self.hub.phone_poll(body)["name"])
        self.hub.names.set("cameras", "phone-aaaa", "Left wide")
        self.assertEqual(self.hub.phone_poll(body)["name"], "Left wide")

    def test_phone_follows_rec_and_stop_and_is_logged(self):
        body = {"device": "phone1", "page": "pageAAAAAAAA", "state": "ready"}
        with self.assertRaises(phones.PhoneError):              # not a device id
            self.hub.phone_poll(body)
        body["device"] = "phone-0001"
        self.assertIsNone(self.hub.phone_poll(body)["cmd"])
        take = self.hub.rec()
        cmd = self.hub.phone_poll(body)["cmd"]
        self.assertEqual((cmd["action"], cmd["take_id"]), ("rec", take["take_id"]))
        summary = self.hub.stop()
        self.assertEqual(summary["phones"], ["phone-0001"])
        self.assertEqual(self.hub.phone_poll(dict(body, state="recording", take=take["take_id"], done_cmd=cmd["id"]))["cmd"]["action"], "stop")
        self.assertIn("phone-0001", self.hub.snapshot()["phones"])
        # take list: uploading until the phone finishes, then frames
        cams = self.hub.ingest_state()["takes"][take["take_id"]]["recorded"]["cameras"]
        self.assertTrue(cams["phone-0001"]["uploading"])
        chunks(self.hub.phones, take["take_id"], "phone-0001")
        self.hub.phones.finish(take["take_id"], "phone-0001", {"clock": clock_samples(), "chunks": 3})
        self.hub.phones.wait_joins(5)
        cams = self.hub.ingest_state()["takes"][take["take_id"]]["recorded"]["cameras"]
        self.assertEqual((cams["phone-0001"]["frames"], cams["phone-0001"]["uploading"]), (180, False))
        self.assertIn("cam1", cams)

    def test_phones_never_block_rec(self):
        self.hub.phone_poll({"device": "phone-0001", "page": "pageAAAAAAAA", "state": "off", "charging": False})
        self.assertEqual(self.hub.rec()["nodes"], self.cfg["nodes"])


if __name__ == "__main__":
    unittest.main()
