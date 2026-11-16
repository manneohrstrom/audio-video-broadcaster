"""Strip thumbnails for the editor's timeline (docs/editor-plan.md, M-E3).

The timeline shows every camera as a filmstrip over the whole song. The strips need small
pictures along the whole take; decoding video for them in the browser would be far too
slow, so the hub makes them once per camera, after ingest:

  - only keyframes are decoded (the Pi cameras put one every 2 s; decoding them alone is
    very cheap, about 1 s of hub time per camera-hour on a Mac-class CPU);
  - each keyframe becomes a 160x90 JPEG tile, 100 tiles to a sheet (10 x 10);
  - index.json lists each tile's time in the camera file, so the page picks the nearest.

Output, per camera: <thumbs_dir>/<take>/<camera>/{index.json, sheet-001.jpg, ...}, on the
hub's SD card (hubd serves it at /media-thumbs/...). Written to a temporary folder and
renamed, so the page never sees half a set.
"""
import json
import os
import shutil
import subprocess

TILE_WIDTH, TILE_HEIGHT = 160, 90
TILES_ACROSS, TILES_DOWN = 10, 10


def keyframe_times(mov_path, ffprobe="ffprobe"):
    """Times (seconds, file time) of the keyframes in the video, in order."""
    result = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                             "-show_entries", "frame=pts_time", "-of", "csv=p=0", mov_path],
                            capture_output=True, text=True, check=True)
    # One time per line; some ffprobe versions add empty fields ("0.000000,"), so take the first.
    times = []
    for line in result.stdout.splitlines():
        field = line.split(",")[0].strip()
        if field and field != "N/A":
            times.append(float(field))
    return times


def make_thumbnails(mov_path, out_dir, ffmpeg="ffmpeg", ffprobe="ffprobe", nice=19):
    """Make the sheets and index.json for one camera file. Returns the index."""
    times = keyframe_times(mov_path, ffprobe)
    if not times:
        raise RuntimeError("no keyframes in %s" % mov_path)
    work = out_dir + ".tmp"
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    subprocess.run(["nice", "-n", str(nice), ffmpeg, "-nostdin", "-v", "error", "-skip_frame", "nokey",
                    "-i", mov_path, "-vf",
                    "scale=%d:%d,tile=%dx%d" % (TILE_WIDTH, TILE_HEIGHT, TILES_ACROSS, TILES_DOWN),
                    "-fps_mode", "passthrough", "-q:v", "5", os.path.join(work, "sheet-%03d.jpg")],
                   check=True, capture_output=True)
    sheets = sorted(name for name in os.listdir(work) if name.startswith("sheet-"))
    per_sheet = TILES_ACROSS * TILES_DOWN
    if len(sheets) != (len(times) + per_sheet - 1) // per_sheet:
        raise RuntimeError("%d sheets for %d keyframes" % (len(sheets), len(times)))
    index = {
        "times_s": [round(t, 4) for t in times],
        "tile_size": [TILE_WIDTH, TILE_HEIGHT],
        "tiles_per_sheet": [TILES_ACROSS, TILES_DOWN],
        "sheets": sheets,
    }
    with open(os.path.join(work, "index.json"), "w") as f:
        json.dump(index, f)
    shutil.rmtree(out_dir, ignore_errors=True)
    os.replace(work, out_dir)
    return index


def have_thumbnails(out_dir):
    return os.path.exists(os.path.join(out_dir, "index.json"))
