import hashlib
import io
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import audio  # noqa: E402
import scratch  # noqa: E402

TAKE = "20261116-140834"


def wav_bytes(seconds=0.5, rate=48000):
    n = int(seconds * rate)
    return scratch.wav_header(n * 2, rate, 1) + struct.pack("<%dh" % n, *([1000, -1000] * (n // 2)))


class AudioTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.busy = False
        self.a = audio.Audio(self.root, takes=lambda: {TAKE}, busy=lambda: self.busy, free_bytes=lambda: 100 << 30)

    def upload(self, song, data, name="Song mix.wav", piece=4000):
        v = self.a.new_version(TAKE, song, name, len(data))
        off = 0
        while off < len(data):
            b = data[off:off + piece]
            r = self.a.put(TAKE, song, v["version"], off, io.BytesIO(b).read, len(b))
            off = r["received"]
        return self.a.finish(TAKE, song, v["version"])

    def test_songs_named_and_unique(self):
        s1 = self.a.new_song(TAKE)
        s2 = self.a.new_song(TAKE, "Blue Song!")
        self.assertEqual((s1["name"], s1["id"]), ("Track 1", "track-1"))
        self.assertEqual(s2["id"], "blue-song")
        with self.assertRaises(audio.AudioError):
            self.a.new_song(TAKE, "blue song!")
        self.assertEqual(self.a.new_song(TAKE, "Blue-song")["id"], "blue-song-2")

    def test_unknown_take_and_bad_names(self):
        with self.assertRaises(audio.AudioError):
            self.a.new_song("20990101-000000")
        with self.assertRaises(audio.AudioError):
            self.a.new_song("../etc")
        s = self.a.new_song(TAKE)
        with self.assertRaises(audio.AudioError):
            self.a.new_version(TAKE, s["id"], "notes.txt", 10)
        with self.assertRaises(audio.AudioError):
            self.a.new_version(TAKE, "nope", "a.wav", 10)

    def test_upload_in_pieces_then_finish(self):
        s = self.a.new_song(TAKE, "Song")
        data = wav_bytes()
        v = self.upload(s["id"], data)
        self.assertTrue(v["complete"])
        self.assertEqual(v["sha256"], hashlib.sha256(data).hexdigest())
        self.assertAlmostEqual(v["duration_s"], 0.5, places=2)
        self.assertEqual((v["sample_rate"], v["channels"]), (48000, 1))
        d = os.path.join(self.root, TAKE, "song", "v1")
        self.assertEqual(sorted(os.listdir(d)), ["Song mix.wav", "version.json"])
        with open(os.path.join(d, "Song mix.wav"), "rb") as f:
            self.assertEqual(f.read(), data)

    def test_wrong_offset_and_busy(self):
        s = self.a.new_song(TAKE)
        v = self.a.new_version(TAKE, s["id"], "a.wav", 100)
        self.a.put(TAKE, s["id"], 1, 0, io.BytesIO(b"x" * 40).read, 40)
        with self.assertRaises(audio.AudioError) as e:
            self.a.put(TAKE, s["id"], 1, 0, io.BytesIO(b"x" * 40).read, 40)
        self.assertIn("has 40 bytes", str(e.exception))
        self.busy = True
        with self.assertRaises(audio.Busy):
            self.a.put(TAKE, s["id"], 1, 40, io.BytesIO(b"x" * 40).read, 40)
        self.busy = False
        with self.assertRaises(audio.AudioError):           # short body: nothing kept
            self.a.put(TAKE, s["id"], 1, 40, io.BytesIO(b"x" * 10).read, 40)
        self.assertEqual(self.a.list(TAKE)["songs"][0]["versions"][0]["received"], 40)
        with self.assertRaises(audio.AudioError):
            self.a.finish(TAKE, s["id"], v["version"])      # not all there

    def test_same_file_again_resumes(self):
        s = self.a.new_song(TAKE)
        v1 = self.a.new_version(TAKE, s["id"], "mix.wav", 100)
        v2 = self.a.new_version(TAKE, s["id"], "mix.wav", 100)
        self.assertEqual(v1["version"], v2["version"])
        self.assertEqual(self.a.new_version(TAKE, s["id"], "mix.wav", 101)["version"], 2)

    def test_not_audio_is_refused_at_finish(self):
        s = self.a.new_song(TAKE)
        with self.assertRaises(audio.AudioError):
            self.upload(s["id"], b"this is not a wav file at all" * 10, name="fake.wav")

    def test_versions_newest_by_default_or_chosen(self):
        s = self.a.new_song(TAKE)
        self.upload(s["id"], wav_bytes(0.2), "a.wav")
        self.upload(s["id"], wav_bytes(0.3), "b.wav")
        self.assertEqual(self.a.list(TAKE)["songs"][0]["current"], 2)
        self.a.choose(TAKE, s["id"], 1)
        self.assertEqual(self.a.list(TAKE)["songs"][0]["current"], 1)
        self.a.new_version(TAKE, s["id"], "c.wav", 50)      # unfinished: can't be chosen
        with self.assertRaises(audio.AudioError):
            self.a.choose(TAKE, s["id"], 3)
        summ = self.a.summary(TAKE, on_drive={"track-1/v1/a.wav"})
        self.assertEqual([v["on_drive"] for v in summ["songs"][0]["versions"]], [True, False, False])
        self.assertEqual([v["complete"] for v in summ["songs"][0]["versions"]], [True, True, False])


class FakeProc:
    """Stands in for arecord: hands out n blocks of PCM, then EOF (or waits for terminate)."""
    def __init__(self, blocks, block_bytes, hold=False):
        self.data = io.BytesIO(b"\x01\x00" * (block_bytes // 2) * blocks)
        self.hold, self.done = hold, threading.Event()
        self.code = None

        class Out:
            def read(o, n):
                b = self.data.read(n)
                if not b and self.hold:
                    self.done.wait(5)
                return b
        self.stdout = Out()

    def terminate(self):
        self.code = -15
        self.done.set()

    def kill(self):
        pass

    def wait(self):
        return self.code if self.code is not None else 0


class ScratchTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.logs = []
        self.t = [1_000_000_000_000]

    def clock(self):
        self.t[0] += 100_000_000
        return self.t[0]

    def make(self, device="plughw:CARD=Mic,DEV=0", blocks=30, hold=True):
        block = 48000 // 10 * 2
        self.procs = []

        def popen(cmd, **kw):
            p = FakeProc(blocks, block, hold)
            self.procs.append((cmd, p))
            return p
        return scratch.Scratch({}, self.root, self.logs.append, clock_ns=self.clock, popen=popen,
                               find_device=lambda: device)

    def test_records_wav_and_block_times(self):
        sc = self.make()
        sc.start(TAKE)
        self.assertEqual(sc.status()["state"], "recording")
        for _ in range(100):
            if sc.samples >= 30 * 4800:
                break
            threading.Event().wait(0.01)
        meta = sc.stop()
        d = os.path.join(self.root, TAKE, "scratch")
        self.assertEqual(meta["samples"], 30 * 4800)
        with open(os.path.join(d, "scratch.wav"), "rb") as f:
            head = f.read(44)
        self.assertEqual(head[:4], b"RIFF")
        self.assertEqual(struct.unpack("<I", head[40:44])[0], 30 * 4800 * 2)
        with open(os.path.join(d, "scratch.times.tsv")) as f:
            rows = [l.split("\t") for l in f.read().split("\n") if l]
        self.assertEqual(len(rows), 30)
        self.assertEqual([int(r[0]) for r in rows[:3]], [0, 4800, 9600])
        self.assertTrue(int(rows[1][1]) > int(rows[0][1]))
        with open(os.path.join(d, "scratch.json")) as f:
            self.assertEqual(json.load(f)["samples"], 30 * 4800)
        self.assertEqual(sc.status()["state"], "ready")
        self.assertIn("-D", self.procs[0][0])

    def test_no_mic_records_nothing(self):
        sc = self.make(device=None)
        sc.start(TAKE)
        self.assertEqual(sc.status()["state"], "no mic")
        self.assertIsNone(sc.stop())
        self.assertFalse(os.path.exists(os.path.join(self.root, TAKE)))

    def test_start_failure_never_raises(self):
        def boom(cmd, **kw):
            raise OSError("arecord not found")
        sc = scratch.Scratch({}, self.root, self.logs.append, popen=boom, find_device=lambda: "hw:1")
        sc.start(TAKE)
        self.assertEqual(sc.status()["state"], "error")
        self.assertIsNone(sc.stop())

    def test_leftover_part_is_finished_on_start(self):
        d = os.path.join(self.root, TAKE, "scratch")
        os.makedirs(d)
        with open(os.path.join(d, "scratch.wav.part"), "wb") as f:
            f.write(scratch.wav_header(0, 48000, 1) + b"\x00\x00" * 4801)
        self.make()
        self.assertTrue(os.path.exists(os.path.join(d, "scratch.wav")))
        with open(os.path.join(d, "scratch.json")) as f:
            m = json.load(f)
        self.assertEqual(m["samples"], 4801)
        self.assertTrue(m["problems"])

    def test_find_usb_mic(self):
        p = tempfile.mkdtemp()
        with open(os.path.join(p, "cards"), "w") as f:
            f.write(" 0 [vc4hdmi0       ]: vc4-hdmi - vc4-hdmi-0\n"
                    " 2 [Device         ]: USB-Audio - USB PnP Sound Device\n")
        os.makedirs(os.path.join(p, "card0", "pcm0p"))
        os.makedirs(os.path.join(p, "card2", "pcm0c"))
        self.assertEqual(scratch.find_usb_mic(p), "plughw:CARD=Device,DEV=0")
        os.rename(os.path.join(p, "card2", "pcm0c"), os.path.join(p, "card2", "pcm0p"))
        self.assertIsNone(scratch.find_usb_mic(p))       # playback only (USB speaker)


if __name__ == "__main__":
    unittest.main()
