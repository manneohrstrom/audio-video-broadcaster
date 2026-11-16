#!/usr/bin/env python3
"""Spike A1: where does a bounce sit on the hub clock? Claps vs. music only.

    python3 match.py scratch.wav scratch.times.tsv bounce.wav [--fs 8000] [--win 8]

The hub mic's own clock is slow (~1900 ppm, spike A2), so its samples are first put on
the hub clock with a straight-line fit of the per-block read times (scratch.times.tsv).
Then the bounce's t=0 is found on the hub clock two ways:
  1. claps: sharp onsets in both, paired by order;
  2. music only: GCC-PHAT cross-correlation of the bounce (clap regions cut out) against
     the mic, over the whole piece and in short windows (consistency, drift, confidence).
Output: hub-clock seconds since the mic's first block. Needs numpy and ffmpeg.
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
import clap_check  # noqa: E402

SR = 48000


def load(path):
    import subprocess
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768


def decimate(x, k):
    """Low-pass by FFT (cut at 0.45 of the new Nyquist... close enough) and keep every k-th sample."""
    n = len(x)
    X = np.fft.rfft(x)
    X[int(len(X) / k * 0.9):] = 0
    return np.fft.irfft(X, n)[::k]


def mic_fit(tsv_path, block=4800):
    t = np.loadtxt(tsv_path, dtype=np.int64)[:-1]          # the last block is short
    n, ns = t[:, 0].astype(float), t[:, 1]
    end = (n + block) / SR
    rel = (ns - ns[0]).astype(float) / 1e9
    b, a = np.polyfit(end, rel, 1)
    return ns[0], a, b                                     # hub_rel = a + b * mic_seconds


def gcc_phat(sig, ref, fs, max_lag_s=None):
    """Lag (s) of ref inside sig, and peak strength over the next best peak."""
    n = 1 << int(np.ceil(np.log2(len(sig) + len(ref))))
    S, R = np.fft.rfft(sig, n), np.fft.rfft(ref, n)
    G = S * np.conj(R)
    G /= np.abs(G) + 1e-9
    cc = np.fft.irfft(G, n)
    cc = np.concatenate([cc[-(len(ref) - 1):], cc[:len(sig)]])   # lag -(len(ref)-1) .. len(sig)-1
    lags = np.arange(-(len(ref) - 1), len(sig))
    i = int(np.argmax(cc))
    # sub-sample peak by parabola
    if 0 < i < len(cc) - 1:
        y0, y1, y2 = cc[i - 1], cc[i], cc[i + 1]
        frac = 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2 + 1e-12)
    else:
        frac = 0.0
    guard = int(0.05 * fs)
    rest = np.concatenate([cc[:max(0, i - guard)], cc[i + guard:]])
    second = rest.max() if len(rest) else 1e-9
    return (lags[i] + frac) / fs, float(cc[i] / max(second, 1e-9))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mic")
    ap.add_argument("tsv")
    ap.add_argument("bounce")
    ap.add_argument("--fs", type=int, default=8000)
    ap.add_argument("--win", type=float, default=8.0)
    o = ap.parse_args()
    k = SR // o.fs

    t0_ns, a, b = mic_fit(o.tsv)
    print("mic clock: %.1f ppm vs hub (hub = %.4f + %.8f x mic s)" % ((b - 1) * 1e6, a, b))
    mic, bnc = load(o.mic), load(o.bounce)

    # --- claps
    mc = clap_check.audio_claps(o.mic, min_gap_s=0.3)
    bc = clap_check.audio_claps(o.bounce, min_gap_s=0.3)
    mc_hub = [a + b * c for c in mc]
    print("claps: mic %s" % [round(x, 3) for x in mc])
    print("       bounce %s" % [round(x, 3) for x in bc])
    # pair by the offset most claps agree on (within 50 ms)
    best = None
    for x in mc_hub:
        for y in bc:
            off = x - y
            pairs = [(m, bb) for m in mc_hub for bb in bc if abs(m - bb - off) < 0.05]
            if best is None or len(pairs) > len(best[1]):
                best = (off, pairs)
    clap_offs = [m - bb for m, bb in best[1]]
    print("clap offsets (hub time of bounce t=0, s): %s" % ", ".join("%.4f" % x for x in clap_offs))

    # --- mic on the hub clock at fs
    m8 = decimate(mic, k)
    dur_hub = a + b * len(mic) / SR
    g = np.arange(0, dur_hub, 1 / o.fs)
    src = (g - a) / b * o.fs                              # mic-sample index (at fs) for each hub-grid time
    mh = np.interp(src, np.arange(len(m8)), m8, left=0, right=0)
    b8 = decimate(bnc, k)

    # --- music only: cut ±1.5 s around every bounce clap
    mask = np.ones(len(b8), bool)
    for c in bc:
        mask[max(0, int((c - 1.5) * o.fs)):int((c + 1.5) * o.fs)] = False
    bm = np.where(mask, b8, 0)
    # only the part of the bounce that the mic can have heard (by the clap estimate, if any)
    off_c = float(np.median(clap_offs)) if clap_offs else None
    lag, conf = gcc_phat(mh, bm, o.fs)
    print("music only, whole bounce: bounce t=0 at hub %.4f s (peak %.1fx the next best)" % (lag, conf))
    if off_c is not None:
        print("   vs claps: %+.1f ms" % ((lag - off_c) * 1000))

    # --- music only, short windows inside the overlap and away from claps
    rows = []
    lo = max(0.0, -lag) if lag < 0 else 0.0
    t = 0.0
    while t + o.win <= len(b8) / o.fs:
        a0, a1 = int(t * o.fs), int((t + o.win) * o.fs)
        hub0 = lag + t
        if hub0 > 0.5 and hub0 + o.win < dur_hub - 0.5 and mask[a0:a1].all():
            seg = b8[a0:a1]
            if np.sqrt(np.mean(seg ** 2)) > 1e-3:
                l2, c2 = gcc_phat(mh, seg, o.fs)
                rows.append((t, l2 - t, c2))
        t += o.win / 2
    print("windows of %.0f s (bounce time, offset, vs whole, confidence):" % o.win)
    for t, off, c in rows:
        print("   %6.1f s  %.4f  %+7.1f ms  %5.1fx%s" % (t, off, (off - lag) * 1000, c, "" if abs(off - lag) < 0.02 else "  <- off"))
    good = [(t, off) for t, off, c in rows if abs(off - lag) < 0.02]
    if len(good) >= 3:
        tt, oo = np.array(good).T
        p = np.polyfit(tt, oo, 1)
        print("bounce clock vs hub (from windows): %.1f ppm; window scatter %.2f ms rms"
              % (p[0] * 1e6, np.std(oo - np.polyval(p, tt)) * 1000))
    print("hub ns of bounce t=0: %d (music) / %s (claps)" % (t0_ns + int(lag * 1e9),
          t0_ns + int(off_c * 1e9) if off_c is not None else "-"))


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------- method 2: onset envelopes
def onset_env(x, fs_env=200, bands=((80, 300), (300, 2000), (2000, 8000))):
    """Spectral-flux onset strength per band at fs_env frames/s (hop 5 ms, 23 ms windows), summed.
    Robust to the mic's colouring and level: log magnitude, positive differences only."""
    hop, nwin = SR // fs_env, 1024
    win = np.hanning(nwin).astype(np.float32)
    nfr = max(0, (len(x) - nwin) // hop)
    idx = np.arange(nwin)[None, :] + hop * np.arange(nfr)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * win, axis=1))
    f = np.fft.rfftfreq(nwin, 1 / SR)
    out = np.zeros(nfr, np.float32)
    for lo, hi in bands:
        e = np.log1p(1000 * spec[:, (f >= lo) & (f < hi)].sum(axis=1))
        d = np.maximum(0, np.diff(e, prepend=e[0]))
        d = d - np.convolve(d, np.ones(fs_env // 2) / (fs_env // 2), "same")    # remove slow trend
        out += np.maximum(0, d) / (np.std(d) + 1e-9)
    return out


def xcorr_norm(sig, ref):
    """Normalised cross-correlation of ref against every position of sig (lags 0..len(sig)-len(ref))
    plus negative lags where ref hangs off the start (only the overlap counts)."""
    n = 1 << int(np.ceil(np.log2(len(sig) + len(ref))))
    cc = np.fft.irfft(np.fft.rfft(sig, n) * np.conj(np.fft.rfft(ref, n)), n)
    cc = np.concatenate([cc[-(len(ref) - 1):], cc[:len(sig)]])
    lags = np.arange(-(len(ref) - 1), len(sig))
    # energy of the overlapping parts, for normalisation
    cs = np.concatenate([[0], np.cumsum(sig ** 2)])
    cr = np.concatenate([[0], np.cumsum(ref ** 2)])
    s0 = np.clip(lags, 0, len(sig)); s1 = np.clip(lags + len(ref), 0, len(sig))
    r0 = np.clip(-lags, 0, len(ref)); r1 = np.clip(len(sig) - lags, 0, len(ref))
    norm = np.sqrt((cs[s1] - cs[s0]) * (cr[r1] - cr[r0])) + 1e-9
    ov = (s1 - s0) / len(ref)
    return lags, np.where(ov > 0.5, cc / norm, 0)


def onset_match(mh, fs_mic, b, fs_env=200, win=None, step=None):
    """Hub offset (s) of bounce t=0 from onset envelopes. mh: mic on the hub grid at fs_mic."""
    import numpy as _np
    mic48 = _np.interp(_np.arange(0, len(mh) * SR // fs_mic) / SR * fs_mic, _np.arange(len(mh)), mh)
    em, eb = onset_env(mic48.astype(_np.float32), fs_env), onset_env(b, fs_env)
    lags, cc = xcorr_norm(em, eb)
    i = int(_np.argmax(cc))
    rest = _np.concatenate([cc[:max(0, i - fs_env // 10)], cc[i + fs_env // 10:]])
    return lags[i] / fs_env, float(cc[i]), float(cc[i] / max(rest.max(), 1e-9)), em, eb
