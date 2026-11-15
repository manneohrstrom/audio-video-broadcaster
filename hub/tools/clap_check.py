#!/usr/bin/env python3
"""Clap check: does the video stay in sync with the DAW audio, and the cameras with each other?

Finds sharp clap onsets in an audio file (the DAW bounce) and the matching motion
peaks in each camera's .mov, then reports, per camera, how far the video drifts from
the audio between a reference clap and every later clap. Within one frame (33 ms)
at every clap is a pass.

    python3 clap_check.py BOUNCE.wav cam1.mov cam2.mov ... [--ref N] [--min-gap S]

Needs ffmpeg on PATH. Standard library only. Decoding 1080p is slow: about 1x real
time per camera on the hub's 4 cores when all run at once. Used for the Phase 3 sync
check (2026-11-15) and planned for the Phase 4 scratch-mic acceptance test.
"""
import argparse
import array
import re
import subprocess
import sys

FPS = 30.0
SR = 48000


def audio_claps(path, min_gap_s=2.0, level=8000, sharpness=6.0):
    """Onset times (s) of sharp transients: much louder than the 200 ms before them."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    a = array.array("h")
    a.frombytes(raw[:len(raw) // 2 * 2])
    w = SR // 1000                                           # 1 ms windows
    env = [max(abs(min(a[i:i + w])), abs(max(a[i:i + w]))) for i in range(0, len(a) - w, w)]
    cands = []
    for i in range(200, len(env) - 50):
        pre = sum(env[i - 200:i - 20]) / 180
        if env[i] > level and env[i] > sharpness * (pre + 50) and env[i] == max(env[i - 20:i + 20]):
            j = i
            while j > i - 20 and env[j - 1] > env[i] / 2:    # onset: first ms above half the peak
                j -= 1
            cands.append((env[i] / (pre + 50), j / 1000.0))
    picked = []
    for score, t in sorted(cands, reverse=True):
        if all(abs(t - p) > min_gap_s for p in picked):
            picked.append(t)
    return sorted(picked)


def video_motion(path):
    """Mean absolute luma difference to the previous frame, per frame (low-res)."""
    out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vf",
                          "scale=192:108,format=gray,tblend=all_mode=difference,signalstats,"
                          "metadata=print:key=lavfi.signalstats.YAVG:file=-", "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stdout
    return [float(v) for v in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", out)]


def match(motion, claps, search_s=(-120, 120)):
    """Frame offset audio->video that best lines up motion peaks with the claps,
    then the peak frame of each clap (None if outside the video)."""
    best = None
    for off in range(int(search_s[0] * FPS), int(search_s[1] * FPS)):
        idx = [int(round(t * FPS)) + off for t in claps]
        inside = [i for i in idx if 0 < i < len(motion)]
        if len(inside) < 2:
            continue
        score = sum(max(motion[max(0, i - 2):i + 3]) for i in inside)
        if best is None or score > best[0]:
            best = (score, off)
    if best is None:
        return None, [None] * len(claps)
    off, frames = best[1], []
    for t in claps:
        i = int(round(t * FPS)) + off
        if 1 <= i < len(motion):
            lo, hi = max(1, i - 15), min(len(motion) - 1, i + 15)
            frames.append(max(range(lo, hi), key=lambda k: motion[k]))
        else:
            frames.append(None)
    return off, frames


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("audio")
    ap.add_argument("movs", nargs="+")
    ap.add_argument("--ref", type=int, default=None, help="index of the reference clap (default: first in all videos)")
    ap.add_argument("--min-gap", type=float, default=2.0, help="seconds between separate claps")
    args = ap.parse_args(argv)

    claps = audio_claps(args.audio, args.min_gap)
    print("audio claps (s):", ", ".join("%.3f" % t for t in claps))
    results = {}
    for path in args.movs:
        off, frames = match(video_motion(path), claps)
        results[path] = frames
        print("%s: offset %s frames, clap frames %s" % (path, off, frames))
    ref = args.ref
    if ref is None:
        ref = next((k for k in range(len(claps)) if all(f[k] is not None for f in results.values())), None)
    if ref is None:
        sys.exit("no clap is visible in every video")
    worst = 0.0
    print("\ndrift against audio, from clap %d (%.3f s):" % (ref, claps[ref]))
    for path, frames in results.items():
        parts = []
        for k in range(ref + 1, len(claps)):
            if frames[k] is None:
                continue
            d = (frames[k] - frames[ref]) / FPS - (claps[k] - claps[ref])
            worst = max(worst, abs(d))
            parts.append("+%.0fs: %+.0f ms" % (claps[k] - claps[ref], d * 1000))
        print("  %s  %s" % (path, " | ".join(parts)))
    print("\nworst %.0f ms: %s" % (worst * 1000, "PASS (within 1 frame)" if worst <= 1 / FPS + 1e-9 else "FAIL"))
    return 0 if worst <= 1 / FPS + 1e-9 else 1


if __name__ == "__main__":
    sys.exit(main())
