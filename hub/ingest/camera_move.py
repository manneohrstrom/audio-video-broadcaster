"""Camera moves: a slow zoom or drift over one shot, made in the edit (a "Ken Burns"; user,
2026-11-28: "to give the cuts a bit more life"). The cameras don't move; the render crops a
slowly changing part of each picture and scales it back to 1080p.

A cut may carry a move for its shot: {"kind": ..., "amount": 0..1}.
  in     a slow push in on the middle of the picture
  out    a slow pull back from it
  left   a slow sideways drift: the view travels to the left, at a constant zoom
  right  the same to the right
The middle of the picture is where the attention is (user, 2026-11-28): on a close-up the
player is in the middle, so "in" on the soloist's close-up zooms in on the soloist. On a wide
shot a drift or a slow push only breaks up the still picture.

The move runs over everything of the shot that is on screen: from half a dissolve before its
cut to half a dissolve after the next one, linearly, so it is already moving as the shot
dissolves in. The Pi cameras record 1080p, so a move is an upscale: amount 1 is a 12 % zoom,
and at most GAIN_PER_SECOND of zoom per second, so a short shot moves less instead of faster.

The editor's preview does the same (hub/hubd/static/editor/js/camera-move.js; the two are
tested against the same numbers). The render does it in each shot's decoder with ffmpeg's
perspective filter (sub-pixel, cubic): zoompan and scale+crop place the picture on whole
pixels, and on a move this slow that shows as stepping (spike 2026-11-28, docs/progress.md).
"""

KINDS = ("in", "out", "left", "right")
MAX_GAIN = 0.12                 # amount 1: the picture 12 % larger at the end (or start) of the move
GAIN_PER_SECOND = 0.015         # at most 1.5 % more zoom per second of the shot
DEFAULT_AMOUNT = 0.5


def clean_move(move):
    """The move as saved ({"kind", "amount"}), or None for no move or one that isn't valid."""
    if not isinstance(move, dict) or move.get("kind") not in KINDS:
        return None
    amount = move.get("amount", DEFAULT_AMOUNT)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        amount = DEFAULT_AMOUNT
    return {"kind": move["kind"], "amount": round(min(1.0, max(0.05, float(amount))), 3)}


def path(move, visible_seconds):
    """The move as straight lines over the shot: (zoom at the start, zoom at the end, centre x at
    the start, centre x at the end), the centre as a share of the width (0.5: the middle).
    visible_seconds: how long the shot is on screen, its dissolves included."""
    gain = min(MAX_GAIN * move["amount"], GAIN_PER_SECOND * max(0.0, visible_seconds))
    if move["kind"] == "in":
        return 1.0, 1.0 + gain, 0.5, 0.5
    if move["kind"] == "out":
        return 1.0 + gain, 1.0, 0.5, 0.5
    zoom = 1.0 + gain
    room = 0.5 - 0.5 / zoom                     # how far the centre can go either way
    if move["kind"] == "right":
        return zoom, zoom, 0.5 - room, 0.5 + room
    return zoom, zoom, 0.5 + room, 0.5 - room


def visible_span(shot_start, shot_end, dissolve_seconds):
    """(from, to): the part of the song a shot is on screen, in its own time unit."""
    return shot_start - dissolve_seconds / 2, shot_end + dissolve_seconds / 2


def rect_at(move, shot_start, shot_end, dissolve_seconds, seconds):
    """(left, top, width, height) of the part of the picture shown at `seconds`, as shares of
    the picture's width and height. Times in seconds of the song."""
    start, end = visible_span(shot_start, shot_end, dissolve_seconds)
    zoom0, zoom1, x0, x1 = path(move, end - start)
    p = min(1.0, max(0.0, (seconds - start) / (end - start))) if end > start else 0.0
    zoom, centre = zoom0 + (zoom1 - zoom0) * p, x0 + (x1 - x0) * p
    size = 1.0 / zoom
    return centre - size / 2, 0.5 - size / 2, size, size


def perspective_filter(move, shot_start_frame, shot_end_frame, dissolve_frames, first_decoded_frame, fps):
    """ffmpeg's perspective filter doing the move in a decoder that hands over the song's output
    frames from first_decoded_frame on (frames numbered from the song's start, at fps).
    perspective's `in` counts the decoder's frames from 1 (checked on the hub's ffmpeg 7.1)."""
    start, end = visible_span(shot_start_frame, shot_end_frame, dissolve_frames)
    zoom0, zoom1, x0, x1 = path(move, (end - start) / fps)
    p = "clip(((in-1)+%d-(%.4f))/%.4f,0,1)" % (first_decoded_frame, start, max(end - start, 1e-6))
    zoom = "(%.6f+%.6f*%s)" % (zoom0, zoom1 - zoom0, p)
    centre_x = "(%.6f+%.6f*%s)" % (x0, x1 - x0, p)
    left, right = "W*(%s-0.5/%s)" % (centre_x, zoom), "W*(%s+0.5/%s)" % (centre_x, zoom)
    top, bottom = "H*(0.5-0.5/%s)" % zoom, "H*(0.5+0.5/%s)" % zoom
    corners = {"x0": left, "y0": top, "x1": right, "y1": top, "x2": left, "y2": bottom, "x3": right, "y3": bottom}
    return "perspective=" + ":".join("%s='%s'" % item for item in corners.items()) + ":interpolation=cubic:eval=frame"
