// Reading hub-ingest's take records (/api/ingest "takes"): which entries are cameras, how
// far each got, labels. Pure functions, shared by the archive and the view LEDs.

/** The ingest steps of one camera file, in order, with their names on the page. */
export const STEPS = [["pulled", "Pull"], ["checked", "Check"], ["conformed", "Convert"],
                      ["verified", "Verify"], ["written", "Copy to drive"], ["marked", "Mark on camera"]];

/** A take record's camera entries: Pi cameras (camN) and phones (their file ids). */
export function camerasOf(take) {
  return Object.entries(take).filter(([key, value]) =>
    key !== "recorded" && key !== "audio" && value && typeof value === "object" && !Array.isArray(value));
}

/** Nothing done or running yet: the take is still only on the cameras. */
export function notIngested(take) {
  return camerasOf(take).every(([, camera]) => !(camera.done || []).length && !camera.step && !camera.error);
}

export function isIngested(take) {
  const cameras = camerasOf(take);
  return take.complete || (cameras.length > 0 && cameras.every(([, camera]) => (camera.done || []).includes("marked")));
}

/** A camera's name as the user gave it (phones), else its id. */
export function labelOf(take, cameraId) {
  return (take[cameraId] && take[cameraId].label) ||
    (take.recorded && take.recorded.cameras && take.recorded.cameras[cameraId] && take.recorded.cameras[cameraId].label) || cameraId;
}
