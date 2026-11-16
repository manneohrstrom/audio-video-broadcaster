"""Hub mic scratch recording (Phase 4 Track A, docs/phase4-plan.md).

During every take the hub records its USB mic, so the DAW bounce can later be placed
in the take by matching audio against audio. The recording must be stamped against
the hub's clock, the clock the cameras' frames are stamped against, and the mic's own
sample clock drifts against it (spike A2 measures how much). So every 100 ms block is
stamped with CLOCK_REALTIME as it arrives; the matcher fits sample index -> hub time
from those stamps (the lower edge of the cloud: a block is never read before it was
captured), whatever the drift turns out to be.

Files, in <audio_dir>/<take>/scratch/ (copied to the drive by hub-ingest):
    scratch.wav         16-bit PCM as recorded
    scratch.times.tsv   "<first sample index of block>\t<hub time ns when read>" per block
    scratch.json        device, rate, channels, samples, start/stop times, problems
    arecord.log         arecord's own messages (overruns)
Both data files are *.part while recording. A recording left as .part (hubd killed
mid-take) is finished on the next start.

No mic plugged in, or arecord failing, never affects REC: it is logged and shown.
"""
import json
import os
import re
import struct
import subprocess
import threading
import time

DEFAULT_SCRATCH = {"enabled": True, "device": None, "rate": 48000, "channels": 1, "block_ms": 100}


def find_usb_mic(proc="/proc/asound"):
    """ALSA device of the first USB audio card with a capture device, or None."""
    try:
        with open(os.path.join(proc, "cards")) as f:
            cards = f.read()
    except OSError:
        return None
    for m in re.finditer(r"^\s*(\d+)\s+\[(\S+)\s*\]:\s+USB-Audio", cards, re.M):
        n, ident = m.groups()
        if any(e.endswith("c") for e in os.listdir(os.path.join(proc, "card" + n)) if e.startswith("pcm")):
            return "plughw:CARD=%s,DEV=0" % ident
    return None


def wav_header(data_bytes, rate, channels, bits=16):
    block = channels * bits // 8
    return (b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVE" +
            b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, bits) +
            b"data" + struct.pack("<I", data_bytes))


def finish_wav(part, final, rate, channels):
    """Write the real header into a .part WAV and rename it."""
    size = os.path.getsize(part) - 44
    size -= size % (2 * channels)
    with open(part, "r+b") as f:
        f.truncate(44 + size)
        f.seek(0)
        f.write(wav_header(min(size, 0xFFFFFFFF - 36), rate, channels))
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, final)
    return size // (2 * channels)


class Scratch:
    def __init__(self, cfg, root, log, clock_ns=time.time_ns, popen=subprocess.Popen, find_device=find_usb_mic):
        self.cfg = dict(DEFAULT_SCRATCH, **(cfg or {}))
        self.root, self.log, self.clock_ns, self.popen, self.find_device = root, log, clock_ns, popen, find_device
        self.lock = threading.Lock()
        self.proc = self.thread = None
        self.take_id = self.error = None
        self.samples = 0
        self.started_ns = None
        self.recover()

    def device(self):
        return self.cfg["device"] or self.find_device()

    def dir(self, take_id):
        return os.path.join(self.root, take_id, "scratch")

    def status(self):
        with self.lock:
            rec = self.thread is not None
            dev = self.device() if self.cfg["enabled"] else None
            st = ("off" if not self.cfg["enabled"] else "recording" if rec else
                  "error" if self.error else "ready" if dev else "no mic")
            return {"state": st, "device": dev, "take_id": self.take_id if rec else None,
                    "seconds": round(self.samples / self.cfg["rate"], 1) if rec else None, "error": self.error}

    def start(self, take_id):
        """Start recording for a take. Never raises: REC must not depend on the mic."""
        try:
            with self.lock:
                if self.thread is not None or not self.cfg["enabled"]:
                    return
                dev = self.device()
                if not dev:
                    self.error = None
                    self.log("scratch: no USB mic, take %s has no scratch recording" % take_id)
                    return
                d = self.dir(take_id)
                os.makedirs(d, exist_ok=True)
                r, c = self.cfg["rate"], self.cfg["channels"]
                cmd = ["arecord", "-q", "-D", dev, "-f", "S16_LE", "-r", str(r), "-c", str(c), "-t", "raw",
                       "--period-time=10000", "--buffer-time=500000"]
                # stderr to a file: a long take's overrun messages could fill a pipe and stall arecord
                self.errlog = open(os.path.join(d, "arecord.log"), "wb")
                self.proc = self.popen(cmd, stdout=subprocess.PIPE, stderr=self.errlog)
                self.take_id, self.error, self.samples = take_id, None, 0
                self.started_ns = self.clock_ns()
                self.meta = {"take_id": take_id, "device": dev, "rate": r, "channels": c, "format": "S16_LE",
                             "started_ns": self.started_ns, "command": cmd, "problems": []}
                self.thread = threading.Thread(target=self._run, args=(d,), daemon=True)
                self.thread.start()
            self.log("scratch: recording take %s from %s" % (take_id, dev))
        except Exception as e:  # noqa: BLE001
            with self.lock:
                if getattr(self, "errlog", None):
                    self.errlog.close()
                self.error, self.proc, self.thread = "start failed: %s" % e, None, None
            self.log("scratch: %s" % self.error)

    def _run(self, d):
        r, c = self.cfg["rate"], self.cfg["channels"]
        block = r * self.cfg["block_ms"] // 1000 * 2 * c
        wav, tsv = os.path.join(d, "scratch.wav.part"), os.path.join(d, "scratch.times.tsv.part")
        with open(wav, "wb") as fw, open(tsv, "w") as ft:
            fw.write(wav_header(0, r, c))
            n = 0
            while True:
                b = self.proc.stdout.read(block)
                t = self.clock_ns()
                if not b:
                    break
                ft.write("%d\t%d\n" % (n, t))
                fw.write(b)
                n += len(b) // (2 * c)
                self.samples = n
                if n % (r * 10) < r * self.cfg["block_ms"] // 1000:      # about every 10 s
                    fw.flush()
                    ft.flush()
        code = self.proc.wait()
        self.errlog.close()
        try:
            with open(os.path.join(d, "arecord.log"), "rb") as f:
                err = f.read()[-4000:].decode(errors="replace").strip()
        except OSError:
            err = ""
        with self.lock:
            if code not in (0, -15, 143) and self.take_id:
                self.error = "arecord stopped (code %s): %s" % (code, err[-200:])
                self.meta["problems"].append(self.error)
                self.log("scratch: %s" % self.error)
            if "overrun" in err.lower():
                self.meta["problems"].append("overruns: %s" % err[-200:])

    def stop(self):
        """Stop and finish the files. Never raises."""
        with self.lock:
            proc, thread, take = self.proc, self.thread, self.take_id
        if thread is None:
            return None
        try:
            proc.terminate()
        except OSError:
            pass
        thread.join(10)
        try:
            proc.kill()
        except OSError:
            pass
        with self.lock:
            meta = self.meta
            meta["stopped_ns"] = self.clock_ns()
            self.proc = self.thread = None
            self.take_id = None
        try:
            meta["samples"] = self._finish(self.dir(take), meta)
            self.log("scratch: take %s, %.1f s recorded" % (take, meta["samples"] / meta["rate"]))
        except Exception as e:  # noqa: BLE001
            self.error = "finishing the scratch files failed: %s" % e
            self.log("scratch: %s" % self.error)
        return meta

    def _finish(self, d, meta):
        n = finish_wav(os.path.join(d, "scratch.wav.part"), os.path.join(d, "scratch.wav"),
                       meta["rate"], meta["channels"])
        os.replace(os.path.join(d, "scratch.times.tsv.part"), os.path.join(d, "scratch.times.tsv"))
        meta["samples"] = n
        tmp = os.path.join(d, "scratch.json.tmp")
        with open(tmp, "w") as f:
            json.dump(meta, f, indent=1)
        os.replace(tmp, os.path.join(d, "scratch.json"))
        return n

    def recover(self):
        """Finish recordings left as .part by a hubd that died mid-take."""
        if not os.path.isdir(self.root):
            return
        for take in os.listdir(self.root):
            d = self.dir(take)
            if not os.path.exists(os.path.join(d, "scratch.wav.part")):
                continue
            meta = {"take_id": take, "rate": self.cfg["rate"], "channels": self.cfg["channels"],
                    "format": "S16_LE", "problems": ["hubd stopped during the take; recording ends early"]}
            try:
                if not os.path.exists(os.path.join(d, "scratch.times.tsv.part")):
                    open(os.path.join(d, "scratch.times.tsv.part"), "w").close()
                self._finish(d, meta)
                self.log("scratch: finished leftover recording of take %s" % take)
            except OSError as e:
                self.log("scratch: leftover recording of take %s: %s" % (take, e))
