// The title in the preview (title-layout.js) must sit where the render puts it
// (hub/ingest/title_render.py): same rows, sizes, baselines and fade.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { layout, opacityAt, isShown, TITLE_SIZE, logoStartSeconds, logoOpacityAt } from "../../static/editor/js/title/title-layout.js";

const here = dirname(fileURLToPath(import.meta.url));
const ingest = join(here, "../../../ingest");

const titles = [
  { on: true, text: "Brockley Live", lines: ["Anna Berg, piano", "", "  Ben Okafor, bass ", "Cleo, drums"], colour: "#ffffff", start_s: 2, duration_s: 6 },
  { on: true, text: "Only a title", lines: [], colour: "#ffffff", start_s: 0, duration_s: 3 },
  { on: true, text: "  ", lines: ["Just", "names"], colour: "#ffffff", start_s: 10.5, duration_s: 20 },
  { on: true, text: "Eight", lines: ["1", "2", "3", "4", "5", "6", "7", "8", "9"], colour: "#ffffff", start_s: 1, duration_s: 4 },
];

function fromPython(title, times) {
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(ingest)})
import title_render as t
title = json.loads(sys.argv[1]); times = json.loads(sys.argv[2])
start, end, fade = title["start_s"], title["start_s"] + title["duration_s"], t.FADE_S
rows = [{"text": r[0], "size": r[2], "baseline": r[3]} for r in t.layout(title)]
alpha = [min(1, max(0, min((s - start) / fade, (end - s) / fade))) for s in times]
print(json.dumps({"rows": rows, "alpha": alpha, "shown": t.is_shown(title), "logo": t.logo_start_s(title)}))`;
  return JSON.parse(execFileSync("python3", ["-c", script, JSON.stringify(title), JSON.stringify(times)]));
}

test("layout and fade match the render's", () => {
  for (const title of titles) {
    const times = [title.start_s - 1, title.start_s, title.start_s + 0.25, title.start_s + 3, title.start_s + title.duration_s - 0.1,
                   title.start_s + title.duration_s + 1];
    const python = fromPython(title, times);
    assert.deepEqual(layout(title).map(({ text, size, baseline }) => ({ text, size, baseline })), python.rows, title.text);
    assert.equal(isShown(title), python.shown);
    times.forEach((seconds, i) => assert.ok(Math.abs(opacityAt(title, seconds) - python.alpha[i]) < 1e-9, `${title.text} at ${seconds}`));
    assert.equal(logoStartSeconds(title), python.logo);
    assert.equal(logoStartSeconds({ ...title, on: false }), fromPython({ ...title, on: false }, []).logo);
  }
});

test("the block is centred top to bottom and the title comes first", () => {
  const rows = layout(titles[0]);
  assert.equal(rows[0].size, TITLE_SIZE);
  assert.equal(rows.length, 4);                       // the empty line is dropped
  const top = rows[0].baseline - 0.7 * TITLE_SIZE, bottom = rows[rows.length - 1].baseline;
  assert.ok(Math.abs((top + bottom) / 2 - 540) <= 1);
  assert.equal(layout(titles[3]).length, 9);          // the title and at most 8 lines
});

test("off, or nothing to show: no title", () => {
  assert.equal(isShown({ ...titles[0], on: false }), false);
  assert.equal(isShown({ ...titles[0], text: "", lines: [" "] }), false);
  assert.equal(opacityAt({ ...titles[0], on: false }, 4), 0);
});

test("the logo fades in after the title, or at 5 s", () => {
  assert.equal(logoStartSeconds(titles[0]), 2 + 6 + 1);           // gone at 8 s, logo from 9 s
  assert.equal(logoStartSeconds({ ...titles[0], on: false }), 5);
  assert.equal(logoOpacityAt(null, 4.9), 0);
  assert.equal(logoOpacityAt(null, 5.5), 0.5);
  assert.equal(logoOpacityAt(null, 600), 1);
});
