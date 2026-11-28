// Camera moves: the preview's geometry (camera-move.js, the same numbers as the render's
// hub/ingest/tests/test_camera_move.py) and moves staying with their shots through the
// cut gestures (edit-model.js). Run: node --test hub/hubd/tests/editor/
import { test } from "node:test";
import assert from "node:assert/strict";
import { moveRectAt, movePath, cutMoveRectAt } from "../../static/editor/js/camera-move.js";
import { chooseCameraForStretch, slideShot, moveEdge, setShotMove } from "../../static/editor/js/edit-model.js";

const close = (got, want) => {
  for (const key of ["left", "top", "width", "height"]) assert.ok(Math.abs(got[key] - want[key]) < 1e-6, `${key}: ${got[key]} vs ${want[key]}`);
};
const centred = zoom => ({ left: 0.5 - 0.5 / zoom, top: 0.5 - 0.5 / zoom, width: 1 / zoom, height: 1 / zoom });

test("a push in runs over the whole visible shot, held outside it (as the render)", () => {
  const move = { kind: "in", amount: 0.5 };
  close(moveRectAt(move, 0, 10, 0.3, -0.15), centred(1));
  close(moveRectAt(move, 0, 10, 0.3, 10.15), centred(1.06));
  close(moveRectAt(move, 0, 10, 0.3, 5), centred(1.03));
  close(moveRectAt(move, 0, 10, 0.3, 12), centred(1.06));
});

test("a pull out is a push in backwards; a short shot moves less, not faster", () => {
  close(moveRectAt({ kind: "out", amount: 1 }, 2, 12, 0, 3), moveRectAt({ kind: "in", amount: 1 }, 2, 12, 0, 11));
  const [a, b] = movePath({ kind: "in", amount: 1 }, 2);
  assert.ok(Math.abs(b - a - 0.03) < 1e-9);
  const [c, d] = movePath({ kind: "in", amount: 1 }, 20);
  assert.ok(Math.abs(d - c - 0.12) < 1e-9);
});

test("a drift crosses the room at a constant zoom", () => {
  const size = 1 / 1.06;
  close(moveRectAt({ kind: "right", amount: 0.5 }, 0, 10, 0, 0), { left: 0, top: 0.5 - size / 2, width: size, height: size });
  close(moveRectAt({ kind: "right", amount: 0.5 }, 0, 10, 0, 10), { left: 1 - size, top: 0.5 - size / 2, width: size, height: size });
  assert.ok(Math.abs(moveRectAt({ kind: "left", amount: 0.5 }, 0, 10, 0, 0).left - (1 - size)) < 1e-6);
});

const SONG = 160;
const IN = { kind: "in", amount: 0.5 };
const start = () => [{ atSeconds: 0, cameraId: "cam1", by: "ai" }, { atSeconds: 20, cameraId: "cam2", by: "ai", move: IN },
                     { atSeconds: 30, cameraId: "cam3", by: "ai" }, { atSeconds: 40, cameraId: "cam1", by: "ai" }];
const moves = cuts => cuts.map(c => `${c.atSeconds}:${c.cameraId}${c.move ? ":" + c.move.kind : ""}`).join(" ");

test("a shot's move is found at its own time only", () => {
  assert.equal(cutMoveRectAt(start(), 0, SONG, 0.3, 5), null);
  close(cutMoveRectAt(start(), 1, SONG, 0, 30), centred(1.06));
});

test("a slid shot keeps its move", () => {
  assert.equal(moves(slideShot(start(), 1, 3, SONG).cuts), "0:cam1 23:cam2:in 33:cam3 40:cam1");
  assert.equal(moves(slideShot(start(), 2, -3, SONG).cuts), "0:cam1 20:cam2:in 27:cam3 37:cam1");
});

test("a shot whose edges are dragged keeps its move", () => {
  assert.equal(moves(moveEdge(start(), 2, 34, SONG).cuts), "0:cam1 20:cam2:in 34:cam3 40:cam1");
  assert.equal(moves(moveEdge(start(), 1, 15, SONG).cuts), "0:cam1 15:cam2:in 30:cam3 40:cam1");
});

test("another camera chosen inside a moving shot: both parts keep the move", () => {
  assert.equal(moves(chooseCameraForStretch(start(), "cam4", 24, 26, SONG)), "0:cam1 20:cam2:in 24:cam4 26:cam2:in 30:cam3 40:cam1");
});

test("setShotMove sets and clears one shot's move", () => {
  const set = setShotMove(start(), 0, { kind: "left", amount: 0.8 });
  assert.equal(moves(set), "0:cam1:left 20:cam2:in 30:cam3 40:cam1");
  assert.equal(moves(setShotMove(set, 1, null)), "0:cam1:left 20:cam2 30:cam3 40:cam1");
  assert.equal(start()[1].move, IN);                     // the original list is untouched
});
