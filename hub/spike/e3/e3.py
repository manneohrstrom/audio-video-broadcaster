#!/usr/bin/env python3
"""Spike E3 (docs/editor-plan.md): what a multicam render costs on the hub.

Renders a cut of an existing take (cuts every 4-10 s across the cameras, a 33^3 LUT
per camera, AAC audio) to a YouTube-style master, and measures wall time, peak memory
and temperature. Reads the take from the STUDIO drive under the playback lease (read-only
mount, as the hub page does). Stops at once if the rig leaves idle (REC or LIVE).

usage: e3.py <take dir> <out dir> --approach {concat,pipe} --preset veryfast [--crf 16]
             [--start 60] [--length 240] [--no-lut]
"""
import argparse, json, os, random, resource, subprocess, sys, threading, time, urllib.request

FPS = 30
W, H = 1920, 1080
LEASE = "/var/lib/hubd/ingest/playback.lease"


def rig_idle():
    try:
        with urllib.request.urlopen("http://127.0.0.1/api/status", timeout=3) as r:
            return json.load(r).get("state") == "idle"
    except OSError:
        return True          # hubd briefly unreachable: don't abort for that


def hub_sample():
    t = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True).stdout
    th = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True).stdout
    return float(t.split("=")[1].split("'")[0]), th.strip().split("=")[1]


def make_cuts(cams, start, length, seed=1):
    """[(camera, file_start_s, frames)], deterministic, cut points on whole frames."""
    rnd, cuts, t, last = random.Random(seed), [], 0, None
    total = int(length * FPS)
    while t < total:
        n = min(int(rnd.uniform(4, 10) * FPS), total - t)
        cam = rnd.choice([c for c in cams if c != last])
        cuts.append((cam, start + t / FPS, n))
        t, last = t + n, cam
    return cuts


def make_lut(path, gain, temp, sat, n=33):
    """A mild per-camera grade as a .cube: exposure gain, warm/cool shift, saturation."""
    with open(path, "w") as f:
        f.write("LUT_3D_SIZE %d\n" % n)
        for b in range(n):
            for g in range(n):
                for r in range(n):
                    rgb = [r / (n - 1) * gain * (1 + temp), g / (n - 1) * gain, b / (n - 1) * gain * (1 - temp)]
                    y = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
                    rgb = [min(1.0, max(0.0, y + (c - y) * sat)) for c in rgb]
                    f.write("%.6f %.6f %.6f\n" % tuple(rgb))


class Watch:
    """Samples temperature; kills the render if the rig leaves idle; keeps the lease."""

    def __init__(self):
        self.procs, self.samples, self.aborted, self.stop = [], [], False, False
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        n = 0
        while not self.stop:
            if not rig_idle():
                self.aborted = True
                for p in self.procs:
                    if p.poll() is None:
                        p.kill()
                return
            if n % 10 == 0:
                self.samples.append(hub_sample())
                os.utime(LEASE) if os.path.exists(LEASE) else open(LEASE, "a").close()
            n += 1
            time.sleep(1)


def audio_input(a):
    """Stand-in for the bounce WAV: 48 kHz stereo noise (the AAC encode is the cost)."""
    return ["-f", "lavfi", "-t", str(a.length), "-i", "anoisesrc=r=48000:a=0.05,aformat=channel_layouts=stereo"]


def encoder_args(a, out, video_map, audio_index):
    return ["-map", video_map, "-map", "%d:a" % audio_index,
            "-c:v", "libx264", "-preset", a.preset, "-crf", a.crf, "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart", "-y", out]


def chain(cam, a, luts):
    f = "fps=%d" % FPS
    if not a.no_lut:
        if a.lut_fmt:     # convert explicitly (e.g. gbrp: planar RGB) instead of ffmpeg's automatic choice
            f += ",format=%s,lut3d=file=%s,format=yuv420p" % (a.lut_fmt, luts[cam])
        else:
            f += ",lut3d=file=%s:interp=%s" % (luts[cam], a.interp)
    return f


def render_concat(a, cuts, luts, out, w):
    """One ffmpeg: every segment its own input (input seek), concat filter."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    for cam, s, n in cuts:
        cmd += ["-threads", "1", "-ss", "%.4f" % s, "-t", "%.4f" % (n / FPS + 0.2),
                "-i", os.path.join(a.take, cam + ".mov")]
    fg = ";".join("[%d:v]%s,trim=end_frame=%d,setpts=PTS-STARTPTS[v%d]" % (i, chain(c, a, luts), n, i)
                  for i, (c, s, n) in enumerate(cuts))
    fg += ";" + "".join("[v%d]" % i for i in range(len(cuts))) + "concat=n=%d:v=1:a=0[out]" % len(cuts)
    cmd += audio_input(a) + ["-filter_complex", fg] + encoder_args(a, out, "[out]", len(cuts))
    p = subprocess.Popen(cmd)
    w.procs.append(p)
    return p.wait()


def render_pipe(a, cuts, luts, out, w):
    """One decoder at a time, raw frames piped into a single encoder."""
    enc = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p",
                            "-s", "%dx%d" % (W, H), "-r", str(FPS), "-i", "-"] + audio_input(a)
                           + encoder_args(a, out, "0:v", 1),
                           stdin=subprocess.PIPE)
    w.procs.append(enc)
    for cam, s, n in cuts:
        dec = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error"]
                               + (["-filter_threads", str(a.filter_threads)] if a.filter_threads else [])
                               + ["-ss", "%.4f" % s,
                                "-i", os.path.join(a.take, cam + ".mov"), "-vf", chain(cam, a, luts),
                                "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
                               stdout=enc.stdin)
        w.procs.append(dec)
        if dec.wait() != 0 or w.aborted:
            break
    enc.stdin.close()
    return enc.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("take")
    ap.add_argument("outdir")
    ap.add_argument("--approach", choices=("concat", "pipe"), default="pipe")
    ap.add_argument("--preset", default="veryfast")
    ap.add_argument("--crf", default="16")
    ap.add_argument("--start", type=float, default=60)
    ap.add_argument("--length", type=float, default=240)
    ap.add_argument("--no-lut", action="store_true")
    ap.add_argument("--lut-fmt", default="", help="pixel format for the LUT step, e.g. gbrp")
    ap.add_argument("--filter-threads", type=int, default=0)
    ap.add_argument("--lut-size", type=int, default=33)
    ap.add_argument("--interp", default="tetrahedral", help="lut3d interpolation: nearest, trilinear, tetrahedral")
    a = ap.parse_args()
    if not rig_idle():
        sys.exit("rig not idle")
    os.makedirs(a.outdir, exist_ok=True)
    cams = sorted(f[:-4] for f in os.listdir(a.take) if f.endswith(".mov"))
    luts = {}
    for i, c in enumerate(cams):
        luts[c] = os.path.join(a.outdir, "%s-%d.cube" % (c, a.lut_size))
        if not os.path.exists(luts[c]):
            make_lut(luts[c], 1.0 + 0.05 * (i - 1.5), 0.03 * (i - 1.5), 1.0 + 0.1 * (i % 2), a.lut_size)
    cuts = make_cuts(cams, a.start, a.length)
    name = "%s-%s-crf%s%s%s%s" % (a.approach, a.preset, a.crf, "-nolut" if a.no_lut else "",
                                  "-" + a.lut_fmt if a.lut_fmt else "", "-ft%d" % a.filter_threads if a.filter_threads else "") + (
        "-lut%d-%s" % (a.lut_size, a.interp) if not a.no_lut else "")
    out = os.path.join(a.outdir, name + ".mp4")
    w = Watch()
    t0 = time.monotonic()
    rc = (render_concat if a.approach == "concat" else render_pipe)(a, cuts, luts, out, w)
    wall = time.monotonic() - t0
    w.stop = True
    w.samples.append(hub_sample())
    rep = {"run": name, "cuts": len(cuts), "length_s": a.length, "wall_s": round(wall, 1),
           "x_realtime": round(a.length / wall, 3), "exit": rc, "aborted_rig_busy": w.aborted,
           "peak_rss_mb_largest_child": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024),
           "temp_max_c": max(s[0] for s in w.samples), "throttled": sorted({s[1] for s in w.samples})}
    if rc == 0 and not w.aborted:
        info = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
             "stream=nb_read_packets,avg_frame_rate:format=duration,size,bit_rate", "-of", "json", out],
            capture_output=True, text=True).stdout)
        rep.update(frames=int(info["streams"][0]["nb_read_packets"]), frames_expected=int(a.length * FPS),
                   avg_frame_rate=info["streams"][0]["avg_frame_rate"],
                   duration_s=float(info["format"]["duration"]),
                   mbps=round(int(info["format"]["bit_rate"]) / 1e6, 1))
    print(json.dumps(rep))


if __name__ == "__main__":
    main()
