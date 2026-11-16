"""Automatic sync on synthetic takes, harder than the real test take of 2026-11-16 on
purpose (user: real sessions are fuller, the room louder, the balance different)."""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import audiosync as A  # noqa: E402

SR = A.SR
T0_NS = 1_794_855_000_000_000_000


def burst(rng, n, lo, hi):
    """A decaying noise burst band-limited to lo..hi Hz (a drum-ish hit)."""
    x = rng.standard_normal(n) * np.exp(-np.arange(n) / (n / 6))
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    X[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(X, n)


def music(seed, seconds, density=8.0, loop_s=None):
    """Dense 'music': random hits in three bands plus held chords. loop_s: repeat the
    first loop_s seconds exactly (a looped part, the hard case)."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    part_a, part_b = np.zeros(n), np.zeros(n)     # a: acoustic (room hears it), b: DI (it doesn't)
    t = 0.0
    while t < seconds - 0.3:
        t += rng.exponential(1 / density)
        i = int(t * SR)
        lo, hi = [(60, 300), (300, 2500), (2500, 7000)][rng.integers(3)]
        b = burst(rng, int(0.15 * SR), lo, hi) * rng.uniform(0.2, 1.0)
        target = part_b if rng.random() < 0.35 else part_a
        target[i:i + len(b)] += b[:max(0, n - i)]
    tt = np.arange(n) / SR
    for k, f0 in enumerate((110, 220, 330, 440)):
        part_b += 0.05 * np.sin(2 * np.pi * f0 * (1 + 0.5 * (int(tt[-1]) % 3)) * tt + k)
    if loop_s:
        L = int(loop_s * SR)
        part_a = np.tile(part_a[:L], n // L + 1)[:n]
        part_b = np.tile(part_b[:L], n // L + 1)[:n]
    return part_a.astype(np.float32), part_b.astype(np.float32)


def room(acoustic, rng, take_s, start_s, ppm=-1920.0, noise=0.5, reverb_s=0.4):
    """What the hub mic hears: the acoustic part only, with reverb, from start_s into the
    take, plus loud room noise; recorded on a clock running ppm off (A2: the mic is slow)."""
    ir = rng.standard_normal(int(reverb_s * SR)) * np.exp(-np.arange(int(reverb_s * SR)) / (reverb_s * SR / 5))
    ir[0] = 4.0
    wet = np.convolve(acoustic, ir / np.abs(ir).sum() * 3)[:len(acoustic)]
    hub = np.zeros(int(take_s * SR))
    i = int(start_s * SR)
    seg = wet[:max(0, len(hub) - i)]
    hub[i:i + len(seg)] += seg
    hub += noise * np.std(seg) * rng.standard_normal(len(hub))          # talking, amp hum, room
    # the mic's clock: its sample k was taken at hub time k / (SR * (1 + ppm/1e6))
    rate = 1 + ppm / 1e6
    k = np.arange(int(len(hub) * rate))
    mic = np.interp(k / rate, np.arange(len(hub)), hub).astype(np.float32)
    return mic, rate


def tsv(path, n_mic16, rate, jitter_ms=0.5, seed=1):
    """scratch.times.tsv: one line per 100 ms block at 48 kHz: first sample, hub ns read."""
    rng = np.random.default_rng(seed)
    with open(path, "w") as f:
        for n48 in range(0, n_mic16 * 3, 4800):
            hub_s = (n48 + 4800) / 48000 / rate + abs(rng.normal(0, jitter_ms / 1000))
            f.write("%d\t%d\n" % (n48, T0_NS + int(hub_s * 1e9)))


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rng = np.random.default_rng(7)
        self.cfg = {"mic_latency_s": 0.0}

    def take(self, bounce_parts, take_s, start_s, **kw):
        mic, rate = room(bounce_parts[0], self.rng, take_s, start_s, **kw)
        p = os.path.join(self.d, "t.tsv")
        tsv(p, len(mic), rate)
        return mic, p

    def assertNear(self, res, start_s, ms=10):
        got = (res["bounce_start_ns"] - T0_NS) / 1e9
        self.assertLess(abs(got - start_s) * 1000, ms, "off by %.1f ms" % ((got - start_s) * 1000))

    def test_dense_music_loud_room_missing_parts(self):
        a, b = music(1, 90)
        mic, p = self.take((a, b), take_s=130, start_s=25.0, noise=0.8)
        res = A.match(mic, p, a + b, self.cfg)
        self.assertNear(res, 25.0)
        self.assertIn(res["confidence"], ("high", "medium"))

    def test_mic_drift_is_taken_out(self):
        a, b = music(2, 120)
        mic, p = self.take((a, b), take_s=140, start_s=10.0, ppm=-1920)
        res = A.match(mic, p, a + b, self.cfg)
        self.assertNear(res, 10.0)
        self.assertLess(abs(res["drift_ppm"]), 50)      # the mic's 1920 ppm is gone

    def test_bounce_longer_than_the_take(self):
        a, b = music(3, 150)                               # Logic ran long before REC
        mic, p = self.take((a[60 * SR:], b[60 * SR:]), take_s=95, start_s=2.0)
        res = A.match(mic, p, a + b, self.cfg)
        self.assertNear(res, 2.0 - 60.0)                   # bounce t=0 is a minute before the mic

    def test_unrelated_bounce_needs_sync(self):
        a, b = music(4, 80)
        mic, p = self.take((a, b), take_s=100, start_s=10.0)
        other_a, other_b = music(5, 80)
        with self.assertRaises(A.SyncFailed):
            A.match(mic, p, other_a + other_b, self.cfg)

    def test_exact_loop_is_not_guessed(self):
        a, b = music(6, 80, loop_s=4.0)                    # the same 4 s over and over
        mic, p = self.take((a, b), take_s=100, start_s=10.0)
        try:
            res = A.match(mic, p, a + b, self.cfg)
        except A.SyncFailed:
            return                                          # "needs sync": fine
        self.assertNear(res, 10.0, ms=20)                  # or right: never a wrong guess

    def test_silent_mic_needs_sync(self):
        a, b = music(8, 60)
        mic = np.zeros(80 * SR, np.float32)
        p = os.path.join(self.d, "t.tsv")
        tsv(p, len(mic), 1.0)
        with self.assertRaises(A.SyncFailed):
            A.match(mic, p, a + b, self.cfg)

    def test_latency_is_subtracted(self):
        a, b = music(9, 60)
        mic, p = self.take((a, b), take_s=80, start_s=10.0)
        res = A.match(mic, p, a + b, {"mic_latency_s": 0.074})
        self.assertNear(res, 10.0 - 0.074)


class ClapTest(unittest.TestCase):
    def test_finds_claps(self):
        x = np.zeros(10 * SR, np.float32)
        rng = np.random.default_rng(3)
        for t in (2.0, 3.1, 4.05, 7.5):
            i = int(t * SR)
            x[i:i + 800] += (rng.standard_normal(800) * np.exp(-np.arange(800) / 100)).astype(np.float32) * 0.6
        got = A.claps(x)
        self.assertEqual(len(got), 4)
        for g, t in zip(got, (2.0, 3.1, 4.05, 7.5)):
            self.assertLess(abs(g - t), 0.003)


if __name__ == "__main__":
    unittest.main()
