// The title sequence in the preview (title-layout.js) must match the render
// (hub/ingest/title_render.py): same sizes, positions, baselines, strip and timing. Letter
// widths come from the font in each (canvas, Pillow); here both get the same made-up widths.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  cardSize, cardBaseline, letterPositions, stripLayout, opacitiesAt, logoStartSeconds, logoOpacityAt, borderOpacityAt,
  isShown, titleText, linesBelow, CARD_TRACKING, CARD_SIZE,
} from "../../static/editor/js/title/title-layout.js";

const here = dirname(fileURLToPath(import.meta.url));
const ingest = join(here, "../../../ingest");
const advance = (letter, size) => (letter === " " ? 0.25 : letter === "W" ? 0.9 : 0.62) * size;

const titles = [
  { on: true, text: "Blue in Green", lines: ["Anna Berg, piano", "", "  Ben Okafor, bass ", "Cleo, drums"], colour: "#ffffff", duration_s: 6 },
  { on: true, text: "A very long title that will not fit at all WWW", lines: [], colour: "#e8b15a", duration_s: 3 },
  { on: true, text: "  ", lines: ["Just names"], colour: "#ffffff", duration_s: 20 },
];
const times = [-1, 0, 0.5, 2, 2.6, 3.1, 3.5, 5, 6.5, 7.4, 8.2, 9, 14.5, 15.2, 40];

function fromPython(title) {
  const script = `
import json, sys
sys.path.insert(0, ${JSON.stringify(ingest)})
import title_render as t
title = json.loads(sys.argv[1]); times = json.loads(sys.argv[2])
def advance(letter, size):
    return (0.25 if letter == " " else 0.9 if letter == "W" else 0.62) * size
text = t.title_text(title)
size = t.card_size(text, advance) if text else None
positions, width = t.letter_positions(text, size or t.CARD_SIZE, t.CARD_TRACKING, advance)
lines = t.lines_below(title)
print(json.dumps({"size": size, "xs": [x for _, x in positions], "width": width,
                  "baseline": t.card_baseline(size or t.CARD_SIZE),
                  "strip": t.strip_layout(lines) if lines else None,
                  "opacities": [t.opacities_at(title, s) for s in times],
                  "logo": t.logo_start_s(title), "logoOff": t.logo_start_s(dict(title, on=False)),
                  "shown": t.is_shown(title), "text": text, "lines": lines}))`;
  return JSON.parse(execFileSync("python3", ["-c", script, JSON.stringify(title), JSON.stringify(times)]));
}

test("layout, timing and the logo match the render's", () => {
  for (const title of titles) {
    const python = fromPython(title);
    const text = titleText(title);
    assert.equal(text, python.text);
    assert.deepEqual(linesBelow(title), python.lines);
    assert.equal(isShown(title), python.shown);
    if (text) {
      const size = cardSize(text, advance);
      assert.equal(size, python.size, title.text);
      const { positions, width } = letterPositions(text, size, CARD_TRACKING, advance);
      positions.forEach(({ x }, i) => assert.ok(Math.abs(x - python.xs[i]) < 1e-9));
      assert.ok(Math.abs(width - python.width) < 1e-9);
      assert.equal(cardBaseline(size), python.baseline);
    }
    if (python.strip) {
      const { top, baselines } = stripLayout(linesBelow(title));
      assert.deepEqual([top, baselines], python.strip);
    }
    times.forEach((t, i) => {
      const ours = opacitiesAt(title, t);
      for (const layer of ["black", "brand", "title", "strip"]) {
        assert.ok(Math.abs(ours[layer] - python.opacities[i][layer]) < 1e-9, `${title.text} ${layer} at ${t}`);
      }
    });
    assert.equal(logoStartSeconds(title), python.logo);
    assert.equal(logoStartSeconds({ ...title, on: false }), python.logoOff);
  }
});

test("a long title gets smaller until it fits; a normal one is 83 px", () => {
  assert.equal(cardSize("BLUE IN GREEN", advance), CARD_SIZE);
  const long = titleText(titles[1]);
  const size = cardSize(long, advance);
  assert.ok(size < CARD_SIZE);
  assert.ok(letterPositions(long, size, CARD_TRACKING, advance).width <= 1632);
});

test("the sequence: black, the brand, black, the title, the video, the strip", () => {
  const at = t => opacitiesAt(titles[0], t);
  assert.deepEqual(at(0), { black: 1, brand: 0, title: 0, strip: 0 });
  assert.equal(at(1).brand, 1);
  assert.equal(at(2.2).brand, 1);
  assert.ok(at(2.6).brand > 0 && at(2.6).brand < 1 && at(2.6).title === 0);   // fading out alone
  assert.deepEqual([at(3.1).brand, at(3.1).title], [0, 0]);                     // 0.2 s of black
  assert.ok(at(3.5).title > 0 && at(3.5).title < 1);                            // then the title fades in
  assert.equal(at(5).title, 1);
  assert.equal(at(6.95).title, 0);
  assert.equal(at(6.95).black, 1);
  assert.equal(at(8.5).black, 0);
  assert.equal(at(9).strip, 1);
  assert.equal(at(7.95 + 0.7 + 6 + 0.7 + 0.1).strip, 0);
  assert.deepEqual(opacitiesAt({ ...titles[0], on: false }, 2), { black: 0, brand: 0, title: 0, strip: 0 });
});

test("border and logo: 2 s after the strip, or after the video comes in; without the sequence the logo at 5 s", () => {
  assert.ok(Math.abs(logoStartSeconds(titles[0]) - (7.95 + 0.7 + 6 + 0.7 + 2)) < 1e-9);
  assert.ok(Math.abs(logoStartSeconds({ ...titles[0], lines: [] }) - (6.95 + 1.5 + 2)) < 1e-9);
  const start = logoStartSeconds(titles[0]);
  assert.equal(borderOpacityAt(titles[0], start - 0.1), 0);
  assert.ok(Math.abs(borderOpacityAt(titles[0], start + 0.5) - 0.5) < 1e-9);
  assert.equal(borderOpacityAt({ ...titles[0], on: false }, 0), 1);              // no sequence: from the start
  assert.equal(logoStartSeconds(null, 20), 25);
  assert.equal(logoOpacityAt(null, 5.5), 0.5);
  assert.equal(logoOpacityAt(null, 600), 1);
});
