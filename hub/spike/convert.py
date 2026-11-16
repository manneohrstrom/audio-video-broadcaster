#!/usr/bin/env python3
"""Phase 3 spike S1: make editor test files from one camera's take folder.

usage: convert.py <take_dir> <out_dir> [--path A|B|both] [--preset veryfast] [--crf 16]
                  [--keep-mkv] [--mkv FILE]

  --keep-mkv  keep the timestamped .mkv (to encode Path B elsewhere, e.g. on the Mac)
  --mkv FILE  use an existing timestamped .mkv instead of running mkvmerge

  Path A, native timing: real per-frame timestamps from video.pts, -c copy.
         ~30.011 fps, a dropped frame becomes one long frame (VFR). No re-encode.
  Path B, conform: same timestamps placed on an exact 30.000 fps time-of-day grid;
         gaps and the rate difference become repeated frames. libx264 re-encode.

Both get a time-of-day start timecode: the camera's first-frame wall clock
(manifest first_frame_wallclock_ns, local time), floored to the 30 fps grid.
Needs mkvmerge (mkvtoolnix) and ffmpeg. Prints one JSON line per output.
"""
import argparse
import json
import os
import subprocess
import sys
import time

FPS = 30


def grid_origin(wc_ns):
    """(timecode string, origin_ns, residual_ns): the frame-grid instant at or before wc_ns."""
    sec = wc_ns // 1_000_000_000
    frac = wc_ns - sec * 1_000_000_000
    ff = frac * FPS // 1_000_000_000
    origin = sec * 1_000_000_000 + ff * 1_000_000_000 // FPS
    lt = time.localtime(sec)
    tc = "%02d:%02d:%02d:%02d" % (lt.tm_hour, lt.tm_min, lt.tm_sec, ff)
    return tc, origin, wc_ns - origin


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit("FAILED: %s\n%s" % (" ".join(cmd), r.stderr[-2000:]))
    return r.stdout


def probe(path):
    out = run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
               "-show_entries", "stream=nb_read_frames,r_frame_rate,avg_frame_rate,duration:stream_tags=timecode",
               "-of", "json", path])
    return json.loads(out)["streams"][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("take_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--path", default="both", choices=["A", "B", "both"])
    ap.add_argument("--preset", default="veryfast")
    ap.add_argument("--crf", default="16")
    ap.add_argument("--keep-mkv", action="store_true")
    ap.add_argument("--mkv")
    a = ap.parse_args()

    with open(os.path.join(a.take_dir, "manifest.json")) as f:
        man = json.load(f)
    cam, take = man["camera"], man["take_id"]
    tc, origin, residual = grid_origin(man["first_frame_wallclock_ns"])
    os.makedirs(a.out_dir, exist_ok=True)
    mkv = a.mkv or os.path.join(a.out_dir, "%s_%s.ts.mkv" % (take, cam))
    if not a.mkv:
        run(["mkvmerge", "-q", "-o", mkv, "--timestamps", "0:" + os.path.join(a.take_dir, "video.pts"),
             os.path.join(a.take_dir, "video.h264")])
    meta = ["-metadata", "title=%s %s" % (take, cam), "-metadata", "comment=studio-multicam spike S1",
            "-timecode", tc, "-movflags", "+faststart"]

    if a.path in ("A", "both"):
        out = os.path.join(a.out_dir, "%s_%s_A-native.mov" % (take, cam))
        t0 = time.time()
        run(["ffmpeg", "-v", "error", "-y", "-i", mkv, "-map", "0:v", "-c", "copy",
             "-video_track_timescale", "90000"] + meta + [out])
        print(json.dumps({"path": "A", "file": out, "timecode": tc, "seconds": round(time.time() - t0, 1),
                          "source_frames": man["frames"], "dropped": man["dropped_frames"], **probe(out)}))

    if a.path in ("B", "both"):
        out = os.path.join(a.out_dir, "%s_%s_B-conform.mov" % (take, cam))
        # Shift so t=0 is the grid origin (first frame lands `residual` after it), then
        # sample an exact 30.000 fps grid: each output frame = input frame on screen then.
        vf = "setpts=PTS-STARTPTS+%f/TB,fps=fps=%d:start_time=0:round=near" % (residual / 1e9, FPS)
        t0 = time.time()
        run(["ffmpeg", "-v", "error", "-y", "-i", mkv, "-map", "0:v", "-vf", vf,
             "-c:v", "libx264", "-preset", a.preset, "-crf", a.crf, "-pix_fmt", "yuv420p",
             "-r", str(FPS), "-video_track_timescale", "30000"] + meta + [out])
        print(json.dumps({"path": "B", "file": out, "timecode": tc, "seconds": round(time.time() - t0, 1),
                          "preset": a.preset, "crf": a.crf, "source_frames": man["frames"],
                          "dropped": man["dropped_frames"], **probe(out)}))
    if not (a.keep_mkv or a.mkv):
        os.remove(mkv)


if __name__ == "__main__":
    main()
