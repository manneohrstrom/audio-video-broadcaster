// The colour grade behind every Filter, in JavaScript (for the editor's preview).
//
// THE SAME MATHS IS IN hub/ingest/grade.py (for the render): change both together. A
// shared test (hub/hubd/tests/editor/grade.test.mjs against vectors made by grade.py)
// checks they give the same colours.
//
// Colours are r, g, b in 0..1 (display, gamma-encoded, as in the video). Steps, in order:
//   exposure (stops) -> temperature (warm +, cool -) and tint (magenta +, green -)
//   -> contrast around mid-grey 0.5 -> highlight and shadow lift (tone)
//   -> saturation (or mono) -> shadows/highlights colour -> split tone -> fade (lifts the
//   blacks) -> clamp.
// A look is: the Adjust sliders (primary correction) first, then the filter, whose
// strength blends between its input (0) and the filtered colour (1), then the colour pop
// effect: lookColour().

export const LUT_SIZE = 33;

const LUMA = [0.2126, 0.7152, 0.0722];      // Rec. 709, as the cameras' video

function clamp01(value) {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

// ---- CSS filters and blended colour layers (W3C Filter Effects and Compositing specs), the
// model of the Instagram-style filters (after CSSgram, MIT). Each filter function clamps.
const matrix = (c, m) => [0, 1, 2].map(i => clamp01(m[i][0] * c[0] + m[i][1] * c[1] + m[i][2] * c[2]));

function cssFilter(c, name, amount) {
  if (name === "brightness") return c.map(v => clamp01(v * amount));
  if (name === "contrast") return c.map(v => clamp01((v - 0.5) * amount + 0.5));
  if (name === "saturate") {
    const s = amount;
    return matrix(c, [[0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
                      [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
                      [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]]);
  }
  if (name === "grayscale") {
    const g = 1 - Math.min(1, amount);
    return matrix(c, [[0.2126 + 0.7874 * g, 0.7152 - 0.7152 * g, 0.0722 - 0.0722 * g],
                      [0.2126 - 0.2126 * g, 0.7152 + 0.2848 * g, 0.0722 - 0.0722 * g],
                      [0.2126 - 0.2126 * g, 0.7152 - 0.7152 * g, 0.0722 + 0.9278 * g]]);
  }
  if (name === "sepia") {
    const g = 1 - Math.min(1, amount);
    return matrix(c, [[0.393 + 0.607 * g, 0.769 - 0.769 * g, 0.189 - 0.189 * g],
                      [0.349 - 0.349 * g, 0.686 + 0.314 * g, 0.168 - 0.168 * g],
                      [0.272 - 0.272 * g, 0.534 - 0.534 * g, 0.131 + 0.869 * g]]);
  }
  if (name === "hue-rotate") {
    const a = amount * Math.PI / 180, cs = Math.cos(a), sn = Math.sin(a);
    return matrix(c, [[0.213 + cs * 0.787 - sn * 0.213, 0.715 - cs * 0.715 - sn * 0.715, 0.072 - cs * 0.072 + sn * 0.928],
                      [0.213 - cs * 0.213 + sn * 0.143, 0.715 + cs * 0.285 + sn * 0.140, 0.072 - cs * 0.072 - sn * 0.283],
                      [0.213 - cs * 0.213 - sn * 0.787, 0.715 - cs * 0.715 + sn * 0.715, 0.072 + cs * 0.928 + sn * 0.072]]);
  }
  throw new Error(`unknown CSS filter ${name}`);
}

const hardLight = (b, s) => (s <= 0.5 ? b * 2 * s : b + (2 * s - 1) - b * (2 * s - 1));
function softLight(b, s) {
  if (s <= 0.5) return b - (1 - 2 * s) * b * (1 - b);
  const d = b <= 0.25 ? ((16 * b - 12) * b + 4) * b : Math.sqrt(b);
  return b + (2 * s - 1) * (d - b);
}
const SEPARABLE = {
  normal: (b, s) => s,
  multiply: (b, s) => b * s,
  screen: (b, s) => b + s - b * s,
  overlay: (b, s) => hardLight(s, b),
  darken: Math.min,
  lighten: Math.max,
  "color-dodge": (b, s) => (b === 0 ? 0 : s >= 1 ? 1 : Math.min(1, b / (1 - s))),
  "color-burn": (b, s) => (b >= 1 ? 1 : s <= 0 ? 0 : 1 - Math.min(1, (1 - b) / s)),
  "soft-light": softLight,
  "hard-light": hardLight,
  exclusion: (b, s) => b + s - 2 * b * s,
  difference: (b, s) => Math.abs(b - s),
};
const lum = c => 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2];
function setLum(c, l) {
  const d = l - lum(c);
  c = c.map(v => v + d);
  const L = lum(c), lo = Math.min(...c), hi = Math.max(...c);
  if (lo < 0) c = c.map(v => L + (v - L) * L / (L - lo));
  if (hi > 1) c = c.map(v => L + (v - L) * (1 - L) / (hi - L));
  return c;
}
function blendLayer(backdrop, layer) {
  const mode = layer.mode || "normal";
  const alpha = (layer.alpha ?? 1) * (layer.opacity ?? 1);
  const mixed = mode === "color" ? setLum(layer.color, lum(backdrop))
    : backdrop.map((b, i) => SEPARABLE[mode](b, layer.color[i]));
  return backdrop.map((b, i) => clamp01((1 - alpha) * b + alpha * mixed[i]));
}
function cssLook(colour, settings) {
  for (const [name, amount] of settings.css || []) colour = cssFilter(colour, name, amount);
  for (const layer of settings.layers || []) colour = blendLayer(colour, layer);
  return colour;
}

/** The graded colour of [r, g, b] with these settings (see looks.json "grade"). */
export function gradeColour(colour, settings) {
  if (settings.css || settings.layers) colour = cssLook([...colour], settings);
  let [r, g, b] = colour;
  const {
    exposure = 0, temperature = 0, tint = 0, contrast = 1, saturation = 1,
    mono = false, shadows = [1, 1, 1], highlights = [1, 1, 1], fade = 0,
    highlight_lift: highlightLift = 0, shadow_lift: shadowLift = 0, split_tone: split = 0,
  } = settings;
  const gain = Math.pow(2, exposure);
  r *= gain; g *= gain; b *= gain;
  r *= 1 + 0.1 * temperature; b *= 1 - 0.1 * temperature;
  g *= 1 - 0.08 * tint;
  r = (r - 0.5) * contrast + 0.5;
  g = (g - 0.5) * contrast + 0.5;
  b = (b - 0.5) * contrast + 0.5;
  // Tone: raise or lower the bright end (highlights) and the dark end (shadows) separately.
  const tone = c => {
    const x = clamp01(c);
    return c + highlightLift * 0.25 * x * x * x + shadowLift * 0.25 * (1 - x) * (1 - x) * (1 - x);
  };
  r = tone(r); g = tone(g); b = tone(b);
  const luma = LUMA[0] * r + LUMA[1] * g + LUMA[2] * b;
  const keep = mono ? 0 : saturation;
  r = luma + (r - luma) * keep;
  g = luma + (g - luma) * keep;
  b = luma + (b - luma) * keep;
  const light = clamp01(luma);                 // 0 = shadow, 1 = highlight
  r *= shadows[0] * (1 - light) + highlights[0] * light;
  g *= shadows[1] * (1 - light) + highlights[1] * light;
  b *= shadows[2] * (1 - light) + highlights[2] * light;
  if (split) {          // split tone: teal shadows and warm highlights (negative: the other way round)
    r *= 1 + split * (0.12 * light - 0.10 * (1 - light));
    g *= 1 + split * (0.02 * light + 0.02 * (1 - light));
    b *= 1 + split * (-0.14 * light + 0.12 * (1 - light));
  }
  r = fade + r * (1 - fade);
  g = fade + g * (1 - fade);
  b = fade + b * (1 - fade);
  return [clamp01(r), clamp01(g), clamp01(b)];
}

/** Grade settings from the Adjust sliders ({ id: -1..1 }), as defined in looks.json. */
export function adjustmentSettings(sliders, definitions) {
  const settings = {};
  for (const definition of definitions || []) {
    let value = sliders?.[definition.id] || 0;
    if (definition.positive_only) value = Math.max(0, value);
    settings[definition.setting] = definition.base + value * definition.scale;
  }
  return settings;
}

const POP_KEY = [1.0, 0.62, 0.25];     // colour pop keeps colours near this warm orange (lamp light)

/** The Colour pop effect: colours far from POP_KEY lose their colour, by `amount` (0..1);
 * near it (within 0.25, fading out over the next 0.3) they keep it. */
export function colourPop(colour, amount) {
  if (!amount) return [...colour];
  const distance = Math.sqrt(colour.reduce((sum, c, i) => sum + (c - POP_KEY[i]) ** 2, 0) / 3);
  const drain = amount * clamp01((distance - 0.25) / 0.3);
  const grey = LUMA[0] * colour[0] + LUMA[1] * colour[1] + LUMA[2] * colour[2];
  return colour.map(c => c + (grey - c) * drain);
}

/** The whole look on one colour: Adjust first, then the filter at its strength, then colour pop. */
export function lookColour(colour, adjust, filter, strength, pop = 0) {
  return colourPop(gradeWithStrength(gradeColour(colour, adjust), filter, strength), pop);
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
export function buildLut(settings, strength, size = LUT_SIZE, adjust = {}, pop = 0) {
  const table = new Uint8Array(size * size * size * 4);
  let i = 0;
  for (let bi = 0; bi < size; bi++) {
    for (let gi = 0; gi < size; gi++) {
      for (let ri = 0; ri < size; ri++) {
        const [r, g, b] = lookColour([ri / (size - 1), gi / (size - 1), bi / (size - 1)], adjust, settings, strength, pop);
        table[i++] = Math.round(r * 255);
        table[i++] = Math.round(g * 255);
        table[i++] = Math.round(b * 255);
        table[i++] = 255;
      }
    }
  }
  return table;
}
