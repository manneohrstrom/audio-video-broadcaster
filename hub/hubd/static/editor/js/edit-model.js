// The cut: which camera is showing when. Pure functions, no DOM, so they're easy to test
// (hub/hubd/tests/editor/edit-model.test.mjs, run with `node --test`).
//
// A cut list is an array of { atSeconds, cameraId, by } sorted by time. Each cut holds
// until the next one; the first is always at 0. Times are seconds from the start of the
// song's range (the page converts to and from the edit file's nanoseconds).
//
// The editing model (docs/editor-plan.md section 4): the user never moves footage around,
// only chooses which camera is on. Three gestures change the cut:
//   - chooseCameraForStretch: press, drag, release on a camera's strip;
//   - slideShot: drag a shot that's in the cut along its camera's strip, keeping its length;
//   - moveEdge / removeCut: drag or delete the boundary between two shots.
// A dragged shot or edge runs over the shots in its way, as far as it's dragged, and two
// shots of the same camera that meet become one (user, 2026-11-21).

/** Shortest a shot may become: shorter leftovers of a dragged-over shot are swallowed (seconds). */
export const MIN_SHOT_SECONDS = 0.2;

const TIME_EPSILON = 1e-6;

/** Index of the cut that is showing at `seconds`. */
export function cutIndexAt(cuts, seconds) {
  let index = 0;
  for (let i = 1; i < cuts.length; i++) {
    if (cuts[i].atSeconds <= seconds) index = i;
  }
  return index;
}

/** Camera id showing at `seconds`. */
export function cameraAt(cuts, seconds) {
  return cuts[cutIndexAt(cuts, seconds)].cameraId;
}

/** End of shot `index`: the next cut, or the end of the song. */
export function shotEnd(cuts, index, songSeconds) {
  return index + 1 < cuts.length ? cuts[index + 1].atSeconds : songSeconds;
}

/** The cut as shots: [{ index, start, end, cameraId }]. */
export function shots(cuts, songSeconds) {
  return cuts.map((cut, index) => ({
    index, start: cut.atSeconds, end: shotEnd(cuts, index, songSeconds), cameraId: cut.cameraId,
  }));
}

/** Sorted, first cut at 0, no two cuts in a row on the same camera. Returns a new list. */
export function normalize(cuts) {
  const sorted = [...cuts].sort((a, b) => a.atSeconds - b.atSeconds);
  const merged = sorted.filter((cut, i) => i === 0 || cut.cameraId !== sorted[i - 1].cameraId);
  if (merged.length) merged[0] = { ...merged[0], atSeconds: 0 };
  return merged;
}

/**
 * Show `cameraId` for exactly [start, end]; before and after, the cut stays as it was
 * (whatever was showing at `end` comes back there). Returns a new list.
 */
export function chooseCameraForStretch(cuts, cameraId, start, end, songSeconds) {
  start = Math.max(0, Math.min(start, songSeconds));
  end = Math.max(0, Math.min(end, songSeconds));
  if (end - start < MIN_SHOT_SECONDS / 2) return cuts;
  const cameraAfter = cameraAt(cuts, end);
  const kept = cuts.filter(cut => cut.atSeconds < start - TIME_EPSILON || cut.atSeconds > end + TIME_EPSILON);
  kept.push({ atSeconds: start, cameraId, by: "user" });
  if (end < songSeconds - 0.05) kept.push({ atSeconds: end, cameraId: cameraAfter, by: "user" });
  return normalize(kept);
}

/** Show `cameraId` from `seconds` until the next cut (keys 1-9 while playing). */
export function cutToCameraAt(cuts, cameraId, seconds) {
  const index = cutIndexAt(cuts, seconds);
  if (cuts[index].cameraId === cameraId) return cuts;
  const next = cuts.map(cut => ({ ...cut }));
  if (seconds - cuts[index].atSeconds < 0.1) next[index] = { atSeconds: cuts[index].atSeconds, cameraId, by: "user" };
  else next.splice(index + 1, 0, { atSeconds: seconds, cameraId, by: "user" });
  return normalize(next);
}

/**
 * Show `cameraId` over [start, end], as chooseCameraForStretch, and swallow what would be
 * left of a shot at either side if it's shorter than MIN_SHOT_SECONDS (no flashes of a
 * camera). The gestures that drag a shot or an edge use this, so a dragged shot runs over
 * whatever is in its way, and meeting a shot of its own camera it becomes one shot with it
 * (normalize). Returns { cuts, start, end, cameraId }: the new list and the stretch as placed.
 */
export function placeShot(cuts, cameraId, start, end, songSeconds) {
  start = Math.max(0, Math.min(start, songSeconds));
  end = Math.max(start, Math.min(end, songSeconds));
  const shotAtStart = cuts[cutIndexAt(cuts, start)];
  if (start - shotAtStart.atSeconds < MIN_SHOT_SECONDS - TIME_EPSILON) start = shotAtStart.atSeconds;
  const nextCut = cuts.find(cut => cut.atSeconds > end + TIME_EPSILON);
  const nextAt = nextCut ? nextCut.atSeconds : songSeconds;
  if (nextAt - end < MIN_SHOT_SECONDS - TIME_EPSILON) end = nextAt;
  return { cuts: chooseCameraForStretch(cuts, cameraId, start, end, songSeconds), start, end, cameraId };
}

/**
 * Drag the edge between shot `cutIndex - 1` and shot `cutIndex` to `seconds`. The shot on
 * the side the edge moves away from grows, over as many shots as it reaches. `held` says
 * which shot's edge was grabbed on a camera strip ("before": the end of shot cutIndex - 1,
 * "after": the start of shot cutIndex): that shot never shrinks below MIN_SHOT_SECONDS.
 * null (a cut handle on the program strip): either side may be swallowed. `original` is the
 * cut list when the drag began. Returns placeShot's result for the shot that grew, plus
 * `edgeSeconds`, where the edge is now.
 */
export function moveEdge(original, cutIndex, seconds, songSeconds, held = null) {
  if (cutIndex <= 0 || cutIndex >= original.length) return null;
  const boundary = original[cutIndex].atSeconds;
  const beforeStart = original[cutIndex - 1].atSeconds;
  const afterEnd = shotEnd(original, cutIndex, songSeconds);
  let at = Math.max(0, Math.min(songSeconds, seconds));
  if (held === "before") at = Math.max(at, beforeStart + MIN_SHOT_SECONDS);
  if (held === "after") at = Math.min(at, afterEnd - MIN_SHOT_SECONDS);
  if (at > boundary + TIME_EPSILON) {
    const grown = placeShot(original, original[cutIndex - 1].cameraId, beforeStart, at, songSeconds);
    return { ...grown, edgeSeconds: grown.end };
  }
  if (at < boundary - TIME_EPSILON) {
    const grown = placeShot(original, original[cutIndex].cameraId, at, afterEnd, songSeconds);
    return { ...grown, edgeSeconds: grown.start };
  }
  return { cuts: original, start: boundary, end: afterEnd, cameraId: original[cutIndex].cameraId, edgeSeconds: boundary };
}

/**
 * Slide shot `index` by `shiftSeconds`, keeping its length. The shot it moves away from
 * fills the space it leaves; the shots it moves onto are run over (placeShot). The first
 * and last shots can't leave the song's ends, so sliding them moves their inner edge.
 * `original` is the cut list when the drag began. Returns placeShot's result.
 */
export function slideShot(original, index, shiftSeconds, songSeconds) {
  const start = original[index].atSeconds;
  const end = shotEnd(original, index, songSeconds);
  const cameraId = original[index].cameraId;
  const unchanged = { cuts: original, start, end, cameraId };
  if (original.length === 1) return unchanged;
  if (index === 0) return moveEdge(original, 1, end + shiftSeconds, songSeconds, "before");
  if (index + 1 >= original.length) return moveEdge(original, index, start + shiftSeconds, songSeconds, "after");
  const shift = Math.max(-start, Math.min(songSeconds - end, shiftSeconds));
  if (Math.abs(shift) < TIME_EPSILON) return unchanged;
  const vacated = original.filter((_, i) => i !== index);      // sliding later: the shot before carries on
  if (shift < 0) vacated[index] = { ...vacated[index], atSeconds: start };   // earlier: the shot after starts sooner
  return placeShot(normalize(vacated), cameraId, start + shift, end + shift, songSeconds);
}

/** Remove the boundary `index` (> 0): the shot before it carries on. */
export function removeCut(cuts, index) {
  if (index <= 0 || index >= cuts.length) return cuts;
  return normalize(cuts.filter((_, i) => i !== index));
}

/** Undo and redo over cut lists: push before a change, then undo/redo swap lists. */
export class History {
  constructor(limit = 200) {
    this.limit = limit;
    this.undoStack = [];
    this.redoStack = [];
  }

  record(cuts) {
    this.undoStack.push(cuts);
    if (this.undoStack.length > this.limit) this.undoStack.shift();
    this.redoStack = [];
  }

  /** Forget the last record (a drag that didn't change anything). */
  dropLast() {
    this.undoStack.pop();
  }

  undo(current) {
    if (!this.undoStack.length) return current;
    this.redoStack.push(current);
    return this.undoStack.pop();
  }

  redo(current) {
    if (!this.redoStack.length) return current;
    this.undoStack.push(current);
    return this.redoStack.pop();
  }

  get canUndo() { return this.undoStack.length > 0; }
  get canRedo() { return this.redoStack.length > 0; }
}
