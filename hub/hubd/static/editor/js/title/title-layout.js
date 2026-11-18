// The video's title in the preview (docs/editor-plan.md section 5b): a title line and the
// lines below it, left-aligned at LEFT and centred top to bottom, in Figtree. The render draws the same thing with ffmpeg's drawtext
// (hub/ingest/title_render.py), with the same font files and these same numbers: change them
// in both places. Coordinates are in the 1920x1080 picture; a baseline is the alphabetic one.

export const WIDTH = 1920;
export const HEIGHT = 1080;
export const LEFT = 144;               // px from the left edge: the start of every row (user: left-aligned)
export const TITLE_SIZE = 84;
export const LINE_SIZE = 42;
export const TITLE_TO_LINES = 78;      // baseline of the title to the baseline of the first line below
export const LINE_SPACING = 60;        // baseline to baseline between the lines below
export const CAP_HEIGHT = 0.7;         // Figtree's capitals, as a fraction of the size (centring)
export const FADE_SECONDS = 0.5;       // the dissolve in, and the one out
export const SHADOW_Y = 2;
export const SHADOW_ALPHA = 0.45;
// The Brockley Live logo (the border is on from the start) fades in shortly after the title
// is gone, or at 5 s when there's no title (user, 2026-11-18). As title_render.logo_start_s.
export const LOGO_AFTER_TITLE_SECONDS = 1;
export const LOGO_WITHOUT_TITLE_SECONDS = 5;
export const LOGO_FADE_SECONDS = 1;
export const MAX_LINES = 8;
export const MAX_CHARACTERS = 80;
export const TITLE_FONT = "600 84px Figtree";
export const LINE_FONT = "400 42px Figtree";

/** @typedef {{ on: boolean, text: string, lines: string[], colour: string, start_s: number, duration_s: number }} Title */

/** @param {Title} title */
export function linesBelow(title) {
  return (title.lines || []).map(line => line.trim()).filter(Boolean).slice(0, MAX_LINES);
}

/** @param {Title | null} title */
export function isShown(title) {
  return !!title && title.on === true && !!((title.text || "").trim() || linesBelow(title).length);
}

/**
 * Each row top to bottom: its text, size and baseline. The block is centred, from the top of
 * the first row's capitals to the last baseline (as title_render.layout).
 * @param {Title} title
 */
export function layout(title) {
  const text = (title.text || "").trim();
  const rows = [...(text ? [{ text, size: TITLE_SIZE }] : []), ...linesBelow(title).map(line => ({ text: line, size: LINE_SIZE }))];
  if (!rows.length) return [];
  const steps = [0];
  for (let i = 1; i < rows.length; i++) steps.push(steps[i - 1] + (rows[i - 1].size === TITLE_SIZE ? TITLE_TO_LINES : LINE_SPACING));
  // Math.round as Python's round(): the same here, as 0.7 x 84 and 0.7 x 42 never leave exactly .5.
  const firstBaseline = Math.round((HEIGHT + CAP_HEIGHT * rows[0].size - steps[steps.length - 1]) / 2);
  return rows.map((row, i) => ({ ...row, baseline: firstBaseline + steps[i] }));
}

/**
 * How visible the title is at song time `seconds`: 0 before, dissolving in over FADE_SECONDS,
 * 1, dissolving out so it's gone at start + duration.
 * @param {Title | null} title
 */
export function opacityAt(title, seconds) {
  if (!isShown(title)) return 0;
  const end = title.start_s + title.duration_s;
  return Math.min(1, Math.max(0, Math.min((seconds - title.start_s) / FADE_SECONDS, (end - seconds) / FADE_SECONDS)));
}

/** When the logo starts fading in (song seconds). @param {Title | null} title */
export function logoStartSeconds(title) {
  return isShown(title) ? title.start_s + title.duration_s + LOGO_AFTER_TITLE_SECONDS : LOGO_WITHOUT_TITLE_SECONDS;
}

/** How visible the logo is at song time `seconds`: 0, fading in, then 1 to the end. @param {Title | null} title */
export function logoOpacityAt(title, seconds) {
  return Math.min(1, Math.max(0, (seconds - logoStartSeconds(title)) / LOGO_FADE_SECONDS));
}

/**
 * Draw the title, fully visible, on a 1920x1080 2D context (cleared first). The caller sets
 * the opacity (CSS on the layer), as drawtext's alpha does for text and shadow together.
 * @param {CanvasRenderingContext2D} context
 * @param {Title} title
 */
export function drawTitle(context, title) {
  context.clearRect(0, 0, WIDTH, HEIGHT);
  if (!isShown(title)) return;
  context.textAlign = "left";
  context.textBaseline = "alphabetic";
  context.fillStyle = title.colour || "#ffffff";
  context.shadowColor = `rgba(0, 0, 0, ${SHADOW_ALPHA})`;
  context.shadowOffsetX = 0;
  context.shadowOffsetY = SHADOW_Y;
  context.shadowBlur = 0;
  for (const row of layout(title)) {
    context.font = row.size === TITLE_SIZE ? TITLE_FONT : LINE_FONT;
    context.fillText(row.text, LEFT, row.baseline);
  }
}
