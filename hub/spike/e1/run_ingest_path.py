#!/usr/bin/env python3
"""Spike E1: run ingest's own rewrap + verify_real on raw take folders, in a private
state dir (not the live ingest). usage: run_ingest_path.py <ingest.py dir> <raw dir>..."""
import json, os, shutil, sys, time
sys.path.insert(0, sys.argv[1])
import ingest  # noqa: E402

root = os.path.abspath("state")
cfg = dict(ingest.DEFAULT_CONFIG, state_dir=root, video_timing="real")
ing = ingest.Ingest(cfg)
for raw in sys.argv[2:]:
    man = json.load(open(os.path.join(raw, "manifest.json")))
    take, cam = man["take_id"], man.get("camera") or "cam"
    d = ing.cam_dir(take, cam)
    os.makedirs(d, exist_ok=True)
    for n in ("video.h264", "video.pts", "manifest.json"):
        dst = os.path.join(d, n)
        if not os.path.exists(dst):
            os.link(os.path.join(raw, n), dst)
    t0 = time.monotonic()
    ing.rewrap(take, cam, man)
    t1 = time.monotonic()
    ing.verify(take, cam, man)
    t2 = time.monotonic()
    cs = ing.cam_state(take, cam)
    print(json.dumps({"raw": raw, "rewrap_s": round(t1 - t0, 1), "verify_s": round(t2 - t1, 1),
                      "duration_s": man.get("duration_s"),
                      **{k: cs.get(k) for k in ("timing", "timecode", "frames_out", "frames_in",
                                                "max_time_err_ms", "dropped", "error")}}))
