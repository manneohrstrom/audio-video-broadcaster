// Time helpers. The hub works in nanoseconds on its own clock; the editor works in
// seconds from the start of the song's range.

export const NS_PER_SECOND = 1e9;

/** "1:23.4" */
export function formatSeconds(seconds) {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = safe - minutes * 60;
  return `${minutes}:${rest.toFixed(1).padStart(4, "0")}`;
}

/** Cuts from the edit file (nanoseconds) to the editor's model (seconds). An AI cut's
 *  cuts also carry the model's reason and the hub's note (what it changed; ai_cut_place.py);
 *  any cut may carry its shot's camera move (camera-move.js).
 *  offsetNs: added to every time (a run's cuts made against an older song range). */
export function cutsFromFile(fileCuts, offsetNs = 0) {
  return fileCuts.map(cut => ({ atSeconds: (cut.at_ns + offsetNs) / NS_PER_SECOND, cameraId: cut.camera, by: cut.by || "auto",
                                ...(cut.story_glide ? { storyGlide: true } : {}),
                                ...(cut.move ? { move: { kind: cut.move.kind, amount: cut.move.amount } } : {}),
                                ...(cut.reason ? { reason: cut.reason } : {}), ...(cut.note ? { note: cut.note } : {}) }));
}

/** Cuts from the editor's model back to the edit file's format. */
export function cutsToFile(cuts) {
  return cuts.map(cut => ({ at_ns: Math.round(cut.atSeconds * NS_PER_SECOND), camera: cut.cameraId, by: cut.by,
                            ...(cut.storyGlide ? { story_glide: true } : {}),
                            ...(cut.move ? { move: { kind: cut.move.kind, amount: cut.move.amount } } : {}),
                            ...(cut.reason ? { reason: cut.reason } : {}), ...(cut.note ? { note: cut.note } : {}) }));
}
