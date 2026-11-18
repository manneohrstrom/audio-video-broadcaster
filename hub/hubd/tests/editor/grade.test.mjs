// The editor's colour grade (grade.js) must give the same colours as the render's
// (hub/ingest/grade.py): the preview has to match the finished video.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { gradeWithStrength, buildLut, LUT_SIZE, lookColour, adjustmentSettings } from "../../static/editor/js/look/grade.js";

const here = dirname(fileURLToPath(import.meta.url));
const looksPath = join(here, "../../static/editor/looks.json");
const looks = JSON.parse(readFileSync(looksPath, "utf8"));

test("every filter gives the same colours in JavaScript and Python", () => {
  const vectors = JSON.parse(execFileSync("python3", [join(here, "../../../ingest/grade.py"), "--vectors", looksPath]));
  const byId = Object.fromEntries(looks.filters.map(f => [f.id, f.grade]));
  assert.ok(vectors.length >= looks.filters.length * 12);
  for (const v of vectors) {
    const got = v.sliders
      ? lookColour(v.in, adjustmentSettings(v.sliders, looks.adjustments), byId[v.filter], v.strength, v.pop || 0)
      : gradeWithStrength(v.in, byId[v.filter], v.strength);
    got.forEach((value, i) => assert.ok(Math.abs(value - v.out[i]) < 1e-5, `${v.filter} ${v.strength} ${v.in}: ${got} vs ${v.out}`));
  }
});

test("Normal at full strength changes nothing; strength 0 changes nothing", () => {
  for (const colour of [[0.2, 0.5, 0.9], [1, 0, 0.3]]) {
    assert.deepEqual(gradeWithStrength(colour, {}, 1).map(v => +v.toFixed(9)), colour);
    assert.deepEqual(gradeWithStrength(colour, { contrast: 2, mono: true }, 0), colour);
  }
});

test("the LUT has the right size and identity corners", () => {
  const lut = buildLut({}, 1);
  assert.equal(lut.length, LUT_SIZE ** 3 * 4);
  assert.deepEqual([...lut.slice(0, 4)], [0, 0, 0, 255]);
  assert.deepEqual([...lut.slice(-4)], [255, 255, 255, 255]);
});
