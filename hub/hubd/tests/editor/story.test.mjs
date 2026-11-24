// Tests for the story's slice in the editor (story.js); the render does the same
// (hub/ingest/story_render.py, tests/test_story_render.py). Run: node --test hub/hubd/tests/editor/
import { test } from "node:test";
import assert from "node:assert/strict";
import { sliceLeftShare, sliceLeftAt, positionCentredAt, SLICE_SHARE, SLICE_WIDTH } from "../../static/editor/js/story.js";

const cuts = [{ atSeconds: 0, cameraId: "cam1" }, { atSeconds: 10, cameraId: "cam2" }];
const pixels = share => Math.round(share * 1920);

test("left, middle and right in whole 1080p pixels, as the render", () => {
  assert.equal(pixels(sliceLeftShare(0)), 0);
  assert.equal(pixels(sliceLeftShare(0.5)), 656);
  assert.equal(pixels(sliceLeftShare(1)), 1920 - SLICE_WIDTH);
  assert.equal(pixels(sliceLeftShare(3)), 1312);
});

test("each camera keeps its place; a camera without one is centred", () => {
  assert.equal(pixels(sliceLeftAt(cuts, { cam2: 1 }, 0.3, 5)), 656);
  assert.equal(pixels(sliceLeftAt(cuts, { cam2: 1 }, 0.3, 12)), 1312);
});

test("over the dissolve the slice glides, half way at the cut", () => {
  const framing = { cam1: 0, cam2: 1 };
  assert.equal(pixels(sliceLeftAt(cuts, framing, 0.4, 9.8)), 0);
  assert.equal(pixels(sliceLeftAt(cuts, framing, 0.4, 10)), 656);
  assert.equal(pixels(sliceLeftAt(cuts, framing, 0.4, 10.2)), 1312);
  const before = sliceLeftAt(cuts, framing, 0.4, 9.9), after = sliceLeftAt(cuts, framing, 0.4, 10.1);
  assert.ok(before > 0 && before < after && after < 1312 / 1920);
  assert.equal(pixels(sliceLeftAt(cuts, framing, 0, 9.99)), 0);          // no dissolve: it jumps
});

test("a press puts the slice's middle under it", () => {
  assert.equal(positionCentredAt(0.5), 0.5);
  assert.equal(positionCentredAt(0), 0);
  assert.equal(positionCentredAt(1 - SLICE_SHARE / 2), 1);
});

test("a dissolve cut holds the slice at the outgoing camera's place and moves the incoming picture", async () => {
  const { incomingShiftShare, cutsInStory } = await import("../../static/editor/js/story.js");
  const marked = [{ atSeconds: 0, cameraId: "cam1" }, { atSeconds: 10, cameraId: "cam2", storyDissolve: true }];
  const framing = { cam1: 0, cam2: 1 };
  for (const seconds of [9.85, 10, 10.15]) assert.equal(pixels(sliceLeftAt(marked, framing, 0.4, seconds)), 0);
  assert.equal(pixels(sliceLeftAt(marked, framing, 0.4, 10.25)), 1312);
  assert.equal(pixels(incomingShiftShare(marked[0], marked[1], framing)), -1312);   // as story_render.dissolve_shift
  assert.equal(incomingShiftShare(cuts[0], cuts[1], framing), 0);
  assert.deepEqual(cutsInStory(marked, 5, 20).map(c => c.index), [1]);
  assert.deepEqual(cutsInStory(marked, 11, 20), []);
});

test("the flag goes to the edit file and back", async () => {
  const { cutsToFile, cutsFromFile } = await import("../../static/editor/js/time-format.js");
  const file = cutsToFile([{ atSeconds: 0, cameraId: "cam1", by: "auto" }, { atSeconds: 2, cameraId: "cam2", by: "user", storyDissolve: true }]);
  assert.equal(file[0].story_dissolve, undefined);
  assert.equal(file[1].story_dissolve, true);
  assert.equal(cutsFromFile(file)[1].storyDissolve, true);
  assert.equal(cutsFromFile(file)[0].storyDissolve, undefined);
});
