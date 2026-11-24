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

/** Cuts from the edit file (nanoseconds) to the editor's model (seconds). */
export function cutsFromFile(fileCuts) {
  return fileCuts.map(cut => ({ atSeconds: cut.at_ns / NS_PER_SECOND, cameraId: cut.camera, by: cut.by || "auto",
                                ...(cut.story_dissolve ? { storyDissolve: true } : {}) }));
}

/** Cuts from the editor's model back to the edit file's format. */
export function cutsToFile(cuts) {
  return cuts.map(cut => ({ at_ns: Math.round(cut.atSeconds * NS_PER_SECOND), camera: cut.cameraId, by: cut.by,
                            ...(cut.storyDissolve ? { story_dissolve: true } : {}) }));
}
