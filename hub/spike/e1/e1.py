#!/usr/bin/env python3
"""Spike E1 (docs/editor-plan.md): rewrap a raw take with -c copy at real frame times,
then check every frame's time in the .mov against video.pts (the manifest's clock).

usage: e1.py <raw dir with video.h264, video.pts, manifest.json> [--scale NS] [--timescale N]
Writes <dir>/e1.mov and prints the timing report as JSON."""
import argparse, json, os, subprocess, sys, time

FPS = 30


def grid_origin(wc_ns):
    sec, frac = divmod(wc_ns, 1_000_000_000)
    ff = frac * FPS // 1_000_000_000
    origin = sec * 1_000_000_000 + ff * 1_000_000_000 // FPS
    lt = time.localtime(sec)
    return "%02d:%02d:%02d:%02d" % (lt.tm_hour, lt.tm_min, lt.tm_sec, ff), wc_ns - origin


def read_pts(path):
    with open(path) as f:
        return [float(l) for l in f if l.strip() and not l.startswith("#")]


def run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--scale", default="1000", help="mkvmerge --timestamp-scale (ns)")
    ap.add_argument("--timescale", default="90000", help="mov video track timescale")
    ap.add_argument("--no-offset", action="store_true", help="first frame at 0, not at its grid residual")
    a = ap.parse_args()
    d = a.dir
    man = json.load(open(os.path.join(d, "manifest.json")))
    pts = read_pts(os.path.join(d, "video.pts"))
    tc, residual = grid_origin(man["first_frame_wallclock_ns"])
    off = 0.0 if a.no_offset else residual / 1e6       # ms
    mkv, out = os.path.join(d, "e1.mkv"), os.path.join(d, "e1.mov")
    tsf = os.path.join(d, "video.pts")
    t0 = time.monotonic()
    run(["mkvmerge", "-q", "-o", mkv, "--timestamp-scale", a.scale,
         "--timestamps", "0:" + tsf, os.path.join(d, "video.h264")])
    t1 = time.monotonic()
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y"]
    if off:
        # First frame at its offset from the timecode's grid slot. mkvmerge starts at 0,
        # so it goes on as -itsoffset; the mov edit list carrying it is in the movie
        # timescale, so that must be fine-grained too (default 1000 rounds to ms).
        cmd += ["-itsoffset", "%.6f" % (off / 1000)]
    cmd += ["-i", mkv, "-map", "0:v", "-c", "copy", "-video_track_timescale", a.timescale, "-movie_timescale", a.timescale,
            "-timecode", tc, "-metadata", "title=e1 %s" % man.get("camera"),
            "-movflags", "+faststart", "-f", "mov", out]
    run(cmd)
    t2 = time.monotonic()
    os.remove(mkv)
    info = json.loads(run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "packet=pts_time,dts_time,duration_time,flags:stream=avg_frame_rate,r_frame_rate,"
                           "time_base,nb_frames:stream_tags=timecode:format=start_time,duration",
                           "-of", "json", out]))
    pk = sorted(info["packets"], key=lambda p: float(p["pts_time"]))
    got = [float(p["pts_time"]) * 1000 for p in pk]
    n = min(len(got), len(pts))
    err = [got[i] - (pts[i] + off) for i in range(n)]
    mx = max(abs(e) for e in err) if err else None
    st = info["streams"][0]
    rep = {
        "dir": d, "frames_pts": len(pts), "frames_mov": len(got), "manifest_frames": man.get("frames"),
        "gaps": len(man.get("gaps") or []), "dropped": man.get("dropped_frames"),
        "timecode": (st.get("tags") or {}).get("timecode"), "timecode_expected": tc,
        "residual_ms": round(residual / 1e6, 3), "first_pts_ms": round(got[0], 3) if got else None,
        "max_abs_err_ms": round(mx, 4) if mx is not None else None,
        "mean_err_ms": round(sum(err) / n, 4) if n else None,
        "time_base": st.get("time_base"), "avg_frame_rate": st.get("avg_frame_rate"),
        "r_frame_rate": st.get("r_frame_rate"), "format": info.get("format"),
        "mkvmerge_s": round(t1 - t0, 2), "ffmpeg_s": round(t2 - t1, 2),
        "bytes_h264": os.path.getsize(os.path.join(d, "video.h264")), "bytes_mov": os.path.getsize(out),
        "keyframes": sum(1 for p in pk if "K" in p.get("flags", "")),
    }
    print(json.dumps(rep, indent=1))
    ok = rep["frames_mov"] == len(pts) and mx is not None and mx < 1.0 and rep["timecode"] == tc
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
