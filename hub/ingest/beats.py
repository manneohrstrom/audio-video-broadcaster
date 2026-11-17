"""Beats and bars of a bounce, for the automatic cut (docs/editor-plan.md section 3).

The studio's songs are played live, without a click, and much of it is jazz improvisation:
the tempo shifts quickly within a song, on purpose (user, 2026-11-17). Nothing here assumes
one fixed tempo:

  1. Onset strength: audiosync's spectral flux (three bands, 200 frames per second), of
     energy^0.3 rather than log energy, so a ghost note on the off-beat stays quieter than
     the hit on the beat (with log energy they look alike and 2/3 of the tempo fits too).
  2. Tempo, chunk by chunk: the onset strength's autocorrelation over 20 s of music, every
     2 s, so each stretch of a song gets its own tempo (user: "analyse it in 20 s chunks").
     The music often repeats as strongly at 74 as at 148 BPM; a prior around 95 BPM, half
     an octave wide, picks the level the band feels (tuned by ear on the user's tracks,
     2026-11-17: four passages, all right). A light Viterbi path through the chunks keeps
     one chunk from flipping to the other level on its own, but follows real changes.
  3. Beats: dynamic programming over the onset strength (after Ellis 2007, "Beat tracking
     by dynamic programming"), but over pairs of beats, so the tempo can float from beat to
     beat: each interval may differ a little from the one before (the band speeding up or
     settling back), while step 2's tempo only keeps the chain at the felt level.
  4. Bars: which beats start a bar. Scored per beat by the kick (the low band's onset) and
     by how much the sound changes there (chords tend to change on the one); the bar
     position then runs through the song as a small hidden Markov model, so a fill or an
     odd bar can shift the bar line without losing it. 4/4, or 3/4 when that scores clearly
     better.
  5. Confidence per beat: how clearly the music pulses around it (the autocorrelation peak
     of its chunk, against the song's clearest chunks), times how busy the music is within
     2 s of it. Rubato intros, breakdowns and rests score low; the cut rules don't trust
     beats there.

Result: beats.json next to the bounce, {"beats_s", "downbeats_s", "beats_per_bar",
"confidence" (per beat, 0..1), "hit" (per beat: how hard the music hits on it, 1 = one of
the song's strong beats), "tempo_bpm" (median), "tempo_curve" ([[s, bpm], ...] per chunk),
"duration_s", "analysed_with"}. Times are seconds from the bounce's first sample;
the sync puts them on the hub clock.

Needs numpy and ffmpeg. A 10-minute song takes a few seconds and stays small in memory
(audio decoded at 16 kHz, spectra made in 10 s pieces: under 100 MB in all, since the
hub may be rendering at the same time).

Run: python3 beats.py <bounce> [<beats.json>]
"""
import json
import os
import sys
import time

import numpy as np

import audiosync

VERSION = 1
SR = audiosync.SR                 # 16 kHz analysis audio
FPS = audiosync.ENV_FS            # onset frames per second (5 ms each)
PIECE_S = 10.0                    # spectra are made this many seconds at a time (memory)

DEFAULTS = {
    "onset_power": 0.3,           # onsets: flux of energy^0.3, so accents stay above ghost notes
                                  # (None: of log energy, as audiosync, which makes them alike)
    "window_s": 20.0,             # tempo chunks (user: 20-30 s)
    "window_step_s": 2.0,
    "min_bpm": 60.0,
    "max_bpm": 200.0,
    "prior_bpm": 95.0,            # tempo prior: centre ...
    "prior_octaves": 0.5,         # ... and width (standard deviation, in octaves)
    "double_weight": 0.0,         # extra credit for repeating at twice the period (see tempo_windows)
    "tempo_jump_cost": 3.0,       # Viterbi: cost per (log period change)^2, against chunk
                                  # evidence of at most 1: an octave jump needs ~1.5 chunks of
                                  # clear evidence, a 4:3 change a fraction of one
    "beat_change_cost": 100.0,    # beats: cost per (log change of interval from the last beat)^2:
                                  # 3 % ~0.1, 10 % ~1, 20 % ~3 (an onset scores ~1-5)
    "tempo_pull": 20.0,           # beats: cost per (log interval / chunk tempo)^2, keeps the level
    "beat_interval_range": [0.6, 1.6],   # beats: intervals allowed, as a share of the chunk's period
    "bar_shift_cost": 4.0,        # bar HMM: cost of a bar line moving (an odd bar, a fill)
    "three_four_margin": 1.3,     # 3/4 only if it scores this many times better than 4/4
}


# ------------------------------------------------------------------ tempo

def tempo_prior(lags, cfg):
    """Weight per lag (in onset frames): a log-normal bump around prior_bpm."""
    bpm = 60.0 * FPS / lags
    return np.exp(-0.5 * (np.log2(bpm / cfg["prior_bpm"]) / cfg["prior_octaves"]) ** 2)


def tempo_windows(env, cfg):
    """(centres_s, lags, salience): per window, how strongly the onset strength repeats at
    each lag. salience has shape (windows, lags); each row is 0..1 (1 = a perfect pulse)."""
    width = int(cfg["window_s"] * FPS)
    step = int(cfg["window_step_s"] * FPS)
    lags = np.arange(int(round(60.0 * FPS / cfg["max_bpm"])), int(round(60.0 * FPS / cfg["min_bpm"])) + 1)
    prior = tempo_prior(lags, cfg)
    fft_size = 1 << int(np.ceil(np.log2(2 * width)))
    starts = list(range(0, max(1, len(env) - width + 1), step))
    salience = np.zeros((len(starts), len(lags)), np.float32)
    for row, start in enumerate(starts):
        piece = env[start:start + width]
        piece = piece - piece.mean()
        energy = float(np.dot(piece, piece))
        if energy <= 0:
            continue
        spectrum = np.fft.rfft(piece, fft_size)
        acf = np.fft.irfft(spectrum * np.conj(spectrum), fft_size)[:width] / energy
        # double_weight > 0 also rewards repeating at twice the period, which favours the
        # faster of two readings; music that repeats as strongly at 74 as at 148 BPM is then
        # left to the prior (the felt beat in jazz tends to be the slower one: user, 2026-11-17).
        double = np.interp(2 * lags, np.arange(width), acf, right=0.0)
        weight = cfg["double_weight"]
        strength = np.maximum(0.0, acf[lags] + weight * double) / (1.0 + weight)
        salience[row] = strength * prior
    centres = (np.array(starts) + width / 2) / FPS
    return centres, lags, salience


def tempo_path(lags, salience, cfg):
    """Viterbi through the windows: one lag per window, rewarding salience, charging for
    changes in tempo (in log period, so drifting 1 % costs 1/10 000 of an octave jump's cost).
    Each window's salience is scaled to a peak of 1 first: a quiet window's evidence counts
    as much as a loud one's for *which* tempo, just as the cost of changing it does."""
    peaks = salience.max(axis=1, keepdims=True)
    salience = np.divide(salience, peaks, out=np.zeros_like(salience), where=peaks > 0)
    count = len(salience)
    log_lags = np.log(lags.astype(float))
    jump = cfg["tempo_jump_cost"] * (log_lags[:, None] - log_lags[None, :]) ** 2   # [from, to]
    score = salience[0].astype(float)
    back = np.zeros((count, len(lags)), np.int32)
    for row in range(1, count):
        total = score[:, None] - jump
        back[row] = np.argmax(total, axis=0)
        score = total[back[row], np.arange(len(lags))] + salience[row]
    path = np.zeros(count, np.int32)
    path[-1] = int(np.argmax(score))
    for row in range(count - 1, 0, -1):
        path[row - 1] = back[row, path[row]]
    return path


def refine_lag(row_salience, index, lags):
    """A lag between whole frames: the parabola through the chosen bin and its neighbours."""
    if 0 < index < len(lags) - 1:
        left, mid, right = row_salience[index - 1:index + 2]
        bend = left - 2 * mid + right
        if bend < 0:
            return lags[index] + 0.5 * (left - right) / bend
    return float(lags[index])


def tempo_curve(env, cfg):
    """(period per onset frame, curve [[s, bpm]], confidence per window, window centres)."""
    centres, lags, salience = tempo_windows(env, cfg)
    path = tempo_path(lags, salience, cfg)
    periods = np.array([refine_lag(salience[row], path[row], lags) for row in range(len(path))])
    # A lone chunk can still pick a neighbouring bin that fits worse; a median over 5 steps doesn't care.
    if len(periods) >= 5:
        padded = np.pad(periods, 2, mode="edge")
        periods = np.median(np.lib.stride_tricks.sliding_window_view(padded, 5), axis=1)
    # Confidence: how clearly this window pulses at the chosen tempo, against the clearest
    # windows of the same song (its 90th percentile), so it says where to trust the beats.
    pulse = salience[np.arange(len(path)), path] / tempo_prior(lags[path], cfg)
    reference = float(np.percentile(pulse, 90)) if len(pulse) else 0.0
    confidence = pulse / reference if reference > 0 else pulse * 0.0
    per_frame = np.interp(np.arange(len(env)) / FPS, centres, periods)
    curve = [[round(float(c), 2), round(60.0 * FPS / p, 2)] for c, p in zip(centres, periods)]
    return per_frame, curve, np.clip(confidence, 0.0, 1.0), centres


# ------------------------------------------------------------------ beats

def smooth(env, sigma_frames):
    """A Gaussian blur, so a beat a few ms off its onset still collects it."""
    radius = max(1, int(3 * sigma_frames))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma_frames) ** 2)
    return np.convolve(env, kernel / kernel.sum(), "same")


def beat_candidates(strength, grid_frames=8, peak_floor=0.1):
    """Frames a beat may sit on: every onset peak, plus a coarse grid (every 40 ms) so a beat
    can also fall in a rest, where the band keeps time without playing."""
    middle = strength[1:-1]
    peaks = np.flatnonzero((middle > strength[:-2]) & (middle >= strength[2:]) & (middle > peak_floor)) + 1
    grid = np.arange(0, len(strength), grid_frames)
    return np.union1d(peaks, grid)


def track_beats(env, period, cfg):
    """Beat frames: the chain of onsets that best follows the music while its tempo floats.

    Dynamic programming over pairs (this beat, the beat before), so each beat knows the
    interval it came in on: changing the interval a little from one beat to the next is
    cheap, changing it a lot is expensive (beat_change_cost), and every interval is pulled
    gently towards the chunk tempo (tempo_pull), which only keeps the chain at the right
    level (74 or 148 BPM), not at one fixed speed. period: frames per beat, per onset frame."""
    strength = smooth(env, max(1.0, float(np.median(period)) / 32))
    strength = strength / (strength.std() or 1.0)
    frames = beat_candidates(strength)
    at = frames.astype(float)
    count = len(frames)
    expected = period[frames]
    gain = strength[frames]
    change_cost, pull = cfg["beat_change_cost"], cfg["tempo_pull"]
    shortest, longest = cfg["beat_interval_range"]
    # The beats that may come before each candidate: an interval within the allowed range.
    first = np.searchsorted(at, at - longest * expected, "left")
    last = np.searchsorted(at, at - shortest * expected, "right")
    width = max(1, int((last - first).max()))
    before = np.full((count, width), -1, np.int64)
    for i in range(count):
        span = np.arange(first[i], last[i])
        before[i, :len(span)] = span
    score = np.full((count, width), -np.inf)       # score[i, w]: beat i, previous beat before[i, w]
    came_from = np.full((count, width), -1, np.int64)   # w of the previous beat's own previous, -1 = chain start
    for i in range(count):
        slots = np.flatnonzero(before[i] >= 0)
        if not len(slots):
            continue
        previous = before[i, slots]
        interval = at[i] - at[previous]
        keep_tempo = -pull * np.log(interval / expected[i]) ** 2
        # Either the previous beat started the chain, or it continues its own best chain.
        start_value = gain[previous]
        older = before[previous]                                   # (n, width)
        older_interval = at[previous][:, None] - at[np.maximum(older, 0)]
        with np.errstate(divide="ignore", invalid="ignore"):
            change = np.log(interval[:, None] / older_interval) ** 2
        carried = np.where(older >= 0, score[previous] - change_cost * change, -np.inf)
        best_older = np.argmax(carried, axis=1)
        carried_value = carried[np.arange(len(slots)), best_older]
        continues = carried_value > start_value
        score[i, slots] = gain[i] + keep_tempo + np.where(continues, carried_value, start_value)
        came_from[i, slots] = np.where(continues, best_older, -1)
    # The last beat: the best chain ending within the final beat period.
    tail = np.flatnonzero(at >= len(strength) - period[-1] - 1)
    tail = tail[np.isfinite(score[tail]).any(axis=1)] if len(tail) else tail
    if not len(tail):
        return frames[:0]
    flat = int(np.argmax(score[tail]))
    beat, slot = int(tail[flat // width]), flat % width
    beats = [beat]
    while slot >= 0:
        previous = int(before[beat, slot])
        beats.append(previous)
        beat, slot = previous, int(came_from[beat, slot])
    return frames[np.array(beats[::-1], np.int64)]


# ------------------------------------------------------------------ bars

def band_spectra(x, bands=24, low_hz=60.0, high_hz=5000.0, fps=100, piece_s=PIECE_S):
    """Log energy in `bands` log-spaced bands, `fps` frames per second: the sound's colour
    over time, coarse enough to see chord and section changes. Shape (frames, bands)."""
    window = 1024
    hop = SR // fps
    edges = np.geomspace(low_hz, high_hz, bands + 1)
    freqs = np.fft.rfftfreq(window, 1 / SR)
    which = np.digitize(freqs, edges) - 1                     # band per FFT bin, -1/bands = outside
    taper = np.hanning(window).astype(np.float32)
    frame_count = max(0, (len(x) - window) // hop + 1)
    out = np.zeros((frame_count, bands), np.float32)
    per_piece = int(piece_s * fps)
    for p0 in range(0, frame_count, per_piece):
        p1 = min(frame_count, p0 + per_piece)
        index = (np.arange(p0, p1) * hop)[:, None] + np.arange(window)[None, :]
        power = np.abs(np.fft.rfft(x[index] * taper, axis=1)) ** 2
        for b in range(bands):
            out[p0:p1, b] = np.log1p(1e4 * power[:, which == b].sum(axis=1))
    return out


def zscore(values):
    spread = values.std()
    return (values - values.mean()) / spread if spread > 0 else values * 0.0


def downbeat_evidence(beat_frames, low_onsets, spectra, spectra_fps=100):
    """Per beat, how much it sounds like the one: the kick (low band onset just around the
    beat) plus the change in the sound's colour from the two beats before to the two after."""
    kick = np.array([low_onsets[max(0, b - 4):b + 5].max(initial=0.0) for b in beat_frames])
    at = np.minimum((beat_frames * spectra_fps) // FPS, max(0, len(spectra) - 1))
    segments = np.array([spectra[a:max(a + 1, b)].mean(axis=0) for a, b in zip(at[:-1], at[1:])]
                        + [spectra[at[-1]:at[-1] + 1].mean(axis=0)])
    segments = segments - segments.mean(axis=1, keepdims=True)
    change = np.zeros(len(beat_frames))
    for i in range(2, len(beat_frames) - 2):
        before, after = segments[i - 2:i].mean(axis=0), segments[i:i + 2].mean(axis=0)
        norm = np.linalg.norm(before) * np.linalg.norm(after)
        change[i] = 1.0 - float(np.dot(before, after) / norm) if norm > 0 else 0.0
    return zscore(kick) + zscore(change)


def bar_positions(evidence, beats_per_bar, shift_cost):
    """(position in the bar per beat, score): a small HMM. Each beat moves the position on by
    one; moving it any other way (an odd bar) costs shift_cost; position 0 earns the beat's
    evidence."""
    count, size = len(evidence), beats_per_bar
    advance = (np.arange(size) + 1) % size
    score = np.where(np.arange(size) == 0, evidence[0], 0.0)
    back = np.zeros((count, size), np.int64)
    for i in range(1, count):
        total = np.full((size, size), -shift_cost) + score[:, None]       # [from, to]
        total[np.arange(size), advance] += shift_cost
        back[i] = np.argmax(total, axis=0)
        score = total[back[i], np.arange(size)] + np.where(np.arange(size) == 0, evidence[i], 0.0)
    positions = np.zeros(count, np.int64)
    positions[-1] = int(np.argmax(score))
    for i in range(count - 1, 0, -1):
        positions[i - 1] = back[i, positions[i]]
    return positions, float(score.max())


def find_bars(evidence, cfg):
    """(positions, beats_per_bar): 4/4 unless 3/4 explains the evidence clearly better."""
    if len(evidence) < 8:
        return np.arange(len(evidence)) % 4, 4
    results = {}
    for size in (3, 4):
        positions, _ = bar_positions(evidence, size, cfg["bar_shift_cost"])
        ones = evidence[positions == 0]
        results[size] = (positions, float(ones.mean() - evidence.mean()) if len(ones) else 0.0)
    if results[3][1] > cfg["three_four_margin"] * max(results[4][1], 1e-9):
        return results[3][0], 3
    return results[4][0], 4


# ------------------------------------------------------------------ all together

def activity(env, beat_frames, reach_s=2.0):
    """Per beat, 0..1: how busy the music is within reach_s of it, against the song's median
    beat (1 = at least as busy). A 20 s tempo chunk can't see a short rest; this can."""
    reach = int(reach_s * FPS)
    cumulative = np.concatenate([[0.0], np.cumsum(env, dtype=float)])
    lo, hi = np.maximum(0, beat_frames - reach), np.minimum(len(env), beat_frames + reach)
    busy = (cumulative[hi] - cumulative[lo]) / np.maximum(1, hi - lo)
    typical = float(np.median(busy)) if len(busy) else 0.0
    return np.clip(busy / typical, 0.0, 1.0) if typical > 0 else busy * 0.0


def hit_strength(env, beat_frames, reach_frames=5):
    """Per beat, how hard the music hits on it: the strongest onset within 25 ms, against the
    song's own strong beats (its 90th percentile = 1). A cut on a beat with a real hit looks
    musical even where the beat level is uncertain (a beat at 148 where the band feels 74)."""
    peaks = np.array([env[max(0, f - reach_frames):f + reach_frames + 1].max(initial=0.0) for f in beat_frames])
    reference = float(np.percentile(peaks, 90)) if len(peaks) else 0.0
    return np.clip(peaks / reference, 0.0, 2.0) if reference > 0 else peaks * 0.0


def analyse(x, cfg=None):
    """Beats and bars of mono 16 kHz audio x: the beats.json document (without file fields)."""
    cfg = dict(DEFAULTS, **(cfg or {}))
    duration_s = len(x) / SR
    if duration_s < cfg["window_s"] * 2:
        raise ValueError("too short to find a tempo (%.1f s)" % duration_s)
    onsets = audiosync.band_onsets(x, piece_s=PIECE_S, power=cfg["onset_power"])
    env = onsets.sum(axis=0)
    period, curve, window_confidence, centres = tempo_curve(env, cfg)
    beat_frames = track_beats(env, period, cfg)
    evidence = downbeat_evidence(beat_frames, onsets[0], band_spectra(x))
    positions, beats_per_bar = find_bars(evidence, cfg)
    beats_s = beat_frames / FPS
    confidence = np.interp(beats_s, centres, window_confidence) * activity(env, beat_frames)
    hits = hit_strength(env, beat_frames)
    return {
        "version": VERSION,
        "duration_s": round(duration_s, 3),
        "tempo_bpm": round(float(np.median([bpm for _, bpm in curve])), 2),
        "beats_per_bar": beats_per_bar,
        "beats_s": [round(float(t), 3) for t in beats_s],
        "downbeats_s": [round(float(t), 3) for t, p in zip(beats_s, positions) if p == 0],
        "confidence": [round(float(c), 3) for c in confidence],
        "hit": [round(float(h), 3) for h in hits],
        "tempo_curve": curve,
        "analysed_with": {key: cfg[key] for key in sorted(cfg)},
    }


def analyse_file(path, out_path=None, cfg=None, ffmpeg="ffmpeg"):
    """Analyse an audio file; with out_path, write the result there (via a temporary name)."""
    started = time.monotonic()
    result = analyse(audiosync.decode(path, ffmpeg=ffmpeg), cfg)
    result["source"] = os.path.basename(path)
    result["analysis_s"] = round(time.monotonic() - started, 2)
    if out_path:
        audiosync.write_json(out_path, result)
    return result


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__.strip().splitlines()[-1])
    document = analyse_file(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
    print(json.dumps({key: document[key] for key in ("source", "duration_s", "tempo_bpm", "beats_per_bar",
                                                     "analysis_s")}))
    print("beats %d, bars %d" % (len(document["beats_s"]), len(document["downbeats_s"])))
