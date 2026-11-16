"""Automatic bounce sync (Phase 4 Track A): where does a bounce sit on the hub clock?

Anchor: the hub mic's scratch recording of the take (scratch.py). Spikes A1/A2
(docs/phase4-spikes.md, 2026-11-16):
  - the mic's own clock runs ~1900 ppm slow: its per-block read times (scratch.times.tsv)
    are fitted with a straight line and the mic is put on the hub clock;
  - matching the *rhythm* works where matching raw sound doesn't (room mic vs close mics,
    effects, a different balance): spectral-flux onset envelopes in three bands, 200 per
    second, normalised cross-correlation;
  - one match is never trusted: the bounce is matched in windows through the song; most
    windows must agree on one straight line (offset + drift), otherwise "needs sync";
  - claps (sharp onsets in both) are only a cross-check: when several agree tightly, they
    must agree with the rhythm within a frame. They never set the offset: drum hits look
    like claps and read up to ~30 ms apart room vs close mics; the rhythm was ~7 ms off;
  - the mic hears a clap ~74 ms after the cameras see the hands meet (USB/ALSA buffering
    plus the motion peak leading the sound): mic_latency_s, subtracted at the end.

Result: sync.json next to the bounce on the hub, {bounce_start_ns, drift_ppm, anchor,
confidence, ...}, or a SyncFailed with the reason (the page then shows "needs sync").
Needs numpy and ffmpeg. Memory stays small: audio is decoded at 16 kHz and the onset
envelopes are computed in one-minute pieces (a 2-hour take is ~1.4 M envelope frames).
"""
import json
import os
import subprocess

import numpy as np

SR = 16000          # analysis sample rate
ENV_FS = 200        # onset envelope frames per second (5 ms)
NWIN = 512          # 32 ms analysis window
BANDS = ((80, 300), (300, 2000), (2000, 7000))
FRAME_S = 1 / 30    # one video frame

DEFAULTS = {
    "mic_latency_s": 0.074,       # A1: cameras' motion peak vs the mic's clap, 4 cameras x 8 claps
    "win_s": 20.0,                # matching windows through the bounce
    "step_s": 10.0,
    "search_s": 30.0,             # each window searches +-this around the whole-bounce match
    "agree_s": 0.025,             # a window agrees with the line within this
    "min_windows": 3,             # windows with sound needed to judge at all
    "high": {"ratio": 1.5, "inliers": 0.6, "scatter_ms": 10.0},
    "medium": {"ratio": 1.3, "inliers": 0.4, "scatter_ms": 15.0},
    "max_drift_ppm": 300,         # interfaces and the hub run within ~100 ppm; more = wrong match
}


class SyncFailed(Exception):
    def __init__(self, reason, details=None):
        super().__init__(reason)
        self.reason, self.details = reason, details or {}


# ------------------------------------------------------------------ audio in

def decode(path, sr=SR, ffmpeg="ffmpeg"):
    """Mono float32 at sr (any format ffmpeg reads)."""
    raw = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4")


def mic_clock(tsv_path, rate=48000, block=4800):
    """(t0_ns, a, b): hub seconds since the first block = a + b * mic seconds (A2).
    Each line is "<first sample> <hub ns when the block was read>"; the block's last
    sample was captured just before it could be read, hence the end of the block."""
    t = np.loadtxt(tsv_path, dtype=np.int64, ndmin=2)
    if len(t) < 20:
        raise SyncFailed("the hub mic recording is too short")
    t = t[:-1]                                     # the last block is usually short
    end = (t[:, 0].astype(float) + block) / rate
    rel = (t[:, 1] - t[0, 1]).astype(float) / 1e9
    b, a = np.polyfit(end, rel, 1)
    return int(t[0, 1]), float(a), float(b)


# ------------------------------------------------------------------ onset envelopes

def onset_env(x, sr=SR, fs=ENV_FS, piece_s=60.0):
    """Spectral-flux onset strength, three bands, normalised per band, summed. Computed in
    pieces so memory stays flat; the slow trend (1/2 s) is removed so loudness doesn't count."""
    hop = sr // fs
    win = np.hanning(NWIN).astype(np.float32)
    f = np.fft.rfftfreq(NWIN, 1 / sr)
    masks = [(f >= lo) & (f < hi) for lo, hi in BANDS]
    nfr = max(0, (len(x) - NWIN) // hop + 1)
    bands = np.zeros((len(BANDS), nfr), np.float32)
    per = int(piece_s * fs)
    for p0 in range(0, nfr, per):
        p1 = min(nfr, p0 + per)
        idx = (np.arange(p0, p1) * hop)[:, None] + np.arange(NWIN)[None, :]
        spec = np.abs(np.fft.rfft(x[idx] * win, axis=1))
        for k, m in enumerate(masks):
            bands[k, p0:p1] = np.log1p(1000 * spec[:, m].sum(axis=1))
    out = np.zeros(nfr, np.float32)
    k = np.ones(fs // 2, np.float32) / (fs // 2)
    for e in bands:
        d = np.maximum(0, np.diff(e, prepend=e[:1]))
        d = np.maximum(0, d - np.convolve(d, k, "same"))
        sd = d.std()
        if sd > 0:
            out += d / sd
    return out


def resample_env(env, a, b, n_out, fs=ENV_FS):
    """The mic's envelope (mic clock) on the hub clock: hub frame j <- mic frame (j/fs - a)/b*fs."""
    src = (np.arange(n_out) / fs - a) / b * fs
    return np.interp(src, np.arange(len(env)), env, left=0, right=0).astype(np.float32)


def xcorr(sig, ref, min_overlap=0.5):
    """Normalised cross-correlation of ref at every lag in sig (negative lags: ref starts
    before sig). Only lags where the overlap is at least min_overlap of the shorter one count."""
    n = 1 << int(np.ceil(np.log2(len(sig) + len(ref))))
    cc = np.fft.irfft(np.fft.rfft(sig, n) * np.conj(np.fft.rfft(ref, n)), n)
    cc = np.concatenate([cc[n - (len(ref) - 1):], cc[:len(sig)]])
    lags = np.arange(-(len(ref) - 1), len(sig))
    cs = np.concatenate([[0], np.cumsum(sig.astype(np.float64) ** 2)])
    cr = np.concatenate([[0], np.cumsum(ref.astype(np.float64) ** 2)])
    s0, s1 = np.clip(lags, 0, len(sig)), np.clip(lags + len(ref), 0, len(sig))
    r0, r1 = np.clip(-lags, 0, len(ref)), np.clip(len(sig) - lags, 0, len(ref))
    norm = np.sqrt((cs[s1] - cs[s0]) * (cr[r1] - cr[r0])) + 1e-9
    ok = (s1 - s0) >= min_overlap * min(len(sig), len(ref))
    return lags, np.where(ok, cc / norm, 0.0)


def peak(lags, cc, guard):
    """Best lag (sub-frame, parabola), its value, and how much higher it is than the best
    peak at least guard frames away (1.0 = no better than elsewhere)."""
    i = int(np.argmax(cc))
    fr = 0.0
    if 0 < i < len(cc) - 1:
        y0, y1, y2 = cc[i - 1], cc[i], cc[i + 1]
        den = y0 - 2 * y1 + y2
        fr = 0.5 * (y0 - y2) / den if den < 0 else 0.0
    rest = np.concatenate([cc[:max(0, i - guard)], cc[i + guard + 1:]])
    second = float(rest.max()) if len(rest) else 0.0
    return lags[i] + fr, float(cc[i]), float(cc[i] / second) if second > 1e-9 else 99.0


# ------------------------------------------------------------------ claps

def claps(x, sr=SR, level=0.05, sharpness=6.0, min_gap_s=0.25):
    """Sharp onsets: a 1 ms peak much louder than the 200 ms before it (as clap_check.py)."""
    w = sr // 1000
    n = len(x) // w
    env = np.abs(x[:n * w]).reshape(n, w).max(axis=1)
    cs = np.concatenate([[0], np.cumsum(env)])
    i = np.arange(200, n - 20)
    pre = (cs[i - 20] - cs[i - 200]) / 180
    loc = np.lib.stride_tricks.sliding_window_view(np.pad(env, 20), 41).max(axis=1)[i]
    cand = i[(env[i] > level) & (env[i] > sharpness * (pre + 0.0015)) & (env[i] >= loc)]
    out = []
    for c in cand:
        j = c
        while j > c - 20 and env[j - 1] > env[c] / 2:     # onset: first ms above half the peak
            j -= 1
        if not out or j / 1000 - out[-1] > min_gap_s:
            out.append(j / 1000)
    return out


# ------------------------------------------------------------------ the match

def match(mic, tsv_path, bounce, cfg=None, mic_rate=48000):
    """mic, bounce: float32 at SR (mic on its own clock). Returns the result dict, or raises
    SyncFailed. Times: hub seconds since the mic's first block, then bounce_start_ns."""
    c = dict(DEFAULTS, **(cfg or {}))
    t0_ns, a, b = mic_clock(tsv_path, rate=mic_rate)
    em = onset_env(mic)
    eb = onset_env(bounce)
    if eb.std() < 1e-6 or em.std() < 1e-6:
        raise SyncFailed("no sound to match in %s" % ("the bounce" if eb.std() < 1e-6 else "the hub mic recording"))
    n_hub = int((a + b * len(em) / ENV_FS) * ENV_FS) + 1
    emh = resample_env(em, a, b, n_hub)
    guard = int(0.1 * ENV_FS)

    # 1. the whole bounce
    lags, cc = xcorr(emh, eb)
    lag0, corr0, ratio0 = peak(lags, cc, guard)
    off0 = lag0 / ENV_FS                     # hub time of bounce t=0

    # 2. windows through the bounce, each searched +-search_s around the whole match
    W, step, S = int(c["win_s"] * ENV_FS), int(c["step_s"] * ENV_FS), int(c["search_s"] * ENV_FS)
    rows = []
    for w0 in range(0, max(1, len(eb) - W + 1), step):
        ref = eb[w0:w0 + W]
        h0 = int(round(lag0)) + w0             # where this window should be on the hub
        if h0 < 0 or h0 + W > len(emh):
            continue                           # this part of the bounce is outside the take
        if ref.std() < 1e-6 or np.count_nonzero(ref > 1) < 0.02 * W:
            continue                           # no onsets in the bounce here (silence)
        if np.count_nonzero(emh[h0:h0 + W] > 1) < 0.02 * W:
            continue                           # the mic heard nothing here
        s0, s1 = max(0, h0 - S), min(len(emh), h0 + W + S)
        seg = emh[s0:s1]
        l, cv = xcorr(seg, ref, min_overlap=0.9)
        lw, val, rat = peak(l, cv, guard)
        rows.append({"t": w0 / ENV_FS, "offset": (s0 + lw - w0) / ENV_FS, "corr": round(val, 3), "ratio": round(rat, 2)})
    if len(rows) < c["min_windows"]:
        raise SyncFailed("too little the hub mic and the bounce both hear (%d windows with sound)" % len(rows),
                         {"windows": rows, "whole": {"offset_s": off0, "ratio": ratio0}})

    # 3. one straight line most windows agree on: try each window's offset as the anchor
    tt = np.array([r["t"] for r in rows])
    oo = np.array([r["offset"] for r in rows])
    best = None
    for i in range(len(rows)):
        for j in range(i, len(rows)):
            drift = 0.0 if j == i or tt[j] == tt[i] else (oo[j] - oo[i]) / (tt[j] - tt[i])
            if abs(drift) * 1e6 > c["max_drift_ppm"]:
                continue
            pred = oo[i] + drift * (tt - tt[i])
            inl = np.abs(oo - pred) < c["agree_s"]
            if best is None or inl.sum() > best.sum():
                best = inl
    p = np.polyfit(tt[best], oo[best], 1) if best.sum() >= 2 else np.array([0.0, float(oo[best][0])])
    if abs(p[0]) * 1e6 > c["max_drift_ppm"]:
        p = np.array([0.0, float(np.median(oo[best]))])
    scatter = float(np.std(oo[best] - np.polyval(p, tt[best])) * 1000) if best.sum() > 2 else 0.0
    frac = float(best.mean())
    music_off = float(p[1])
    for r, ok in zip(rows, best):
        r["agrees"] = bool(ok)

    def grade(g):
        return ratio0 >= g["ratio"] and frac >= g["inliers"] and scatter <= g["scatter_ms"] and best.sum() >= 2

    confidence = "high" if grade(c["high"]) else "medium" if grade(c["medium"]) else None
    whole_agrees = abs(off0 - music_off) < 2 * c["agree_s"]
    details = {"whole": {"offset_s": round(off0, 4), "corr": round(corr0, 3), "ratio": round(ratio0, 2)},
               "windows": rows, "agreeing": int(best.sum()), "of": len(rows), "scatter_ms": round(scatter, 1),
               "drift_ppm": round(p[0] * 1e6, 1)}
    if confidence is None or not whole_agrees:
        why = ("the whole bounce and its pieces point to different places" if not whole_agrees else
               "the match isn't clear enough (%d of %d pieces agree, peak %.2fx the next best)"
               % (best.sum(), len(rows), ratio0))
        raise SyncFailed(why, details)

    # 4. claps: only a cross-check. Drum hits look like claps too, and through the room mic
    #    vs close mics they read up to ~30 ms apart (A1), more than the music match (~7 ms).
    anchor, offset = "hub mic: music", music_off
    mc_hub = [a + b * x for x in claps(mic)]
    bc = claps(bounce)
    pairs = []
    for x in bc:
        want = music_off + p[0] * x + x
        near = [m for m in mc_hub if abs(m - want) < 0.040]
        if near:
            pairs.append(min(near, key=lambda m: abs(m - want)) - x - p[0] * x)
    tight = [q for q in pairs if abs(q - float(np.median(pairs))) < 0.008] if pairs else []
    details["claps"] = {"bounce": len(bc), "mic": len(mc_hub), "paired": len(pairs), "tight": len(tight)}
    if len(tight) >= 3:
        clap_off = float(np.mean(tight))
        details["claps"].update(offset_s=round(clap_off, 4), spread_ms=round((max(tight) - min(tight)) * 1000, 1))
        if abs(clap_off - music_off) > FRAME_S:
            raise SyncFailed("the claps and the music disagree by %.0f ms" % ((clap_off - music_off) * 1000), details)
        anchor = "hub mic: music (claps agree)"

    start_s = offset - c["mic_latency_s"]
    return {"bounce_start_ns": t0_ns + int(round(start_s * 1e9)), "drift_ppm": round(p[0] * 1e6, 1),
            "anchor": anchor, "confidence": confidence, "mic_latency_s": c["mic_latency_s"],
            "music_offset_s": round(music_off, 4), "details": details}


def sync_version(take_dir, song, version, cfg=None, ffmpeg="ffmpeg"):
    """Sync one uploaded version on the hub (audio staged under take_dir, the hub's
    <audio_dir>/<take>). Writes sync.json (or sync_failed.json) and returns the doc."""
    vdir = os.path.join(take_dir, song, "v%d" % version)
    with open(os.path.join(vdir, "version.json")) as f:
        ver = json.load(f)
    out_ok, out_bad = os.path.join(vdir, "sync.json"), os.path.join(vdir, "sync_failed.json")
    try:
        sdir = os.path.join(take_dir, "scratch")
        if not os.path.exists(os.path.join(sdir, "scratch.wav")):
            raise SyncFailed("no hub mic recording for this take")
        mic = decode(os.path.join(sdir, "scratch.wav"), ffmpeg=ffmpeg)
        bounce = decode(os.path.join(vdir, ver["filename"]), ffmpeg=ffmpeg)
        res = match(mic, os.path.join(sdir, "scratch.times.tsv"), bounce, cfg)
        res["file"] = ver["filename"]
        others = other_versions(take_dir, song, version)
        if others:
            far = {v: round((res["bounce_start_ns"] - ns) / 1e9, 3) for v, ns in others.items()
                   if abs(res["bounce_start_ns"] - ns) > FRAME_S * 1e9}
            if far:
                res["confidence"] = "check"
                res["notes"] = ["lands %s from v%s: edited in time?" % (", ".join("%+.3f s" % d for d in far.values()),
                                                                       ", v".join(str(v) for v in far))]
        write_json(out_ok, res)
        if os.path.exists(out_bad):
            os.remove(out_bad)
        return res
    except SyncFailed as e:
        doc = {"reason": e.reason, "details": e.details, "file": ver.get("filename")}
        write_json(out_bad, doc)
        return dict(doc, failed=True)


def other_versions(take_dir, song, version):
    out = {}
    sdir = os.path.join(take_dir, song)
    for v in os.listdir(sdir) if os.path.isdir(sdir) else []:
        if v.startswith("v") and v[1:].isdigit() and int(v[1:]) != version:
            try:
                with open(os.path.join(sdir, v, "sync.json")) as f:
                    out[int(v[1:])] = int(json.load(f)["bounce_start_ns"])
            except (OSError, ValueError, KeyError):
                pass
    return out


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1, default=float)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


if __name__ == "__main__":
    # python3 audiosync.py <take dir on the hub> <song> <version> [config json]
    import sys
    cfg = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
    print(json.dumps(sync_version(sys.argv[1], sys.argv[2], int(sys.argv[3]), cfg), default=float))
