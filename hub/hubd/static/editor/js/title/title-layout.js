// The title sequence in the preview (user, 2026-11-21; docs/editor-plan.md section 5b), timed
// from the start of the video:
//   1. from black, "BROCKLEY LIVE" fades in (Outfit Bold, 83 px, letters 33 % apart, cream);
//   2. it dissolves into the song's title (the same style, in the title's colour);
//   3. the title fades out, the video fades in from black, and a strip along the bottom
//      (black at 50 %) shows the lines below the title in cream, smaller, as typed; after
//      "Names stay for" seconds it dissolves out.
// The render draws the same thing (hub/ingest/title_render.py) with the same font file and
// these same numbers: change them in both places. Coordinates are in the 1920x1080 picture;
// a baseline is the alphabetic one. Letters are placed one by one by their advances (no
// kerning), as the render does.

export const WIDTH = 1920;
export const HEIGHT = 1080;
export const FONT_FAMILY = "Outfit";
export const CREAM = "#f3ead8";
export const BRAND_TEXT = "BROCKLEY LIVE";

export const CARD_SIZE = 83;
export const CARD_TRACKING = 0.33;
export const CARD_MAX_WIDTH = 1632;
export const CAP_HEIGHT = 0.706;

export const DETAIL_SIZE = 38;
export const DETAIL_LINE_SPACING = 54;
export const STRIP_PADDING = 30;
export const STRIP_DESCENT = 10;
export const STRIP_ALPHA = 0.5;
export const DETAIL_MAX_WIDTH = 1760;

// The sequence, in seconds from the start of the video: [start, length].
export const BRAND_IN = [0, 1];
export const CARDS_DISSOLVE = [3.5, 1];
export const TITLE_OUT = [8, 1];
export const VIDEO_IN = [9, 1.5];
export const DETAILS_IN = 10;
export const DETAILS_FADE = 0.7;

// The Brockley Live logo (the border is on from the start) fades in shortly after the
// sequence, or at 5 s when there's no title (user, 2026-11-18). As title_render.logo_start_s.
export const LOGO_AFTER_TITLE_SECONDS = 1;
export const LOGO_WITHOUT_TITLE_SECONDS = 5;
export const LOGO_FADE_SECONDS = 1;
export const MAX_LINES = 8;
export const MAX_CHARACTERS = 80;

/** @typedef {{ on: boolean, text: string, lines: string[], colour: string, duration_s: number }} Title */
/** @typedef {(letter: string, size: number) => number} Advance  a letter's advance in px at a size */

/** The strip's lines: the non-empty lines under the title, trimmed. @param {Title} title */
export function linesBelow(title) {
  return (title.lines || []).map(line => line.trim()).filter(Boolean).slice(0, MAX_LINES);
}

/** Is the title sequence on? @param {Title | null} title */
export function isShown(title) {
  return !!title && title.on === true;
}

/** The title card's text: the title in capitals. @param {Title} title */
export function titleText(title) {
  return (title.text || "").trim().toUpperCase();
}

/** When the strip is gone (seconds into the video). @param {Title} title */
export function detailsEndSeconds(title) {
  return DETAILS_IN + DETAILS_FADE + (title.duration_s ?? 6) + DETAILS_FADE;
}

/**
 * When the logo starts fading in (song seconds); the video starts at `videoStartSeconds`.
 * @param {Title | null} title @param {number} [videoStartSeconds]
 */
export function logoStartSeconds(title, videoStartSeconds = 0) {
  if (!isShown(title)) return videoStartSeconds + LOGO_WITHOUT_TITLE_SECONDS;
  const end = linesBelow(title).length ? detailsEndSeconds(title) : VIDEO_IN[0] + VIDEO_IN[1];
  return videoStartSeconds + end + LOGO_AFTER_TITLE_SECONDS;
}

/**
 * How visible the logo is at song time `seconds`: 0, fading in, then 1 to the end.
 * @param {Title | null} title @param {number} seconds @param {number} [videoStartSeconds]
 */
export function logoOpacityAt(title, seconds, videoStartSeconds = 0) {
  return Math.min(1, Math.max(0, (seconds - logoStartSeconds(title, videoStartSeconds)) / LOGO_FADE_SECONDS));
}

const ramp = (t, start, length) => Math.min(1, Math.max(0, (t - start) / length));

/**
 * How visible each layer is at `t` seconds into the video (as title_render.opacities_at).
 * @param {Title | null} title @param {number} t
 */
export function opacitiesAt(title, t) {
  if (!isShown(title)) return { black: 0, brand: 0, title: 0, strip: 0 };
  const stripOut = detailsEndSeconds(title) - DETAILS_FADE;
  return {
    black: 1 - ramp(t, ...VIDEO_IN),
    brand: ramp(t, ...BRAND_IN) * (1 - ramp(t, ...CARDS_DISSOLVE)),
    title: titleText(title) ? ramp(t, ...CARDS_DISSOLVE) * (1 - ramp(t, ...TITLE_OUT)) : 0,
    strip: linesBelow(title).length ? ramp(t, DETAILS_IN, DETAILS_FADE) * (1 - ramp(t, stripOut, DETAILS_FADE)) : 0,
  };
}

// -- layout (as title_render)
/**
 * Each letter's x from the start of the row, and the row's width (no spacing after the last letter).
 * @param {string} text @param {number} size @param {number} tracking @param {Advance} advance
 */
export function letterPositions(text, size, tracking, advance) {
  const letters = [...text];
  const positions = [];
  let x = 0;
  letters.forEach((letter, index) => {
    positions.push({ letter, x });
    x += advance(letter, size) + (index + 1 < letters.length ? tracking * size : 0);
  });
  return { positions, width: x };
}

/** The card's size: CARD_SIZE, smaller (whole px) while wider than CARD_MAX_WIDTH. @param {string} text @param {Advance} advance */
export function cardSize(text, advance) {
  let size = CARD_SIZE;
  while (size > 24 && letterPositions(text, size, CARD_TRACKING, advance).width > CARD_MAX_WIDTH) size -= 1;
  return size;
}

/** The card's baseline: its capitals centred top to bottom. @param {number} size */
export function cardBaseline(size) {
  return Math.round((HEIGHT + CAP_HEIGHT * size) / 2);
}

/** The strip's top and each line's baseline. @param {string[]} lines */
export function stripLayout(lines) {
  const capitals = Math.round(CAP_HEIGHT * DETAIL_SIZE);
  const height = STRIP_PADDING + capitals + (lines.length - 1) * DETAIL_LINE_SPACING + STRIP_PADDING + STRIP_DESCENT;
  const top = HEIGHT - height;
  return { top, baselines: lines.map((_, i) => top + STRIP_PADDING + capitals + i * DETAIL_LINE_SPACING) };
}

// -- drawing (each layer on its own 1920x1080 canvas; the caller fades it with CSS opacity)
/** @param {CanvasRenderingContext2D} context @returns {Advance} */
function canvasAdvance(context) {
  return (letter, size) => {
    context.font = `700 ${size}px ${FONT_FAMILY}`;
    return context.measureText(letter).width;
  };
}

/** @param {CanvasRenderingContext2D} context */
function drawRow(context, text, size, tracking, rowX, baseline) {
  const advance = canvasAdvance(context);
  const { positions } = letterPositions(text, size, tracking, advance);
  context.font = `700 ${size}px ${FONT_FAMILY}`;
  context.textAlign = "left";
  context.textBaseline = "alphabetic";
  for (const { letter, x } of positions) if (letter.trim()) context.fillText(letter, rowX + x, baseline);
}

/**
 * A centred card ("BROCKLEY LIVE" or the title), cleared first.
 * @param {CanvasRenderingContext2D} context @param {string} text @param {string} colour
 */
export function drawCard(context, text, colour) {
  context.clearRect(0, 0, WIDTH, HEIGHT);
  if (!text) return;
  const advance = canvasAdvance(context);
  const size = cardSize(text, advance);
  const { width } = letterPositions(text, size, CARD_TRACKING, advance);
  context.fillStyle = colour;
  drawRow(context, text, size, CARD_TRACKING, (WIDTH - width) / 2, cardBaseline(size));
}

/**
 * The strip along the bottom with the lines, cleared first.
 * @param {CanvasRenderingContext2D} context @param {string[]} lines
 */
export function drawStrip(context, lines) {
  context.clearRect(0, 0, WIDTH, HEIGHT);
  if (!lines.length) return;
  const advance = canvasAdvance(context);
  const { top, baselines } = stripLayout(lines);
  context.fillStyle = `rgba(0, 0, 0, ${STRIP_ALPHA})`;
  context.fillRect(0, top, WIDTH, HEIGHT - top);
  context.fillStyle = CREAM;
  lines.forEach((line, i) => {
    let size = DETAIL_SIZE;
    let { width } = letterPositions(line, size, 0, advance);
    if (width > DETAIL_MAX_WIDTH) {
      size = Math.max(16, Math.floor(size * DETAIL_MAX_WIDTH / width));
      ({ width } = letterPositions(line, size, 0, advance));
    }
    drawRow(context, line, size, 0, (WIDTH - width) / 2, baselines[i]);
  });
}
