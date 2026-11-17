"""Beat finder (beats.py) on synthetic music with a known answer.

Real checks were by ear on the user's own studio tracks (2026-11-17, docs/progress.md):
these tests only pin down the behaviour that must not break: following a tempo change,
following a floating tempo, finding the bar line, staying calm through a rest."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import beats  # noqa: E402

SR = beats.SR


def hit(rng, seconds, low_hz, high_hz, level):
    """A short decaying noise burst limited to low..high Hz (a drum-ish hit)."""
    n = int(seconds * SR)
    x = rng.standard_normal(n) * np.exp(-np.arange(n) / (n / 6))
    spectrum = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(n, 1 / SR)
    spectrum[(freqs < low_hz) | (freqs > high_hz)] = 0
    return level * np.fft.irfft(spectrum, n)


def band(beat_times, duration_s, seed=1, beats_per_bar=4, chords_every_bar=True):
    """Music on the given beat times: kick on the one (and louder), hi-hat on every beat,
    a quiet off-beat hat, a chord that changes on every bar line, a little noise."""
    rng = np.random.default_rng(seed)
    x = 0.002 * rng.standard_normal(int(duration_s * SR))
    kick, hat, ghost = hit(rng, 0.12, 40, 200, 1.0), hit(rng, 0.05, 4000, 7000, 0.25), hit(rng, 0.04, 4000, 7000, 0.06)
    chord_freqs = [(220, 277, 330), (196, 247, 294), (175, 220, 262), (247, 311, 370)]
    for i, t in enumerate(beat_times):
        at = int(t * SR)
        for sound in ([kick, hat] if i % beats_per_bar == 0 else [hat]):
            end = min(len(x), at + len(sound))
            x[at:end] += sound[:end - at]
        if i + 1 < len(beat_times):
            off = int((t + beat_times[i + 1]) / 2 * SR)
            end = min(len(x), off + len(ghost))
            x[off:end] += ghost[:end - off]
        if chords_every_bar and i % beats_per_bar == 0 and i + beats_per_bar < len(beat_times):
            stop = int(beat_times[i + beats_per_bar] * SR)
            t_axis = np.arange(stop - at) / SR
            notes = chord_freqs[(i // beats_per_bar) % len(chord_freqs)]
            x[at:stop] += sum(0.03 * np.sin(2 * np.pi * f * t_axis) for f in notes)
    return x.astype(np.float32)


def steady(bpm, start_s, end_s):
    return list(np.arange(start_s, end_s, 60.0 / bpm))


def matched(found, truth, tolerance_s=0.04):
    """Share of true beats with a found beat within tolerance."""
    found = np.asarray(found)
    return float(np.mean([np.min(np.abs(found - t)) <= tolerance_s for t in truth]))


class TempoChange(unittest.TestCase):
    """100 BPM for 40 s, then 130 BPM: each stretch gets its own tempo and the beats follow."""

    @classmethod
    def setUpClass(cls):
        first = steady(100, 1.0, 41.0)
        second = steady(130, first[-1] + 60.0 / 130, 81.0)
        cls.truth = first + second
        cls.result = beats.analyse(band(cls.truth, 82.0))

    def tempo_at(self, t):
        curve = np.array(self.result["tempo_curve"])
        return curve[np.argmin(np.abs(curve[:, 0] - t)), 1]

    def test_each_stretch_has_its_tempo(self):
        self.assertAlmostEqual(self.tempo_at(20.0), 100.0, delta=3.0)
        self.assertAlmostEqual(self.tempo_at(62.0), 130.0, delta=4.0)

    def test_beats_follow_the_change(self):
        inner = [t for t in self.truth if 3.0 < t < 79.0]
        self.assertGreater(matched(self.result["beats_s"], inner), 0.9)

    def test_bar_lines_on_the_one(self):
        ones = [t for i, t in enumerate(self.truth) if i % 4 == 0 and 5.0 < t < 77.0]
        self.assertEqual(self.result["beats_per_bar"], 4)
        self.assertGreater(matched(self.result["downbeats_s"], ones), 0.75)

    def test_result_fields(self):
        r = self.result
        self.assertEqual(len(r["beats_s"]), len(r["confidence"]))
        self.assertEqual(len(r["beats_s"]), len(r["hit"]))
        self.assertEqual(r["beats_s"], sorted(r["beats_s"]))
        self.assertTrue(set(r["downbeats_s"]) <= set(r["beats_s"]))


class FloatingTempo(unittest.TestCase):
    """The band drifts between ~92 and ~104 BPM and back within half a minute, as in a jam
    (user, 2026-11-17: the tempo floats on purpose). The beats must stay on it."""

    def test_beats_stay_on_a_floating_tempo(self):
        times, t = [], 1.0
        while t < 61.0:
            bpm = 98.0 + 6.0 * np.sin(2 * np.pi * t / 25.0)
            times.append(t)
            t += 60.0 / bpm
        result = beats.analyse(band(times, 62.0, seed=2))
        inner = [s for s in times if 3.0 < s < 59.0]
        self.assertGreater(matched(result["beats_s"], inner), 0.9)


class Rest(unittest.TestCase):
    """20 s of music, 12 s of near silence, 20 s of music: the beats in the rest have low
    confidence, so the cut won't trust them."""

    def test_low_confidence_in_the_rest(self):
        music = steady(110, 1.0, 21.0) + steady(110, 33.0, 53.0)
        result = beats.analyse(band(music, 54.0, seed=3))
        beats_s, confidence = np.array(result["beats_s"]), np.array(result["confidence"])
        in_rest = confidence[(beats_s > 25.0) & (beats_s < 29.0)]
        in_music = confidence[(beats_s > 8.0) & (beats_s < 15.0)]
        self.assertGreater(len(in_music), 0)
        if len(in_rest):
            self.assertLess(in_rest.mean(), 0.6 * in_music.mean())


class TooShort(unittest.TestCase):
    def test_refuses_a_snippet(self):
        with self.assertRaises(ValueError):
            beats.analyse(np.zeros(SR * 5, np.float32))


if __name__ == "__main__":
    unittest.main()
