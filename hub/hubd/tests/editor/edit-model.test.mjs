// Tests for the editor's cut logic. Run: node --test hub/hubd/tests/editor/
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  chooseCameraForStretch, cutToCameraAt, slideShot, moveEdge, removeCut, normalize,
  cameraAt, History, MIN_SHOT_SECONDS,
} from "../../static/editor/js/edit-model.js";

const SONG = 160;
const cut = (atSeconds, cameraId) => ({ atSeconds, cameraId, by: "auto" });
const summary = cuts => cuts.map(c => `${c.atSeconds}:${c.cameraId}`).join(" ");
const start = () => [cut(0, "cam1"), cut(20, "cam2"), cut(30, "cam3"), cut(40, "cam1")];

test("choosing a stretch inside a shot brings the old camera back after it", () => {
  assert.equal(summary(chooseCameraForStretch(start(), "cam4", 10, 15, SONG)), "0:cam1 10:cam4 15:cam1 20:cam2 30:cam3 40:cam1");
});

test("choosing a stretch across cuts replaces what was there", () => {
  assert.equal(summary(chooseCameraForStretch(start(), "cam4", 18, 35, SONG)), "0:cam1 18:cam4 35:cam3 40:cam1");
});

test("choosing the camera that is already on changes nothing", () => {
  assert.equal(summary(chooseCameraForStretch(start(), "cam1", 2, 8, SONG)), summary(start()));
});

test("a stretch to the end of the song has no cut after it", () => {
  assert.equal(summary(chooseCameraForStretch(start(), "cam3", 35, SONG, SONG)), "0:cam1 20:cam2 30:cam3");
});

test("cutting from the playhead holds until the next cut", () => {
  assert.equal(summary(cutToCameraAt(start(), "cam4", 25)), "0:cam1 20:cam2 25:cam4 30:cam3 40:cam1");
});

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

test("sliding a shot keeps its length; the neighbour it leaves fills the space", () => {
  assert.equal(summary(slideShot(start(), 1, 3, SONG).cuts), "0:cam1 23:cam2 33:cam3 40:cam1");
  assert.equal(summary(slideShot(start(), 1, -3, SONG).cuts), "0:cam1 17:cam2 27:cam3 40:cam1");
});

test("a slid shot runs over the shots in its way", () => {
  // cam2 (20-30) slid 15 s later: cam1 fills where it was, it runs over half of cam3 and
  // into cam1, which comes back after it.
  const right = slideShot(start(), 1, 15, SONG);
  assert.equal(summary(right.cuts), "0:cam1 30:cam3 35:cam2 45:cam1");
  assert.equal(summary(slideShot(start(), 1, 25, SONG).cuts), "0:cam1 30:cam3 40:cam1 45:cam2 55:cam1");
  near(right.start, 35);
  near(right.end, 45);
  // cam3 (30-40) slid to the very start: it becomes the first shot, and cam1 fills its old place.
  assert.equal(summary(slideShot(start(), 2, -100, SONG).cuts), "0:cam3 10:cam1 20:cam2 30:cam1");
});

test("a slid shot meeting its own camera becomes one shot with it", () => {
  // The cam1 shot at 30-40 slid 10 s earlier, over cam2, onto the end of the cam1 shot at 0-20.
  const cuts = [cut(0, "cam1"), cut(20, "cam2"), cut(30, "cam1"), cut(40, "cam3")];
  assert.equal(summary(slideShot(cuts, 2, -10, SONG).cuts), "0:cam1 30:cam3");
});

test("leftovers shorter than the minimum are swallowed", () => {
  // cam2 (20-30) slid so that only 0.1 s of cam3 would be left before cam1 at 40.
  const result = slideShot(start(), 1, 9.9, SONG);
  assert.equal(summary(result.cuts), "0:cam1 29.9:cam2 40:cam1");
});

test("the first and last shots move their inner edge, as far as they're dragged", () => {
  assert.equal(summary(slideShot(start(), 0, 15, SONG).cuts), "0:cam1 35:cam3 40:cam1");
  // The last shot (cam1 from 40) slid back to 15: over cam3 and cam2, onto the first cam1 shot.
  assert.equal(summary(slideShot(start(), 3, -25, SONG).cuts), "0:cam1");
});

test("an edge drags as far as wanted, over the neighbours", () => {
  // The end of cam2 (cut 2 at 30) dragged to 50: cam2 runs over cam3 and into cam1.
  const later = moveEdge(start(), 2, 50, SONG, "before");
  assert.equal(summary(later.cuts), "0:cam1 20:cam2 50:cam1");
  near(later.edgeSeconds, 50);
  // The start of cam3 (cut 2 at 30) dragged to 5: cam3 runs over cam2 and most of cam1.
  const earlier = moveEdge(start(), 2, 5, SONG, "after");
  assert.equal(summary(earlier.cuts), "0:cam1 5:cam3 40:cam1");
  // Dragged into the end of the song: no cut left after it.
  assert.equal(summary(moveEdge(start(), 3, SONG + 5, SONG, "before").cuts), "0:cam1 20:cam2 30:cam3");
});

test("the held shot doesn't shrink away; a cut handle may swallow either side", () => {
  const held = moveEdge(start(), 2, 0, SONG, "before");      // the end of cam2 (20-30) dragged back to 0
  near(held.edgeSeconds, 20 + MIN_SHOT_SECONDS);
  const handle = moveEdge(start(), 2, 0, SONG, null);        // the cut handle at 30 dragged to 0
  assert.equal(summary(normalize(handle.cuts)), "0:cam3 40:cam1");
  assert.equal(moveEdge(start(), 0, 5, SONG), null);
});

test("removing cuts", () => {
  assert.equal(summary(removeCut(start(), 2)), "0:cam1 20:cam2 40:cam1");
  assert.equal(summary(removeCut(start(), 0)), summary(start()));
});

test("normalize merges repeats and pins the first cut to 0", () => {
  assert.equal(summary(normalize([cut(3, "cam1"), cut(10, "cam1"), cut(12, "cam2")])), "0:cam1 12:cam2");
});

test("cameraAt", () => {
  assert.equal(cameraAt(start(), 0), "cam1");
  assert.equal(cameraAt(start(), 25), "cam2");
  assert.equal(cameraAt(start(), 99), "cam1");
});

test("history: undo, redo, and a new change clears redo", () => {
  const history = new History();
  let cuts = start();
  history.record(cuts);
  cuts = removeCut(cuts, 1);
  cuts = history.undo(cuts);
  assert.equal(summary(cuts), summary(start()));
  cuts = history.redo(cuts);
  assert.equal(cuts.length, 3);
  history.record(cuts);
  assert.equal(history.canRedo, false);
});

// The AI cut's reasons and the hub's notes travel with the cuts (time-format.js).
import { cutsFromFile as aiCutsFromFile, cutsToFile as aiCutsToFile } from "../../static/editor/js/time-format.js";

test("an AI cut's reason and note survive loading and saving, and a run's cuts move onto a newer range", () => {
  const file = [{ at_ns: 0, camera: "cam1", by: "ai", reason: "head, whole band" },
                { at_ns: 4_000_000_000, camera: "cam2", by: "ai", reason: "sax solo", note: "moved -0.3 s onto the bar" }];
  const cuts = aiCutsFromFile(file);
  assert.deepEqual(cuts[1], { atSeconds: 4, cameraId: "cam2", by: "ai", reason: "sax solo", note: "moved -0.3 s onto the bar" });
  assert.deepEqual(aiCutsToFile(cuts), file);
  assert.equal(aiCutsFromFile(file, -1_000_000_000)[1].atSeconds, 3);
});
