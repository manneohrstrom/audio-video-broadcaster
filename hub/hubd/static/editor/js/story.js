// The Instagram story (user, 2026-11-24): a 9:16 slice of the finished video, from its own
// start to its own end. Where the slice sits is set per camera (0 at the left edge, 1 at the
// right edge); at a cut it glides from one camera's place to the next one's over the dissolve,
// with the dissolve's curve (a cut marked storyGlide); or, by default (user, 2026-11-24:
// "sometimes you want a dissolve instead of a move", then "default to the dissolve mode"), it
// stays at the outgoing camera's place while the incoming picture is shifted so its slice
// lands there, and the two slices cross-fade.
// The same maths as the render: hub/ingest/story_render.py.
import { cutIndexAt } from "./edit-model.js";

export const SOURCE_WIDTH = 1920;
/** 1080 x 9 / 16 = 607.5 pixels of the 1080p frame, rounded to even as in the render. */
export const SLICE_WIDTH = 608;
/** The slice's width as a share of the frame's. */
export const SLICE_SHARE = SLICE_WIDTH / SOURCE_WIDTH;
export const CENTRE = 0.5;
/** A new story: this long from the video's start (as story_render.DEFAULT_LENGTH_NS). */
export const DEFAULT_STORY_SECONDS = 30;
/** Instagram plays a story clip up to this long and splits a longer one into parts. */
export const INSTAGRAM_CLIP_SECONDS = 60;

/** The slice's left edge as a share of the frame's width (in whole 1080p pixels, as rendered). */
export function sliceLeftShare(position) {
  const clamped = Math.min(1, Math.max(0, position));
  return Math.round(clamped * (SOURCE_WIDTH - SLICE_WIDTH)) / SOURCE_WIDTH;
}

/** The position (0..1) that puts the slice's middle at `share` of the frame's width. */
export function positionCentredAt(share) {
  return Math.min(1, Math.max(0, (share - SLICE_SHARE / 2) / (1 - SLICE_SHARE)));
}

function smoothstep(t) {
  const clamped = Math.min(1, Math.max(0, t));
  return clamped * clamped * (3 - 2 * clamped);
}

/**
 * The slice's left edge (share of the width) at a song time: the camera's own place, and
 * during the dissolve around a cut a glide between the two cameras' places.
 * @param {{ cameraId: string, atSeconds: number }[]} cuts
 * @param {Record<string, number>} framing  camera -> 0..1 (missing: centred)
 */
export function sliceLeftAt(cuts, framing, dissolveSeconds, seconds) {
  const place = cut => sliceLeftShare(framing[cut.cameraId] ?? CENTRE);
  const index = cutIndexAt(cuts, seconds);
  const half = dissolveSeconds / 2;
  let from = null, to = null, cutAt = 0;
  if (half > 0 && index > 0 && seconds - cuts[index].atSeconds < half) {
    from = cuts[index - 1]; to = cuts[index]; cutAt = cuts[index].atSeconds;
  } else if (half > 0 && cuts[index + 1] && cuts[index + 1].atSeconds - seconds < half) {
    from = cuts[index]; to = cuts[index + 1]; cutAt = cuts[index + 1].atSeconds;
  }
  if (!from) return place(cuts[index]);
  if (!to.storyGlide) return place(from);
  const mix = smoothstep(0.5 + (seconds - cutAt) / dissolveSeconds);
  return place(from) * (1 - mix) + place(to) * mix;
}

/**
 * At a dissolve cut: how far to move the incoming camera's picture (share of the width) so its
 * slice lands on the outgoing camera's; 0 for a gliding cut.
 */
export function incomingShiftShare(fromCut, toCut, framing) {
  if (toCut.storyGlide) return 0;
  return sliceLeftShare(framing[fromCut.cameraId] ?? CENTRE) - sliceLeftShare(framing[toCut.cameraId] ?? CENTRE);
}

/** The cuts inside the story (not its first shot): { index, cut, from } for each. */
export function cutsInStory(cuts, startSeconds, endSeconds) {
  return cuts.map((cut, index) => ({ index, cut, from: cuts[index - 1] }))
    .filter(({ index, cut }) => index > 0 && cut.atSeconds > startSeconds && cut.atSeconds < endSeconds);
}
