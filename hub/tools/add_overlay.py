#!/usr/bin/env python3
"""Add a film overlay (grain, dust, scratches, light leak) for the editor's Film effects.

    python3 add_overlay.py <clip> --id grain-35mm --name "35mm grain" --mode overlay \\
        --strength 0.4 --source "Grainzilla 4K Film Grain Pack" --license "Grainzilla commercial"
    python3 add_overlay.py dust.jpg --id dust --name "Dust" --mode screen --strength 0.6 \\
        --source "Pexels, <photographer>" --license "Pexels licence"
    python3 add_overlay.py --generate-test-grain        # a grain clip made here, for testing

A still (a scan of dusty or light-leaked film) needs no transparency: the blend modes work on
brightness. The still is levelled so that it sits on the mode's neutral value (mid-grey for
overlay and soft-light, black for screen, white for multiply), so the picture keeps its
exposure and only the texture shows (see level_for_mode). It is then turned into a 12 s loop with a slight gate
weave and flicker (--motion still turns that off).

Makes, under <overlays dir>/<id>/ on the hub (default /var/lib/hubd/overlays):
    clip.mp4      1920x1080, 30 fps, H.264, at most 12 s, keyframe every second: the render
                  loops it (hub/ingest/look_render.py)
    preview.mp4   960x540 copy for the editor's preview (looped in WebGL)
    overlay.json  name, blend mode (overlay | screen | soft-light | multiply), default
                  strength, where it came from and under which licence

Overlays bought or downloaded under a licence that forbids redistribution stay on the hub
only: they are never put in the git repository.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

import numpy

MODES = ("overlay", "screen", "soft-light", "multiply")
MAX_SECONDS = 12
STILL_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")
WEAVE_MARGIN = 24        # pixels around the 1920x1080 frame that the weave can move into


def convert(source, destination, width, height, crf):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source, "-t", str(MAX_SECONDS), "-an",
                    "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,fps=30,format=yuv420p"
                    % (width, height, width, height),
                    "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-g", "30",
                    "-movflags", "+faststart", destination], check=True)


def read_still(path, width, height):
    """The still scaled and cropped to fill width x height, as floats 0..1 (height, width, 3)."""
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-frames:v", "1",
                          "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d"
                          % (width, height, width, height),
                          "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"], check=True, capture_output=True).stdout
    return numpy.frombuffer(raw, dtype="<u2").reshape(height, width, 3) / 65535.0


def write_still(pixels, path):
    """Save floats 0..1 (height, width, 3) as a 16-bit PNG."""
    height, width = pixels.shape[:2]
    raw = (numpy.clip(pixels, 0, 1) * 65535 + 0.5).astype("<u2").tobytes()
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb48le",
                    "-s", "%dx%d" % (width, height), "-i", "-", "-frames:v", "1", path], input=raw, check=True)


def box_blur(pixels, radius):
    """Mean over a (2 radius + 1) square around each pixel (edges repeated), per channel."""
    padded = numpy.pad(pixels, ((radius + 1, radius), (radius + 1, radius), (0, 0)), mode="edge")
    sums = padded.cumsum(axis=0).cumsum(axis=1)
    size = 2 * radius + 1
    window = sums[size:, size:] - sums[:-size, size:] - sums[size:, :-size] + sums[:-size, :-size]
    return window / (size * size)


TEXTURE_CONTRAST = 0.16   # standard deviation of the levelled texture for overlay and soft-light
BACKGROUND_RADIUS = 40    # pixels: what is wider than this counts as background for screen and multiply


def box_blur(pixels, radius):
    """Mean over a (2 radius + 1) square around each pixel (edges repeated), per channel."""
    padded = numpy.pad(pixels, ((radius + 1, radius), (radius + 1, radius), (0, 0)), mode="edge")
    sums = padded.cumsum(axis=0).cumsum(axis=1)
    size = 2 * radius + 1
    window = sums[size:, size:] - sums[:-size, size:] - sums[size:, :-size] + sums[:-size, :-size]
    return window / (size * size)


TEXTURE_CONTRAST = 0.16   # standard deviation of the levelled texture for overlay and soft-light
BACKGROUND_RADIUS = 40    # pixels: what is wider than this counts as background for screen and multiply


def level_for_mode(pixels, mode):
    """Level the still so that blending it leaves the picture's exposure alone and only the texture shows.

    overlay, soft-light: neutral is mid-grey. The brightness average is moved to 0.5 and the
                         contrast set to TEXTURE_CONTRAST (scans differ a lot; this way the same
                         strength gives a similar amount of texture). Differences between the
                         channels are kept: a tint stays a tint.
    screen:              neutral is black. The scan's local background is taken away, so only
                         what is brighter than its surroundings (dust, scratches) stays.
    multiply:            neutral is white. Likewise, only what is darker than its surroundings stays.
    """
    luma = pixels @ numpy.array([0.2126, 0.7152, 0.0722])
    if mode in ("overlay", "soft-light"):
        return 0.5 + (pixels - luma.mean()) * (TEXTURE_CONTRAST / max(1e-6, luma.std()))
    background = box_blur(pixels, BACKGROUND_RADIUS)
    if mode == "screen":
        detail = numpy.clip(pixels - background, 0, None)
        return detail / max(1e-6, numpy.percentile(detail, 99.9))
    detail = numpy.clip(background - pixels, 0, None)
    return 1 - detail / max(1e-6, numpy.percentile(detail, 99.9))


def still_to_clip(still, mode, motion, path):
    """A MAX_SECONDS loop of the levelled still, with gate weave and flicker unless motion is "still".

    The weave and flicker are sums of sines whose frequencies are whole multiples of
    1/MAX_SECONDS, so the loop has no jump where it restarts.
    """
    with tempfile.TemporaryDirectory() as work:
        levelled = os.path.join(work, "levelled.png")
        write_still(level_for_mode(read_still(still, 1920 + 2 * WEAVE_MARGIN, 1080 + 2 * WEAVE_MARGIN), mode),
                    levelled)
        if motion == "still":
            moves = "crop=1920:1080:%d:%d" % (WEAVE_MARGIN, WEAVE_MARGIN)
        else:
            cycle = "2*PI*t/%d" % MAX_SECONDS
            weave_x = "%d+2.5*sin(%s*7)+1.5*sin(%s*29+1)" % (WEAVE_MARGIN, cycle, cycle)
            weave_y = "%d+3*sin(%s*11+2)+1*sin(%s*37)" % (WEAVE_MARGIN, cycle, cycle)
            flicker = "0.012*sin(%s*97)+0.008*sin(%s*151+0.5)" % (cycle, cycle)
            moves = "crop=1920:1080:x='%s':y='%s',eq=brightness='%s':eval=frame" % (weave_x, weave_y, flicker)
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-loop", "1", "-framerate", "30",
                        "-i", levelled, "-t", str(MAX_SECONDS), "-vf", moves + ",format=yuv420p",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "12", path], check=True)


def generate_test_grain(path):
    """Six seconds of mid-grey with moving film-like grain (for testing only)."""
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=0x808080:s=1920x1080:r=30:d=6",
                    "-vf", "noise=alls=45:allf=t+u,gblur=sigma=0.7,format=yuv420p",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "16", path], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("clip", nargs="?")
    parser.add_argument("--id")
    parser.add_argument("--name")
    parser.add_argument("--mode", default="overlay", choices=MODES)
    parser.add_argument("--strength", type=float, default=0.4)
    parser.add_argument("--source", default="")
    parser.add_argument("--license", default="")
    parser.add_argument("--motion", default="weave", choices=("weave", "still"),
                        help="for a still: slight gate weave and flicker (default), or none")
    parser.add_argument("--overlays-dir", default="/var/lib/hubd/overlays")
    parser.add_argument("--generate-test-grain", action="store_true")
    args = parser.parse_args()

    if args.generate_test_grain:
        args.id, args.name, args.mode, args.strength = "test-grain", "Test grain", "overlay", 0.5
        args.source, args.license = "generated on the hub (ffmpeg noise)", "ours: no restrictions"
        os.makedirs(args.overlays_dir, exist_ok=True)
        args.clip = os.path.join(args.overlays_dir, "test-grain-source.mp4")
        generate_test_grain(args.clip)
    if not args.clip or not args.id or not args.name:
        parser.error("give a clip, --id and --name (or --generate-test-grain)")
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,40}$", args.id):
        parser.error("--id: lowercase letters, digits and dashes")

    folder = os.path.join(args.overlays_dir, args.id)
    os.makedirs(folder, exist_ok=True)
    made_from_still = args.clip.lower().endswith(STILL_EXTENSIONS)
    if made_from_still:
        still, args.clip = args.clip, os.path.join(folder, "still-source.mp4")
        still_to_clip(still, args.mode, args.motion, args.clip)
    # Grain is noise and barely compresses; it also hides compression, so moderate quality
    # (a 6 s grain clip: ~136 MB at CRF 16).
    convert(args.clip, os.path.join(folder, "clip.mp4"), 1920, 1080, 22)
    convert(args.clip, os.path.join(folder, "preview.mp4"), 960, 540, 22)    # lower quality smooths the grain away
    info = {"id": args.id, "name": args.name, "mode": args.mode, "default_strength": args.strength,
            "source": args.source, "license": args.license}
    with open(os.path.join(folder, "overlay.json"), "w") as f:
        json.dump(info, f, indent=1)
    if args.generate_test_grain or made_from_still:
        os.remove(args.clip)
    print("added overlay %s (%s, %s)" % (args.id, args.name, args.mode))


if __name__ == "__main__":
    sys.exit(main())
