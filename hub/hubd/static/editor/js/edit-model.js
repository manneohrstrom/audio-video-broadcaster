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
//   - moveCut / removeCut: drag or delete the boundary between two shots.

/** Shortest a shot may become when a neighbour is slid or a cut moved (seconds). */
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

/** How far shot `index` may slide, as [earliest, latest] shift in seconds. */
export function slideLimits(cuts, index, songSeconds) {
  const start = cuts[index].atSeconds;
  const end = shotEnd(cuts, index, songSeconds);
  const isFirst = index === 0;
  const isLast = index + 1 >= cuts.length;
  let earliest = -Infinity;
  let latest = Infinity;
  if (!isFirst) earliest = Math.max(earliest, cuts[index - 1].atSeconds + MIN_SHOT_SECONDS - start);
  if (!isLast) {
    const nextShotEnd = index + 2 < cuts.length ? cuts[index + 2].atSeconds : songSeconds;
    latest = Math.min(latest, nextShotEnd - MIN_SHOT_SECONDS - end);
  }
  if (isFirst) earliest = Math.max(earliest, MIN_SHOT_SECONDS - end);        // only its end can move
  if (isLast) latest = Math.min(latest, songSeconds - MIN_SHOT_SECONDS - start); // only its start can move
  return [earliest, latest];
}

/**
 * Slide shot `index` by `shiftSeconds` (clamped to slideLimits), keeping its length; the
 * shots either side get longer or shorter. `original` is the cut list when the drag began,
 * so a drag is always computed from where it started. Returns a new list.
 */
export function slideShot(original, index, shiftSeconds, songSeconds) {
  const [earliest, latest] = slideLimits(original, index, songSeconds);
  const shift = Math.max(earliest, Math.min(latest, shiftSeconds));
  const next = original.map(cut => ({ ...cut }));
  if (index > 0) next[index] = { ...next[index], atSeconds: original[index].atSeconds + shift, by: "user" };
  if (index + 1 < next.length) next[index + 1] = { ...next[index + 1], atSeconds: original[index + 1].atSeconds + shift, by: "user" };
  return next;
}

/** Move the boundary `index` (> 0) to `seconds`, keeping both neighbours at least MIN_SHOT_SECONDS. */
export function moveCut(cuts, index, seconds, songSeconds) {
  if (index <= 0 || index >= cuts.length) return cuts;
  const earliest = cuts[index - 1].atSeconds + MIN_SHOT_SECONDS;
  const latest = shotEnd(cuts, index, songSeconds) - MIN_SHOT_SECONDS;
  const next = cuts.map(cut => ({ ...cut }));
  next[index] = { ...next[index], atSeconds: Math.max(earliest, Math.min(latest, seconds)), by: "user" };
  return next;
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
