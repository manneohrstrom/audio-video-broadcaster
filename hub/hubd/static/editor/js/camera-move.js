// Camera moves: a slow zoom or drift over one shot (a "Ken Burns", user 2026-11-28), the
// preview's half. The render does the same in hub/ingest/camera_move.py; the two are tested
// against the same numbers (hub/hubd/tests/editor/camera-move.test.mjs, test_camera_move.py).
//
// A cut may carry a move for its shot: { kind, amount 0..1 }.
//   in     a slow push in on the middle of the picture (a close-up's player is in the middle)
//   out    a slow pull back from it
//   left   a slow sideways drift to the left, at a constant zoom (for the wide shots)
//   right  the same to the right
// The move runs, in a straight line, over everything of the shot on screen: from half a
// dissolve before its cut to half a dissolve after the next one. Amount 1 is a 12 % zoom, and
// at most GAIN_PER_SECOND of zoom per second, so a short shot moves less instead of faster.

export const MOVE_KINDS = ["in", "out", "left", "right"];
export const MAX_GAIN = 0.12;
export const GAIN_PER_SECOND = 0.015;
export const DEFAULT_AMOUNT = 0.5;

/** What each move is called on screen. */
export const MOVE_NAMES = { in: "push in", out: "pull out", left: "drift left", right: "drift right" };

/** [zoom at the start, zoom at the end, centre x at the start, centre x at the end] (share of the width). */
export function movePath(move, visibleSeconds) {
  const gain = Math.min(MAX_GAIN * move.amount, GAIN_PER_SECOND * Math.max(0, visibleSeconds));
  if (move.kind === "in") return [1, 1 + gain, 0.5, 0.5];
  if (move.kind === "out") return [1 + gain, 1, 0.5, 0.5];
  const zoom = 1 + gain;
  const room = 0.5 - 0.5 / zoom;
  return move.kind === "right" ? [zoom, zoom, 0.5 - room, 0.5 + room] : [zoom, zoom, 0.5 + room, 0.5 - room];
}

/**
 * The part of the picture shown at `seconds` for a shot [shotStart, shotEnd] with this move:
 * { left, top, width, height } as shares of the picture. Times in song seconds.
 */
export function moveRectAt(move, shotStart, shotEnd, dissolveSeconds, seconds) {
  const start = shotStart - dissolveSeconds / 2;
  const end = shotEnd + dissolveSeconds / 2;
  const [zoom0, zoom1, x0, x1] = movePath(move, end - start);
  const p = end > start ? Math.min(1, Math.max(0, (seconds - start) / (end - start))) : 0;
  const zoom = zoom0 + (zoom1 - zoom0) * p;
  const centre = x0 + (x1 - x0) * p;
  const size = 1 / zoom;
  return { left: centre - size / 2, top: 0.5 - size / 2, width: size, height: size };
}

/** The move of the cut at `index` in a cut list, as a picture rectangle at `seconds` (or null). */
export function cutMoveRectAt(cuts, index, songSeconds, dissolveSeconds, seconds) {
  const cut = cuts[index];
  if (!cut || !cut.move) return null;
  const end = index + 1 < cuts.length ? cuts[index + 1].atSeconds : songSeconds;
  return moveRectAt(cut.move, cut.atSeconds, end, dissolveSeconds, seconds);
}
