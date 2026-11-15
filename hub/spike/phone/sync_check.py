#!/usr/bin/env python3
"""Phone spike: offset of each camera from a reference camera at every clap.

Works on raw take folders (video.h264 + video.pts + manifest.json), the Pi layout
and the phone spike layout alike. Every frame gets a hub-clock time:
first_frame_wallclock_ns + pts. Motion = mean luma difference to the previous
frame (low res), cached in <dir>/motion.json. Peaks are refined to sub-frame time.

Claps are the motion peaks that the reference camera and a second, trusted camera
(--confirm, another Pi) both see within --agree ms: that drops other movement
(people walking, dishes). Bursts of claps a third of a second apart are fine:
every peak is matched to the nearest one within --window ms.

    python3 sync_check.py REF_DIR --confirm DIR OTHER_DIR [...]

Prints, per camera: offset (camera minus reference, ms) per clap, median, sd and
the trend (ms per minute) from first to last clap. Needs ffmpeg.
"""
import argparse
import json
import os
import re
import statistics
import subprocess


def load(d):
    with open(os.path.join(d, "manifest.json")) as f:
        man = json.load(f)
    with open(os.path.join(d, "video.pts")) as f:
        pts = [float(l) for l in f if l.strip() and not l.startswith("#")]
    cache = os.path.join(d, "motion.json")
    if os.path.exists(cache):
        with open(cache) as f:
            m = json.load(f)
    else:
        out = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "h264", "-i", os.path.join(d, "video.h264"),
                              "-fps_mode", "passthrough", "-vf",
                              "scale=192:108,format=gray,tblend=all_mode=difference,signalstats,"
                              "metadata=print:key=lavfi.signalstats.YAVG:file=-", "-f", "null", "-"],
                             capture_output=True, text=True, check=True).stdout
        m = [0.0] + [float(v) for v in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", out)]
        with open(cache, "w") as f:
            json.dump(m, f)
    if len(m) != len(pts):
        print("  note: %s decoded %d frames, pts has %d" % (d, len(m), len(pts)))
    n = min(len(m), len(pts))
    t0 = man["first_frame_wallclock_ns"] / 1e6
    return man, m[:n], [t0 + p for p in pts[:n]]


def peaks(m, t, k=8.0, min_sep_ms=200):
    """Sub-frame hub times (ms) of sharp motion peaks."""
    med = statistics.median(m)
    mad = statistics.median(abs(x - med) for x in m) or 0.1
    out = []
    for i in range(1, len(m) - 1):
        if m[i] >= m[i - 1] and m[i] > m[i + 1] and m[i] > med + k * mad:
            a, b, c = m[i - 1], m[i], m[i + 1]
            den = a - 2 * b + c
            frac = 0.5 * (a - c) / den if den else 0.0
            ti = t[i] + frac * (t[i + 1] - t[i - 1]) / 2
            if out and ti - out[-1][0] < min_sep_ms:
                if b > out[-1][1]:
                    out[-1] = (ti, b)
                continue
            out.append((ti, b))
    return out


def nearest(ps, x, window):
    best = min(ps, key=lambda p: abs(p[0] - x), default=None)
    return best[0] - x if best and abs(best[0] - x) <= window else None


def report(name, offs):
    if not offs:
        print("%s: no matching claps" % name)
        return
    v = [o for _, o in offs]
    ts = [t / 60000 for t, _ in offs]
    slope = 0.0
    if len(v) > 2 and ts[-1] > ts[0]:
        mt, mv = statistics.mean(ts), statistics.mean(v)
        slope = sum((a - mt) * (b - mv) for a, b in zip(ts, v)) / sum((a - mt) ** 2 for a in ts)
    print("%s: %d claps, median %+.1f ms, mean %+.1f, sd %.1f, trend %+.2f ms/min over %.1f min"
          % (name, len(v), statistics.median(v), statistics.mean(v), statistics.pstdev(v), slope, ts[-1] - ts[0]))
    # group by minute so the start/end comparison is easy to read
    by = {}
    for t, o in offs:
        by.setdefault(int((t - offs[0][0]) // 60000), []).append(o)
    print("   by minute: " + "  ".join("%d': %+.0f (n=%d)" % (k, statistics.median(x), len(x)) for k, x in sorted(by.items())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("others", nargs="+")
    ap.add_argument("--confirm", required=True, help="second trusted (Pi) camera")
    ap.add_argument("--agree", type=float, default=20, help="ms: ref and confirm must agree")
    ap.add_argument("--window", type=float, default=150, help="ms: max offset searched")
    a = ap.parse_args()

    rman, rm, rt = load(a.ref)
    cman, cm, ct = load(a.confirm)
    rp, cp = peaks(rm, rt), peaks(cm, ct)
    claps = [(t, o) for t, o in ((t, nearest(cp, t, a.agree)) for t, _ in rp) if o is not None]
    print("reference %s: %d peaks, %d confirmed by %s" % (rman.get("camera"), len(rp), len(claps), cman.get("camera")))
    report("%s (control)" % cman.get("camera"), claps)
    for d in a.others:
        man, m, t = load(d)
        p = peaks(m, t, k=6.0)
        offs = [(ct_, o) for ct_, o in ((ct_, nearest(p, ct_, a.window)) for ct_, _ in claps) if o is not None]
        report(man.get("camera"), offs)


if __name__ == "__main__":
    main()
