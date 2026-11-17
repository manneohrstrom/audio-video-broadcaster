#!/usr/bin/env python3
"""Add a film overlay (grain, dust, scratches, light leak) for the editor's Film effects.

    python3 add_overlay.py <clip> --id grain-35mm --name "35mm grain" --mode overlay \\
        --strength 0.4 --source "Grainzilla 4K Film Grain Pack" --license "Grainzilla commercial"
    python3 add_overlay.py --generate-test-grain        # a grain clip made here, for testing

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

MODES = ("overlay", "screen", "soft-light", "multiply")
MAX_SECONDS = 12


def convert(source, destination, width, height, crf):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", source, "-t", str(MAX_SECONDS), "-an",
                    "-vf", "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,fps=30,format=yuv420p"
                    % (width, height, width, height),
                    "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-g", "30",
                    "-movflags", "+faststart", destination], check=True)


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
    # Grain is noise and barely compresses; it also hides compression, so moderate quality
    # (a 6 s grain clip: ~136 MB at CRF 16).
    convert(args.clip, os.path.join(folder, "clip.mp4"), 1920, 1080, 22)
    convert(args.clip, os.path.join(folder, "preview.mp4"), 960, 540, 22)    # lower quality smooths the grain away
    info = {"id": args.id, "name": args.name, "mode": args.mode, "default_strength": args.strength,
            "source": args.source, "license": args.license}
    with open(os.path.join(folder, "overlay.json"), "w") as f:
        json.dump(info, f, indent=1)
    if args.generate_test_grain:
        os.remove(args.clip)
    print("added overlay %s (%s, %s)" % (args.id, args.name, args.mode))


if __name__ == "__main__":
    sys.exit(main())
