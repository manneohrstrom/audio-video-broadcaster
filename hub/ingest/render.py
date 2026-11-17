"""Quick mix (Phase 4 A4, docs/phase4-plan.md): an automatic cut and a YouTube render.

The cut is written in the editor's edit format (docs/editor-plan.md, section 2) and the
render is the editor's render (section 6), so the editor later reuses both:

    edit = {"version": 1, "take": ..., "song": ..., "song_version": N, "bounce": <rel path>,
            "range_ns": [start, end],       # hub clock (the cameras' first_frame_wallclock_ns clock)
            "cuts": [{"at_ns": 0, "camera": "cam1"}, ...],   # at_ns: from range_ns[0]
            "colour": {}, "outputs": ["youtube"], "made_by": "quick-v1", "notes": [...]}

Rule (quick-v1): a new camera every 10 s, round the Pi cameras in order, starting and
ending on the sync server camera; a camera is skipped for a shot it didn't record all of,
or that has a rough section (dropped frames) in it.

Time mapping: an ingested camera file's time t is hub time grid_origin + t, where the
grid origin is the 30 fps time-of-day slot at or before the camera's first frame (the
file's timecode). True both for conformed files and for real-timing copies.

Render ("pipe", spike E3): one decoder per shot, one after another (input seek, fps=30
picking the source frame nearest each output frame), raw frames into one encoder with
the bounce as the only audio. Memory stays flat whatever the number of shots.
Output: 1920x1080, exactly 30 fps, H.264 veryfast CRF 16, AAC 320 kbps, faststart.
"""
import json
import os
import signal
import subprocess

FPS = 30
W, H = 1920, 1080
SHOT_S = 10.0
NS = 1_000_000_000


def grid_origin_ns(wc_ns):
    """Hub time of a camera file's t=0: the 30 fps grid slot at or before the first frame
    (same as ingest.grid_origin, without the timecode string)."""
    sec, frac = divmod(wc_ns, NS)
    return sec * NS + (frac * FPS // NS) * NS // FPS


def cameras(take_doc, include_phones=False):
    """{cam: {"origin_ns", "start_ns", "end_ns", "rough": [(a_ns, b_ns)]}} for the Pi cameras,
    and the phones too with include_phones (the editor can cut to a phone; the quick mix's
    rotation is Pi cameras only). Phone files are laid out like the Pis' (ingest rewrap)."""
    out = {}
    mans = take_doc.get("manifests") or {}
    for cam, man in sorted(mans.items()):
        if not man.get("first_frame_wallclock_ns") or (man.get("source") == "phone" and not include_phones):
            continue
        first = man["first_frame_wallclock_ns"]
        last = man.get("last_frame_wallclock_ns") or first + int((man.get("duration_s") or 0) * NS)
        rough = []
        for g in (take_doc.get("cameras") or {}).get(cam, {}).get("rough") or _rough(man):
            if "start_wallclock_ns" in g:
                rough.append((g["start_wallclock_ns"], g["end_wallclock_ns"]))
        out[cam] = {"origin_ns": grid_origin_ns(first), "start_ns": first, "end_ns": last, "rough": rough}
    return out


def _rough(man):
    try:
        from ingest import rough_sections          # older takes: work it out from the gaps
    except ImportError:
        return []
    return rough_sections(man.get("gaps"), man.get("first_frame_wallclock_ns"))


def quick_cut(take_doc, bounce_start_ns, bounce_s, song, song_version, bounce_rel, server="cam1", shot_s=SHOT_S):
    cams = cameras(take_doc)
    if not cams:
        raise ValueError("no Pi camera in this take")
    order = sorted(cams)
    if server not in cams:
        server = order[0]
    # The song: where the bounce and the cameras overlap.
    a = max(bounce_start_ns, min(c["start_ns"] for c in cams.values()))
    b = min(bounce_start_ns + int(bounce_s * NS), max(c["end_ns"] for c in cams.values()))
    if b - a < NS:
        raise ValueError("the bounce and the video overlap by less than a second")
    notes = []

    edge = NS // FPS           # the cameras start and stop microseconds apart: allow a frame

    def ok(cam, t0, t1):
        c = cams[cam]
        return (c["start_ns"] <= t0 + edge and t1 - edge <= c["end_ns"]
                and not any(r0 < t1 and t0 < r1 for r0, r1 in c["rough"]))

    rot = order[order.index(server):] + order[:order.index(server)]
    n_shots = max(1, int(round((b - a) / (shot_s * NS))))
    step = (b - a) / n_shots
    cuts, nxt = [], 0          # nxt: where the rotation goes on
    for i in range(n_shots):
        t0, t1 = a + int(i * step), a + int((i + 1) * step)
        want = [server] if i in (0, n_shots - 1) else []
        want += [rot[(nxt + j) % len(rot)] for j in range(len(rot))]
        cam = next((c for c in want if ok(c, t0, t1)), None)
        if cam is None:   # nobody has all of it: whoever covers the most
            cam = max(order, key=lambda c: min(t1, cams[c]["end_ns"]) - max(t0, cams[c]["start_ns"]))
            notes.append("shot at %.1f s: no camera without problems, used %s" % ((t0 - a) / NS, cam))
        nxt = (rot.index(cam) + 1) % len(rot)
        if not cuts or cuts[-1]["camera"] != cam:     # same camera again: one longer shot
            cuts.append({"at_ns": t0 - a, "camera": cam})
    return {"version": 1, "take": take_doc.get("take_id"), "song": song, "song_version": song_version,
            "bounce": bounce_rel, "bounce_start_ns": bounce_start_ns, "range_ns": [a, b], "cuts": cuts,
            "colour": {}, "outputs": ["youtube"], "made_by": "quick-v1", "notes": notes}


def plan(edit, take_doc):
    """Segments [(cam, file_start_s, frames)] and (audio_start_s, frames total)."""
    cams = cameras(take_doc)
    a, b = edit["range_ns"]
    total = int(round((b - a) * FPS / NS))
    bounds = [int(round(c["at_ns"] * FPS / NS)) for c in edit["cuts"]] + [total]
    segs = []
    for c, f0, f1 in zip(edit["cuts"], bounds, bounds[1:]):
        if f1 <= f0:
            continue
        t_ns = a + f0 * NS // FPS
        segs.append((c["camera"], (t_ns - cams[c["camera"]]["origin_ns"]) / NS, f1 - f0))
    return segs, (a - edit["bounce_start_ns"]) / NS, total


def encoder_cmd(bounce, audio_start_s, frames, out, preset="veryfast", crf="16"):
    return (["ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", "%dx%d" % (W, H),
             "-r", str(FPS), "-i", "-", "-ss", "%.6f" % audio_start_s, "-t", "%.6f" % (frames / FPS), "-i", bounce,
             # -threads 4: 358 MB at ~1x real time on the 1 GB hub (auto: 387 MB). No -shortest:
             # in ffmpeg 7 it buffers ~150 MB to line the streams up; -t and -frames:v bound them instead.
             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-threads", "4", "-preset", preset, "-crf", crf,
             "-pix_fmt", "yuv420p", "-r", str(FPS), "-frames:v", str(frames), "-c:a", "aac", "-b:a", "320k",
             "-ar", "48000", "-movflags", "+faststart", "-y", out])


def decoder_cmd(src, start_s, frames):
    # Input seek (decodes from the keyframe before and drops frames before start_s),
    # then fps=30: one output frame every 1/30 s from start_s, the nearest source frame.
    return (["ffmpeg", "-nostdin", "-v", "error", "-threads", "2", "-ss", "%.6f" % max(0.0, start_s), "-i", src,
             "-vf", "fps=%d:round=near,scale=%d:%d" % (FPS, W, H), "-frames:v", str(frames),
             "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"])


class Aborted(Exception):
    pass


def render(segs, video_dir, bounce, audio_start_s, frames, out, busy, should_abort=lambda: False,
           progress=lambda f: None, popen=subprocess.Popen, poll=0.5, nice=19):
    """Run the pipe. busy(): SIGSTOP both processes until it clears. should_abort():
    kill and raise Aborted. progress(frames_done) about every poll."""
    def pre():
        # If memory ever runs out, the kernel kills a render process first (the render
        # fails cleanly) instead of the 1 GB hub thrashing until the watchdog resets it
        # (2026-11-16: it did, with -shortest in the encoder).
        try:
            with open("/proc/self/oom_score_adj", "w") as f:
                f.write("1000")
        except OSError:
            pass
        if nice:
            os.nice(nice)
    enc = popen(encoder_cmd(bounce, audio_start_s, frames, out), stdin=subprocess.PIPE,
                stderr=subprocess.PIPE, preexec_fn=pre)
    done, stopped = 0, False
    try:
        for cam, start_s, n in segs:
            dec = popen(decoder_cmd(os.path.join(video_dir, cam + ".mov"), start_s, n), stdout=enc.stdin,
                        stderr=subprocess.PIPE, preexec_fn=pre)
            while True:
                try:
                    dec.wait(timeout=poll)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if should_abort():
                    raise Aborted("stopped")
                b = busy()
                if b and not stopped:
                    for p in (dec, enc):
                        p.send_signal(signal.SIGSTOP)
                    stopped = True
                elif not b and stopped:
                    for p in (dec, enc):
                        p.send_signal(signal.SIGCONT)
                    stopped = False
            err = dec.stderr.read()[-300:]
            dec.stderr.close()
            if dec.returncode:
                raise RuntimeError("decoding %s at %.1f s failed: %s" % (cam, start_s, err))
            done += n
            progress(done)
        enc.stdin.close()
        code, err = enc.wait(), enc.stderr.read()[-300:]
        enc.stderr.close()
        if code:
            raise RuntimeError("encoder failed: %s" % err)
    except BaseException:
        for p in (enc,) + ((dec,) if "dec" in locals() else ()):
            try:
                if stopped:
                    p.send_signal(signal.SIGCONT)
                p.kill()
            except (OSError, ValueError):
                pass
        raise


def probe_output(out):
    r = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_packets,avg_frame_rate,width,height:format=duration", "-of", "json", out],
                       capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    return {"frames": int(s["nb_read_packets"]), "rate": s["avg_frame_rate"], "size": (s["width"], s["height"]),
            "duration_s": float(d["format"]["duration"])}


def thumb(src, t):
    """A 64x36 grey thumbnail (bytes) of the frame at t, picked as the render picks it."""
    return subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "%.6f" % t, "-i", src,
                           "-vf", "fps=%d:round=near,scale=64:36,format=gray" % FPS,
                           "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout


def diff(a, b):
    n = min(len(a), len(b))
    return sum(abs(x - y) for x, y in zip(a[:n], b[:n])) / max(1, n)


def check(out, segs, frames, video_dir, max_checks=24):
    """Problems (empty if fine): frame count, rate, length, and for up to max_checks shots
    that the middle frame looks like its own camera more than any other camera."""
    p = probe_output(out)
    problems = []
    if p["frames"] != frames:
        problems.append("%d frames, expected %d" % (p["frames"], frames))
    if p["rate"] != "30/1":
        problems.append("frame rate %s" % p["rate"])
    if abs(p["duration_s"] - frames / FPS) > 0.1:
        problems.append("length %.3f s, expected %.3f" % (p["duration_s"], frames / FPS))
    cams = sorted({c for c, _, _ in segs})
    pick = segs if len(segs) <= max_checks else [segs[int(i * len(segs) / max_checks)] for i in range(max_checks)]
    starts, f = {}, 0
    for s in segs:
        starts[id(s)] = f
        f += s[2]
    checked = 0
    for s in pick:
        cam, start_s, n = s
        if len(cams) < 2 or n < 3:
            continue
        mid = n // 2
        got = thumb(out, (starts[id(s)] + mid) / FPS)
        d = {c: diff(got, thumb(os.path.join(video_dir, c + ".mov"), start_s + mid / FPS)) for c in cams}
        best = min(d, key=d.get)
        checked += 1
        if best != cam:
            problems.append("shot at %.1f s looks like %s, should be %s" % (starts[id(s)] / FPS, best, cam))
    return problems, dict(p, spot_checks=checked)
