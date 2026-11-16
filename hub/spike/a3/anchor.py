#!/usr/bin/env python3
"""Spike A3 (docs/phase4-plan.md): place a bounce in a take from claps alone, no hub mic.

Unlike hub/tools/clap_check.py (which searches +-2 min and needs 2+ claps), this
searches the whole take and must refuse rather than guess:

1. Audio: onset strength per video frame (1/30 s bins): how much louder each 1 ms
   window is than the 200 ms before it, kept only for sharp transients (claps, hits).
2. Video: per camera, motion bursts: frame-difference energy above its local median,
   scaled by the local spread, so a camera with a busy drummer doesn't dominate.
   All cameras share the take's clock, so their bursts are summed.
3. Correlate the two over every possible offset (FFT). The best offset must stand out
   from the second best by a margin, or the result is "needs sync".
4. Refine: pair each strong audio clap with the motion peak near it; the offset is
   the median of (video peak time - clap time), with each pair's spread reported.

    python3 anchor.py BOUNCE MOTION_FILE [MOTION_FILE ...] [--start S --length S]

MOTION_FILE: one value per frame from ffmpeg's tblend/signalstats YAVG (as clap_check).
--start/--length cut a piece out of the bounce, to test partial songs.
"""
import argparse
import subprocess
import sys

import numpy as np

FPS = 30.0
SR = 48000


def onset_envelope(path, start=0.0, length=None):
    cmd = ["ffmpeg", "-v", "error", "-ss", str(start)] + (["-t", str(length)] if length else []) + \
          ["-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    a = np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, dtype="<i2").astype(np.float32)
    w = SR // 1000
    env = np.abs(a[: len(a) // w * w]).reshape(-1, w).max(axis=1)            # 1 ms peak envelope
    pre = np.convolve(env, np.ones(180) / 180, mode="full")[: len(env)]       # mean of the 180 ms ...
    pre = np.concatenate([np.zeros(20), pre[:-20]])                           # ... ending 20 ms before
    sharp = env / (pre + 50.0)
    sharp[env < 4000] = 0                                                     # quiet clicks don't count
    return env, sharp


def audio_frames(sharp):
    """Per video frame: the strongest onset in it (log-scaled)."""
    per = int(1000 / FPS)                         # ~33 ms; exact frame edges below
    n = int(len(sharp) / 1000 * FPS)
    edges = (np.arange(n + 1) * 1000 / FPS).astype(int)
    out = np.array([sharp[edges[i]:edges[i + 1]].max() if edges[i + 1] > edges[i] else 0 for i in range(n)])
    del per
    return np.log1p(np.maximum(out - 3.0, 0))     # transients well above their surroundings only


def motion_bursts(motion, win=31):
    m = np.asarray(motion, dtype=np.float64)
    pad = np.pad(m, win // 2, mode="edge")
    view = np.lib.stride_tricks.sliding_window_view(pad, win)
    med = np.median(view, axis=1)
    mad = np.median(np.abs(view - med[:, None]), axis=1) + 0.05
    b = np.maximum((m - med) / mad, 0)
    # tblend gives n-1 values; value k is the change from frame k to k+1 -> frame k+1
    return np.log1p(np.concatenate([[0.0], b]))


def correlate(a, v):
    """score[o] for video frame = audio frame + o, for o from -(len(a)-1) to len(v)-1."""
    n = 1 << int(np.ceil(np.log2(len(a) + len(v))))
    c = np.fft.irfft(np.fft.rfft(v, n) * np.conj(np.fft.rfft(a, n)), n)
    return np.concatenate([c[-(len(a) - 1):], c[: len(v)]]), -(len(a) - 1)


def anchor(bounce_env_sharp, motions, min_ratio=1.5, min_claps=1):
    env, sharp = bounce_env_sharp
    a = audio_frames(sharp)
    v = sum(motion_bursts(m) for m in motions)
    score, lo = correlate(a, v)
    # Only offsets where at least half the audio lands on the video count.
    offs = np.arange(len(score)) + lo
    overlap = np.minimum(offs + len(a), len(v)) - np.maximum(offs, 0)
    score[overlap < len(a) / 2] = 0
    best = int(np.argmax(score))
    s2 = score.copy()
    s2[max(0, best - 5): best + 6] = 0
    second = float(s2.max())
    ratio = float(score[best]) / second if second > 0 else float("inf")
    off_frames = best + lo

    # Refine on individual claps: strong audio onsets with a clear motion peak nearby.
    claps = []
    order = np.argsort(-sharp)
    taken = []
    for i in order[:2000]:
        if sharp[i] < 8:
            break
        if any(abs(i - t) < 1500 for t in taken):
            continue
        taken.append(i)
    for i in sorted(taken):
        t = i / 1000.0                                            # onset ms -> s (peak ms; clap_check uses half-peak onset)
        j = i
        while j > i - 20 and env[j - 1] > env[i] / 2:
            j -= 1
        t = j / 1000.0
        f = int(round(t * FPS)) + off_frames
        if 3 <= f < len(v) - 3:
            k = max(range(f - 3, f + 4), key=lambda q: v[q])
            if v[k] > 2.0:
                claps.append((t, k, k / FPS - t))
    if not claps:
        return {"result": "needs sync", "why": "no clap with a matching motion peak", "ratio": ratio,
                "coarse_offset_s": off_frames / FPS}
    d = np.array([c[2] for c in claps])
    offset = float(np.median(d))
    ok = ratio >= min_ratio and len(claps) >= min_claps
    return {"result": "synced" if ok else "needs sync",
            "why": None if ok else "best match not clearly better than the next (ratio %.2f)" % ratio,
            "offset_s": round(offset, 3), "coarse_offset_s": round(off_frames / FPS, 3),
            "ratio": round(ratio, 2), "claps": len(claps),
            "spread_ms": round(float(d.max() - d.min()) * 1000), "pairs": [(round(t, 3), k) for t, k, _ in claps]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("bounce")
    ap.add_argument("motion", nargs="+")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--length", type=float, default=None)
    args = ap.parse_args(argv)
    motions = [[float(x) for x in open(p)] for p in args.motion]
    r = anchor(onset_envelope(args.bounce, args.start, args.length), motions)
    if "offset_s" in r:
        r["bounce_zero_at_s"] = round(r["offset_s"] - args.start, 3)   # where the full bounce's t=0 lands
    print(r)


if __name__ == "__main__":
    sys.exit(main())
