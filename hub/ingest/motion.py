"""Movement per camera, for the automatic cut (docs/editor-plan.md section 3: "prefer
cameras with movement").

From each camera's editing proxy (proxies.py), not the original: the proxy has a keyframe
every 0.5 s and its frames carry the original's times, so decoding only the keyframes, at
64x36 in grey, is cheap (a few seconds per camera-hour) and the times are right as they are.
Movement at a keyframe is how much the picture changed since the one before (mean absolute
difference, 0..255), so a player moving, an arm strumming, a camera with only a still room
in view all read differently. The camera's own noise floor (its 10th percentile: sensor
noise, a flickering light) is taken off, so a camera at high gain doesn't look busy.

Output: <proxies_dir>/<take>/<camera>.motion.json, {"version", "times_s" (file time of each
reading), "motion" (per reading, noise floor removed), "noise_floor"}, written via a
temporary name.

Run: python3 motion.py <proxy.mp4> [<out.json>]
"""
import json
import os
import subprocess
import sys

import numpy as np

import thumbnails

VERSION = 1
WIDTH, HEIGHT = 64, 36


def keyframes_grey(video_path, ffmpeg="ffmpeg", nice=19):
    """The video's keyframes as (count, HEIGHT, WIDTH) uint8, in order."""
    command = ["nice", "-n", str(nice), ffmpeg, "-nostdin", "-v", "error", "-skip_frame", "nokey",
               "-i", video_path, "-vf", "scale=%d:%d,format=gray" % (WIDTH, HEIGHT),
               "-fps_mode", "passthrough", "-f", "rawvideo", "-"]
    raw = subprocess.run(command, capture_output=True, check=True).stdout
    frame_size = WIDTH * HEIGHT
    return np.frombuffer(raw[:len(raw) - len(raw) % frame_size], np.uint8).reshape(-1, HEIGHT, WIDTH)


def measure(video_path, ffmpeg="ffmpeg", ffprobe="ffprobe"):
    """The motion.json document for one camera's proxy."""
    times = thumbnails.keyframe_times(video_path, ffprobe=ffprobe)
    frames = keyframes_grey(video_path, ffmpeg=ffmpeg)
    count = min(len(times), len(frames))
    if count < 2:
        raise ValueError("too few keyframes to measure movement (%d)" % count)
    frames = frames[:count].astype(np.int16)
    change = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    floor = float(np.percentile(change, 10))
    return {
        "version": VERSION,
        "times_s": [round(float(t), 3) for t in times[1:count]],
        "motion": [round(float(max(0.0, c - floor)), 3) for c in change],
        "noise_floor": round(floor, 3),
    }


def write_json(path, document):
    temporary = path + ".tmp"
    with open(temporary, "w") as f:
        json.dump(document, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def motion_path(proxies_dir, take_id, camera_id):
    return os.path.join(proxies_dir, take_id, camera_id + ".motion.json")


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__.strip().splitlines()[-1])
    result = measure(sys.argv[1])
    if len(sys.argv) == 3:
        write_json(sys.argv[2], result)
    print(json.dumps({"readings": len(result["motion"]), "noise_floor": result["noise_floor"],
                      "median": float(np.median(result["motion"]))}))
