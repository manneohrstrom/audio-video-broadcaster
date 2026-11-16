// The colour grade behind every Filter, in JavaScript (for the editor's preview).
//
// THE SAME MATHS IS IN hub/ingest/grade.py (for the render): change both together. A
// shared test (hub/hubd/tests/editor/grade.test.mjs against vectors made by grade.py)
// checks they give the same colours.
//
// Colours are r, g, b in 0..1 (display, gamma-encoded, as in the video). Steps, in order:
//   exposure (stops) -> temperature (warm +, cool -) and tint (magenta +, green -)
//   -> contrast around mid-grey 0.5 -> saturation (or mono) -> shadows/highlights colour
//   -> fade (lifts the blacks) -> clamp to 0..1.
// The filter's strength then blends between the original (0) and the graded colour (1).

export const LUT_SIZE = 33;

const LUMA = [0.2126, 0.7152, 0.0722];      // Rec. 709, as the cameras' video

function clamp01(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

/** The graded colour of [r, g, b] with these settings (see looks.json "grade"). */
export function gradeColour([r, g, b], settings) {
  const {
    exposure = 0, temperature = 0, tint = 0, contrast = 1, saturation = 1,
    mono = false, shadows = [1, 1, 1], highlights = [1, 1, 1], fade = 0,
  } = settings;
  const gain = Math.pow(2, exposure);
  r *= gain; g *= gain; b *= gain;
  r *= 1 + 0.1 * temperature; b *= 1 - 0.1 * temperature;
  g *= 1 - 0.08 * tint;
  r = (r - 0.5) * contrast + 0.5;
  g = (g - 0.5) * contrast + 0.5;
  b = (b - 0.5) * contrast + 0.5;
  const luma = LUMA[0] * r + LUMA[1] * g + LUMA[2] * b;
  const keep = mono ? 0 : saturation;
  r = luma + (r - luma) * keep;
  g = luma + (g - luma) * keep;
  b = luma + (b - luma) * keep;
  const light = clamp01(luma);                 // 0 = shadow, 1 = highlight
  r *= shadows[0] * (1 - light) + highlights[0] * light;
  g *= shadows[1] * (1 - light) + highlights[1] * light;
  b *= shadows[2] * (1 - light) + highlights[2] * light;
  r = fade + r * (1 - fade);
  g = fade + g * (1 - fade);
  b = fade + b * (1 - fade);
  return [clamp01(r), clamp01(g), clamp01(b)];
}

/** gradeColour blended with the original by `strength` (0..1). */
export function gradeWithStrength(colour, settings, strength) {
  const graded = gradeColour(colour, settings);
  return colour.map((value, i) => value + (graded[i] - value) * strength);
}

/**
 * A LUT_SIZE^3 colour table as RGBA bytes (red fastest, then green, then blue), the
 * layout of a WebGL 3D texture and of a .cube file.
 */
export function buildLut(settings, strength, size = LUT_SIZE) {
  const table = new Uint8Array(size * size * size * 4);
  let i = 0;
  for (let bi = 0; bi < size; bi++) {
    for (let gi = 0; gi < size; gi++) {
      for (let ri = 0; ri < size; ri++) {
        const [r, g, b] = gradeWithStrength([ri / (size - 1), gi / (size - 1), bi / (size - 1)], settings, strength);
        table[i++] = Math.round(r * 255);
        table[i++] = Math.round(g * 255);
        table[i++] = Math.round(b * 255);
        table[i++] = 255;
      }
    }
  }
  return table;
}
