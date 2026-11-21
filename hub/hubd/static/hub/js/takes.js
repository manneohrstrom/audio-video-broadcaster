// Reading hub-ingest's take records (/api/ingest "takes"): which entries are cameras, how
// far each got, labels. Pure functions, shared by the archive and the view LEDs.
import { givenCameraName } from "./names.js";

/** The ingest steps of one camera file, in order, with their names on the page. */
export const STEPS = [["pulled", "Pull"], ["checked", "Check"], ["conformed", "Convert"],
                      ["verified", "Verify"], ["written", "Copy to drive"], ["marked", "Mark on camera"]];

/** After those, on the hub: what the editor needs from each camera (hub-ingest's "prep"),
 *  keyed as hub-ingest's activity kinds. */
export const PREP_STEPS = [["thumbnails", "Thumbnails"], ["proxy", "Preview copy"], ["motion", "Movement"]];

/** One camera's prep steps as { step: { state, error, progress, phase } }: hub-ingest's record,
 *  with the one it's working on now marked "running". Empty while the take is being ingested. */
export function prepOf(camera, takeId, cameraId, activity) {
  const prep = (camera && camera.prep) || {};
  const working = activity && activity.take === takeId && activity.camera === cameraId ? activity.kind : null;
  return Object.fromEntries(PREP_STEPS.filter(([step]) => prep[step]).map(([step]) => [step,
    step === working ? { state: "running", progress: activity.progress, phase: activity.phase } : prep[step]]));
}

/** Prep still to do or failed: the camera's steps stay on its take card until it's done. */
export function prepUnfinished(prep) {
  return Object.values(prep).some(step => step.state === "waiting" || step.state === "running" || step.state === "failed");
}

/** A take record's camera entries: Pi cameras (camN) and phones (their file ids). */
export function camerasOf(take) {
  return Object.entries(take).filter(([key, value]) =>
    key !== "recorded" && key !== "audio" && value && typeof value === "object" && !Array.isArray(value));
}

/** Is this camera entry a phone? (Pi cameras are camN; hubd marks phones in its record.) */
export function isPhone(take, cameraId) {
  const recorded = take.recorded && take.recorded.cameras && take.recorded.cameras[cameraId];
  return !!(recorded && recorded.phone) || !/^cam\d+$/.test(cameraId);
}

/** Every camera the take recorded, in ingest's order, then the ones ingest hasn't picked up
 *  yet (a phone still sending, or one that finished after this round began: user 2026-11-21,
 *  "everything that is part of the engine should be represented in the UI"). Those come as
 *  [id, { notYet: true, uploading }]. */
export function allCamerasOf(take) {
  const known = camerasOf(take);
  const have = new Set(known.map(([id]) => id));
  const recorded = (take.recorded && take.recorded.cameras) || {};
  const later = Object.entries(recorded).filter(([id]) => !have.has(id))
    .map(([id, r]) => [id, { notYet: true, uploading: !!r.uploading }]);
  return known.concat(later);
}

/** Nothing done or running yet: the take is still only on the cameras. */
export function notIngested(take) {
  return camerasOf(take).every(([, camera]) => !(camera.done || []).length && !camera.step && !camera.error);
}

export function isIngested(take) {
  const cameras = camerasOf(take);
  return take.complete || (cameras.length > 0 && cameras.every(([, camera]) => (camera.done || []).includes("marked")));
}

/** A camera's name: the one given on the hub page, else a phone's own label, else its id. */
export function labelOf(take, cameraId, names) {
  return givenCameraName(names, cameraId) || (take[cameraId] && take[cameraId].label) ||
    (take.recorded && take.recorded.cameras && take.recorded.cameras[cameraId] && take.recorded.cameras[cameraId].label) || cameraId;
}
