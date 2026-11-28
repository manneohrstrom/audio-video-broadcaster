"""Placing the AI cut's decisions on the song (docs/ai-cut-plan.md). Standard library only.

The model gives each cut as bounce seconds, a camera and a reason. The hub turns that into
the edit format's cuts ({"at_ns" from the song range's start, "camera", "by": "ai",
"reason", and "note" when the hub changed something), checking each one:

  - the time goes on the hub clock with the sync and its drift, like the beats (beats.json);
  - it moves to the nearest bar start within BAR_REACH_S, else the nearest beat within
    BEAT_REACH_S;
  - a camera that has no footage for the shot is replaced: by a wide camera that has it
    (shot "wide" or showing the whole band), else the camera before; dropped frames in the
    shot are tolerated (rough sections, as everywhere) but said in the note;
  - a cut before the video starts (the bounce often starts earlier) opens the video: of those,
    the last one; cuts after the song's end, or less than MIN_SHOT_S after the one before,
    are left out, and the first shot starts at the song's start.
"""
import bisect

NS_PER_SECOND = 1_000_000_000
FRAME_NS = NS_PER_SECOND // 30
BAR_REACH_S = 1.2
BEAT_REACH_S = 0.5
MIN_SHOT_S = 2.0


def bounce_to_song(bounce_start_ns, drift_ppm, range_start_ns):
    """A function: bounce seconds -> ns from the song range's start (hub time = start + t * (1 + drift))."""
    scale = 1.0 + (drift_ppm or 0.0) * 1e-6
    return lambda seconds: bounce_start_ns + int(round(seconds * NS_PER_SECOND * scale)) - range_start_ns


def nearest(sorted_times, at, reach):
    """The time in sorted_times nearest to at, if within reach; else None."""
    i = bisect.bisect_left(sorted_times, at)
    best = min((sorted_times[j] for j in (i - 1, i) if 0 <= j < len(sorted_times)), key=lambda t: abs(t - at), default=None)
    return best if best is not None and abs(best - at) <= reach else None


def has_footage(camera, a, b, range_start_ns):
    """Recorded the whole shot (song ns a..b), within a frame."""
    return (camera["first_frame_ns"] - range_start_ns <= a + FRAME_NS
            and b - FRAME_NS <= camera["last_frame_ns"] - range_start_ns)


def drops_frames(camera, a, b, range_start_ns):
    return any(r0 - range_start_ns < b and a < r1 - range_start_ns for r0, r1 in camera["rough_sections_ns"])


def is_wide(camera):
    tags = camera.get("tags") or {}
    return tags.get("shot") == "wide" or "whole band" in (tags.get("shows") or [])


def place_cuts(decisions, cameras, range_ns, to_song, beats):
    """(cuts, left_out): the edit's cuts from the model's decisions, and the decisions the hub
    left out, each with why. decisions: [{"time_s", "camera", "why"}]; beats: beats.json or None."""
    length = range_ns[1] - range_ns[0]
    by_id = {camera["id"]: camera for camera in cameras}
    bars = sorted(to_song(t) for t in (beats or {}).get("downbeats_s") or [])
    beat_times = sorted(to_song(t) for t in (beats or {}).get("beats_s") or [])
    left_out, timed = [], []
    for decision in sorted(decisions, key=lambda d: d.get("time_s", 0)):
        reason = str(decision.get("why") or "")[:400]
        if decision.get("camera") not in by_id:
            left_out.append(dict(decision, left_out="no such camera"))
            continue
        asked = to_song(float(decision.get("time_s") or 0))
        notes = []
        at = nearest(bars, asked, BAR_REACH_S * NS_PER_SECOND)
        if at is None:
            at = nearest(beat_times, asked, BEAT_REACH_S * NS_PER_SECOND)
        if at is not None and abs(at - asked) >= NS_PER_SECOND // 20:
            notes.append("moved %+.1f s onto the %s" % ((at - asked) / NS_PER_SECOND,
                                                      "bar" if at in bars else "beat"))
        at = asked if at is None else at
        if at >= length:
            left_out.append(dict(decision, left_out="after the song's end"))
            continue
        if at < 0:                                   # before the video: the last of these opens it
            notes = ["before the video starts (%.1f s): opens it" % (at / NS_PER_SECOND)]
            if timed and timed[-1]["at_ns"] == 0:
                earlier = timed.pop()
                left_out.append({"time_s": None, "camera": earlier["camera"], "why": earlier["reason"],
                                 "left_out": "before the video starts, and not the last such cut"})
            at = 0
        timed.append({"at_ns": at, "camera": decision["camera"], "reason": reason, "notes": notes})
    spaced = []
    for cut in timed:
        if spaced and cut["at_ns"] - spaced[-1]["at_ns"] < MIN_SHOT_S * NS_PER_SECOND:
            left_out.append({"time_s": cut["at_ns"] / NS_PER_SECOND, "camera": cut["camera"], "why": cut["reason"],
                             "left_out": "less than %g s after the cut before" % MIN_SHOT_S})
            continue
        spaced.append(cut)
    if not spaced:
        return [], left_out
    if spaced[0]["at_ns"] > 0:
        spaced[0]["notes"].append("starts at the song's start (asked for %.1f s in)" % (spaced[0]["at_ns"] / NS_PER_SECOND))
    spaced[0]["at_ns"] = 0
    cuts = []
    for i, cut in enumerate(spaced):
        a, b = cut["at_ns"], spaced[i + 1]["at_ns"] if i + 1 < len(spaced) else length
        camera = by_id[cut["camera"]]
        if not has_footage(camera, a, b, range_ns[0]):
            covering = [c for c in cameras if has_footage(c, a, b, range_ns[0])]
            previous = by_id.get(cuts[-1]["camera"]) if cuts else None
            stand_in = (next((c for c in covering if is_wide(c)), None)
                        or (previous if previous in covering else None) or (covering[0] if covering else None))
            if stand_in is not None:
                cut["notes"].append("%s has no footage here: %s instead" % (camera["id"], stand_in["id"]))
                camera = stand_in
        elif drops_frames(camera, a, b, range_ns[0]):
            cut["notes"].append("%s drops frames in this shot" % camera["id"])
        placed = {"at_ns": a, "camera": camera["id"], "by": "ai", "reason": cut["reason"]}
        if cut["notes"]:
            placed["note"] = "; ".join(cut["notes"])[:300]
        if cuts and cuts[-1]["camera"] == placed["camera"]:
            left_out.append({"time_s": a / NS_PER_SECOND, "camera": placed["camera"], "why": placed["reason"],
                             "left_out": "the same camera as the shot before"})
            continue
        cuts.append(placed)
    return cuts, left_out
