// Tests for the editor's cut logic. Run: node --test hub/hubd/tests/editor/
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  chooseCameraForStretch, cutToCameraAt, slideShot, slideLimits, moveCut, removeCut, normalize,
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

test("sliding a shot keeps its length and stops at the neighbours' minimum", () => {
  assert.equal(summary(slideShot(start(), 1, 3, SONG)), "0:cam1 23:cam2 33:cam3 40:cam1");
  const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);
  const left = slideShot(start(), 1, -100, SONG);
  near(left[1].atSeconds, MIN_SHOT_SECONDS);
  const right = slideShot(start(), 1, 100, SONG);
  near(right[2].atSeconds, 40 - MIN_SHOT_SECONDS);
  near(right[2].atSeconds - right[1].atSeconds, 10);
});

test("the first and last shots only move their inner edge", () => {
  const [earliest] = slideLimits(start(), 0, SONG);
  assert.ok(Math.abs(earliest - (MIN_SHOT_SECONDS - 20)) < 1e-9);
  const moved = slideShot(start(), 3, 200, SONG);
  assert.ok(Math.abs(moved[3].atSeconds - (SONG - MIN_SHOT_SECONDS)) < 1e-9);
});

test("moving and removing cuts", () => {
  assert.equal(moveCut(start(), 1, 5, SONG)[1].atSeconds, 5);
  assert.ok(Math.abs(moveCut(start(), 1, 50, SONG)[1].atSeconds - (30 - MIN_SHOT_SECONDS)) < 1e-9);
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
