"""Editing proxies: small copies of each camera file, only for the editor's video window.

Why (2026-11-16): the editor decodes camera files in the browser. Full-quality files are
~15 Mbit/s with a keyframe every 2 s; between the hub and the editing computer the
network gave ~90 Mbit/s, so four cameras at once didn't fit, and every jump fetched ~4 MB
(switching camera during playback showed a still picture for 1-1.5 s). Proxies are
960x540 with a keyframe every 0.5 s and well under 5 Mbit/s: all cameras can decode at
once and jumps are small. The finished video is always rendered from the originals.

The one hard rule: a proxy's frames carry exactly the times of the original's frames
(the editor's sync rests on them). ffmpeg would otherwise round times to the encoder's
1/30 s grid; -copyts, -enc_time_base demux and a fine track timescale keep them within a
millisecond. make_proxy checks every frame time against the original and refuses a proxy
that is off by more.

Output: <proxies_dir>/<take>/<camera>.mp4 on the hub's SD card (hubd serves it at
/media-proxy/...), written as .part and renamed.
"""
import os
import signal
import subprocess
import time

WIDTH, HEIGHT = 960, 540
KEYFRAME_EVERY_FRAMES = 15          # 0.5 s at 30 fps
QUALITY_CRF = "28"
# The hub's ffmpeg (7.1) writes proxy times to the millisecond (up to 0.5 ms off); the
# Mac's keeps them exact. A millisecond against 33 ms frames changes nothing.
MAX_TIME_ERROR_S = 0.001


def frame_times(path, ffprobe="ffprobe"):
    """Every frame's presentation time (seconds), sorted."""
    result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
                             "-of", "csv=p=0", path], capture_output=True, text=True, check=True)
    times = []
    for line in result.stdout.splitlines():
        field = line.split(",")[0].strip()
        if field and field != "N/A":
            times.append(float(field))
    return sorted(times)


def proxy_command(source, destination, ffmpeg="ffmpeg"):
    return [ffmpeg, "-nostdin", "-v", "error", "-copyts", "-i", source, "-an",
            "-vf", "scale=%d:%d" % (WIDTH, HEIGHT),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", QUALITY_CRF, "-g", str(KEYFRAME_EVERY_FRAMES),
            "-fps_mode", "passthrough", "-enc_time_base:v", "demux", "-video_track_timescale", "90000",
            "-movflags", "+faststart", "-f", "mp4", "-y", destination]


def make_proxy(source, destination, should_pause=lambda: False, should_stop=lambda: False,
               ffmpeg="ffmpeg", ffprobe="ffprobe", poll_s=1.0):
    """Encode the proxy (niced). While should_pause() is true the encoder is suspended
    (SIGSTOP), so a take or live session gets the hub's CPU; should_stop() kills it and
    raises InterruptedError (the proxy is made again later). Checks the frame times."""
    part = destination + ".part"
    os.makedirs(os.path.dirname(destination), exist_ok=True)

    def lower_priority():
        os.nice(19)
    process = subprocess.Popen(proxy_command(source, part, ffmpeg), stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, preexec_fn=lower_priority)
    paused = False
    try:
        while process.poll() is None:
            time.sleep(poll_s)
            if should_stop():
                raise InterruptedError("stopped")
            wanted = should_pause()
            if wanted and not paused:
                process.send_signal(signal.SIGSTOP)
                paused = True
            elif not wanted and paused:
                process.send_signal(signal.SIGCONT)
                paused = False
        error = process.stderr.read().decode(errors="replace")[-300:]
        if process.returncode:
            raise RuntimeError("proxy encode failed: %s" % error)
    except BaseException:
        if process.poll() is None:
            if paused:
                process.send_signal(signal.SIGCONT)
            process.kill()
            process.wait()
        if os.path.exists(part):
            os.remove(part)
        raise
    finally:
        process.stderr.close()

    original, proxy = frame_times(source, ffprobe), frame_times(part, ffprobe)
    if len(original) != len(proxy):
        os.remove(part)
        raise RuntimeError("proxy has %d frames, the original %d" % (len(proxy), len(original)))
    worst = max((abs(a - b) for a, b in zip(original, proxy)), default=0.0)
    if worst > MAX_TIME_ERROR_S:
        os.remove(part)
        raise RuntimeError("proxy frame times are off by up to %.1f ms" % (worst * 1000))
    os.replace(part, destination)
    return {"frames": len(proxy), "max_time_error_ms": round(worst * 1000, 4), "bytes": os.path.getsize(destination)}
