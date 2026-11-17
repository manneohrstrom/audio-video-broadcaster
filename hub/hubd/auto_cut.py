"""The automatic cut, version 1: rules (docs/editor-plan.md section 3).

The editor's starting cut for a song, made from what the rig already knows:

  - the bounce's beats and bars (hub-ingest's beats.py, beats.json next to the bounce);
  - each camera's movement over time (hub-ingest's motion.py, from the editing proxies);
  - when each camera recorded and its rough sections (dropped frames).

Rules, in order of weight:

  1. Never show a camera outside the time it recorded or inside a rough section.
  2. Hold each shot 4-10 s: longer (about 8.5 s) where the music is calm, shorter (about
     5 s) where it's busy, judged by how hard the beats hit in the next 10 s against the
     song as a whole.
  3. Cut on a beat. Prefer the first beat of a bar, and beats where the music really hits
     (beats.json "hit"): the music is often jazz, where the beat level can be uncertain,
     and a cut on a hit looks musical either way. Beats with low confidence (rubato, a
     rest) count for little; with no usable beat in reach the cut goes on time alone.
  4. Start on the wide camera and come back to it about every 30 s.
  5. Otherwise pick the camera with the most movement during the shot, among those not
     shown recently (a camera just shown waits its turn), so the cut moves around the band.
     Movement counts on a fixed scale (motion.py's readings: under 0.15 is sensor noise,
     1.5 clearly someone moving; from the clap-test take, 2026-11-16), so in a still room
     the noise doesn't decide and the cameras simply take turns. The wide camera is only
     chosen when it's due (rule 4), or when no other camera can be shown.

Deterministic: the same inputs give the same cut (ties go to the camera shown longest ago,
then by id), so opening a song twice doesn't surprise the user. Pi cameras only, as the
quick mix (decision 6 of docs/phase4-plan.md: phones once they have their own anchor).

All times are hub-clock nanoseconds; the cuts come back as the edit format's
[{"at_ns" (from the song range's start), "camera", "by": "auto"}].
"""
import bisect

NS_PER_SECOND = 1_000_000_000
FRAME_NS = NS_PER_SECOND // 30

DEFAULTS = {
    "hold_min_s": 4.0,
    "hold_max_s": 10.0,
    "hold_calm_s": 8.5,          # preferred length where the music is calm ...
    "hold_busy_s": 5.0,          # ... and where it's busy
    "length_weight": 1.0,        # how much a shot's length away from the target costs
    "wide_every_s": 30.0,        # back to the wide camera when it hasn't been on this long
    "min_confidence": 0.35,      # beats below this hardly count (rubato, a rest)
    "downbeat_bonus": 0.6,       # the first beat of a bar, on top of the beat's hit
    "movement_noise": 0.15,      # movement readings up to this are noise ...
    "movement_full": 1.5,        # ... and from this, clearly someone moving
    "movement_weight": 1.0,      # camera choice: movement against ...
    "freshness_weight": 0.5,     # ... how long ago the camera was on
}


# ------------------------------------------------------------------ inputs on the hub clock

def beat_points(beats, bounce_start_ns, drift_ppm, settings):
    """[(hub ns, musical, trust)] per beat: how musical a place it is to cut (its hit, plus a
    bonus on the first beat of a bar) and how far the beat can be trusted (its confidence).
    drift_ppm: the sync's clock drift (hub time = start + t * (1 + drift))."""
    scale = 1.0 + (drift_ppm or 0.0) * 1e-6
    downbeats = set(beats.get("downbeats_s") or [])
    confidence = beats.get("confidence") or []
    hits = beats.get("hit") or []
    points = []
    for i, t in enumerate(beats.get("beats_s") or []):
        trust = confidence[i] if i < len(confidence) else 0.0
        hit = min(1.5, hits[i]) if i < len(hits) else 0.5
        musical = hit + (settings["downbeat_bonus"] if t in downbeats else 0.0)
        points.append((bounce_start_ns + int(round(t * NS_PER_SECOND * scale)), musical, trust))
    return points


def movement_series(motion_document, file_zero_ns):
    """(hub ns per reading, movement per reading) from a camera's motion.json."""
    times = [file_zero_ns + int(round(t * NS_PER_SECOND)) for t in motion_document.get("times_s") or []]
    return times, list(motion_document.get("motion") or [])


def mean_movement(series, start_ns, end_ns):
    times, values = series
    lo, hi = bisect.bisect_left(times, start_ns), bisect.bisect_right(times, end_ns)
    return sum(values[lo:hi]) / (hi - lo) if hi > lo else 0.0


# ------------------------------------------------------------------ where to cut

def cut_points(range_ns, points, settings):
    """Shot boundaries (hub ns) from the range's start to its end, each shot 4-10 s long,
    each boundary on the best beat in reach (or on time, with no usable beat)."""
    start, end = range_ns
    hold_min = int(settings["hold_min_s"] * NS_PER_SECOND)
    hold_max = int(settings["hold_max_s"] * NS_PER_SECOND)
    span = max(1, hold_max - hold_min)
    times = [p[0] for p in points]
    strength = [musical * min(1.0, trust) for _, musical, trust in points]
    typical = sorted(strength)[len(strength) // 2] if strength else 0.0
    boundaries = [start]
    while True:
        here = boundaries[-1]
        left = end - here
        if left <= hold_max:
            break
        target = target_length(times, strength, typical, here, hold_max, settings)
        # Never leave a last shot shorter than the minimum.
        lo, hi = here + hold_min, min(here + hold_max, end - hold_min)
        best_at, best_value = None, None
        for i in range(bisect.bisect_left(times, lo), bisect.bisect_right(times, hi)):
            at, musical, trust = points[i]
            weight = 1.0 if trust >= settings["min_confidence"] else 0.2
            value = musical * weight * (0.3 + 0.7 * min(1.0, trust)) \
                - settings["length_weight"] * ((at - here - target) / span) ** 2
            if best_value is None or value > best_value:
                best_at, best_value = at, value
        boundaries.append(best_at if best_at is not None else int(min(hi, max(lo, here + target))))
    return boundaries + [end]


def target_length(times, strength, typical, here, reach, settings):
    """The preferred length (ns) of the shot starting at `here`: calm music ahead, longer;
    busy music, shorter. Busy = the beats in reach hit at least twice as hard as the song's
    median beat; calm = half as hard or less (or no trusted beats at all)."""
    lo, hi = bisect.bisect_left(times, here), bisect.bisect_right(times, here + reach)
    ahead = strength[lo:hi]
    busy = (sum(ahead) / len(ahead)) / typical if ahead and typical > 0 else 0.5
    share = min(1.0, max(0.0, (busy - 0.5) / 1.5))            # 0 = calm .. 1 = busy
    seconds = settings["hold_calm_s"] + share * (settings["hold_busy_s"] - settings["hold_calm_s"])
    return seconds * NS_PER_SECOND


# ------------------------------------------------------------------ which camera

def usable(camera, a, b):
    """Recorded the whole shot (within a frame) and no rough section in it."""
    return (camera["first_frame_ns"] <= a + FRAME_NS and b - FRAME_NS <= camera["last_frame_ns"]
            and not any(r0 < b and a < r1 for r0, r1 in camera["rough_sections_ns"]))


def coverage(camera, a, b):
    return min(b, camera["last_frame_ns"]) - max(a, camera["first_frame_ns"])


def choose_cameras(cameras, boundaries, movement, wide_id, settings):
    """One camera id per shot."""
    wide_every = settings["wide_every_s"] * NS_PER_SECOND
    order = {c["id"]: i for i, c in enumerate(cameras)}
    last_on = {}                 # camera id -> index of the shot it was last on
    wide_off_at = None           # when the wide camera last went off
    chosen = []
    for index, (a, b) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        fit = [c for c in cameras if usable(c, a, b)]
        if not fit:
            pick = max(cameras, key=lambda c: (coverage(c, a, b), c["id"] == wide_id))
        else:
            current = chosen[-1] if chosen else None
            wide = next((c for c in fit if c["id"] == wide_id), None)
            wide_due = wide is not None and wide["id"] != current and (
                index == 0 or wide_off_at is None or a - wide_off_at >= wide_every)
            if wide_due:
                pick = wide
            else:
                others = ([c for c in fit if c["id"] not in (current, wide_id)]
                          or [c for c in fit if c["id"] != current] or fit)
                noise, full = settings["movement_noise"], settings["movement_full"]

                def value(camera):
                    """(score, then the camera on longest ago, then the first by id): higher wins."""
                    reading = mean_movement(movement.get(camera["id"], ([], [])), a, b)
                    moving = min(1.0, max(0.0, (reading - noise) / (full - noise)))
                    shown = last_on.get(camera["id"], -100)
                    fresh = min(index - shown, 4) / 4.0
                    score = settings["movement_weight"] * moving + settings["freshness_weight"] * fresh
                    return (round(score, 9), -shown, -order[camera["id"]])
                pick = max(others, key=value)
        if chosen and chosen[-1] == wide_id and pick["id"] != wide_id:
            wide_off_at = a
        last_on[pick["id"]] = index
        chosen.append(pick["id"])
    return chosen


# ------------------------------------------------------------------ all together

def rules_cut(cameras, range_ns, beats, bounce_start_ns, drift_ppm=0.0, motion=None, wide_id=None,
              settings=None):
    """The edit's cuts for one song. cameras: editor_projects.describe_cameras(); beats: the
    bounce's beats.json; motion: {camera id: motion.json} (missing cameras count as still);
    wide_id: the wide camera (default: the sync server, else the first Pi camera)."""
    settings = dict(DEFAULTS, **(settings or {}))
    pis = [c for c in cameras if c["kind"] == "pi"] or list(cameras)
    if not pis:
        return []
    if wide_id not in [c["id"] for c in pis]:
        wide_id = next((c["id"] for c in pis if c["is_sync_server"]), pis[0]["id"])
    movement = {c["id"]: movement_series(motion[c["id"]], c["file_zero_ns"])
                for c in pis if motion and c["id"] in motion}
    boundaries = cut_points(range_ns, beat_points(beats, bounce_start_ns, drift_ppm, settings), settings)
    chosen = choose_cameras(pis, boundaries, movement, wide_id, settings)
    cuts = []
    for at, camera_id in zip(boundaries, chosen):
        if not cuts or cuts[-1]["camera"] != camera_id:
            cuts.append({"at_ns": at - range_ns[0], "camera": camera_id, "by": "auto"})
    return cuts
