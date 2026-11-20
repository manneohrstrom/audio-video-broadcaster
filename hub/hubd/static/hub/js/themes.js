// The hub page's looks (user, 2026-11-20): the grille on the footer plate picks one. A theme
// changes the palette, the page's background (a colour or a tiling texture), the fonts and
// the rig display's style; all of that is CSS (hub/styles/themes.css, one block per theme,
// selected by data-theme on <html>). Kept in this browser only (localStorage), so each
// phone and laptop can have its own; index.html applies it before the first paint.
//
// display: "segments" for a seven- or fourteen-segment face (DSEG: the unlit segments
// show faintly behind the lit ones), "plain" for any other font (the text alone).

/** @typedef {{ id: string, name: string, note: string, display: "segments" | "plain", themeColor: string }} Theme */

/** @type {Theme[]} */
export const THEMES = [
  { id: "graphite", name: "Graphite", note: "the original: a 1970s desk instrument", display: "segments", themeColor: "#1e1c1a" },
  { id: "sixties", name: "Space Age '66", note: "mod cream, op-art rings, Nixie tubes", display: "plain", themeColor: "#f2e7cf" },
  { id: "seventies", name: "Groove '74", note: "walnut veneer, sunset stripes, a VFD", display: "segments", themeColor: "#2b1a10" },
  { id: "eighties", name: "Memphis '84", note: "confetti, hard shadows, a Casio LCD", display: "segments", themeColor: "#fdf8ee" },
  { id: "neon", name: "Neon 2077", note: "cyan and magenta, scanlines, a grid", display: "segments", themeColor: "#07040f" },
  { id: "budapest", name: "Grand Budapest", note: "striped wallpaper, a brass plaque", display: "plain", themeColor: "#f0bcc4" },
  { id: "paper", name: "Paper", note: "almost nothing: ink on paper", display: "plain", themeColor: "#f7f6f2" },
  { id: "bluenote", name: "Blue Note '59", note: "a jazz sleeve: halftone and bold type", display: "plain", themeColor: "#0e0e10" },
  { id: "dub", name: "Sound System", note: "speaker grille, red, gold and green", display: "segments", themeColor: "#0c0c0b" },
  { id: "phosphor", name: "Phosphor", note: "a green screen terminal", display: "plain", themeColor: "#020a05" },
];

const KEY = "hub-theme";
const TODAY_KEY = "hub-theme-today";     // "<day> <id>": lets index.html paint today's theme at once

// "A new one every day" (user, 2026-11-20), the default choice: picked from the date alone,
// so every phone and laptop shows the same theme on the same day. The days go through all
// the themes in a shuffled order, a new shuffle every round, never the same one twice running.
export const DAILY = "daily";

/** A shuffled order of the themes for round `round`, the same everywhere (seeded). */
function roundOrder(round) {
  let seed = (round * 2654435761) >>> 0;
  const random = () => {                                  // mulberry32
    seed = (seed + 0x6d2b79f5) >>> 0;
    let t = seed;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const order = THEMES.map((_, i) => i);
  for (let i = order.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [order[i], order[j]] = [order[j], order[i]];
  }
  return order;
}

/** The order for a round, its first day never repeating the last day of the round before. */
function dayOrder(round) {
  const order = roundOrder(round);
  const before = roundOrder(round - 1);
  if (order[0] === before[before.length - 1]) [order[0], order[1]] = [order[1], order[0]];
  return order;
}

/** The local calendar day as a number (days since 1970-01-01). */
function dayNumber(date) {
  return Math.round(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000);
}

/** Today's theme (local midnight to midnight). @returns {Theme} */
export function dailyTheme(date = new Date()) {
  const day = dayNumber(date);
  return THEMES[dayOrder(Math.floor(day / THEMES.length))[day % THEMES.length]];
}

/** "2026-11-20", local: the same text index.html builds. */
function dayKey(date) {
  return `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
}

/** What this browser chose: DAILY (the default) or a theme's id. @returns {string} */
export function savedChoice() {
  let choice = null;
  try { choice = localStorage.getItem(KEY); } catch { /* private mode: the default */ }
  return THEMES.some(theme => theme.id === choice) ? choice : DAILY;
}

/** Remember a choice (DAILY or a theme's id). */
export function saveChoice(choice) {
  try { localStorage.setItem(KEY, choice); } catch { /* not kept: fine */ }
}

/** The theme a choice stands for right now. @returns {Theme} */
export function themeFor(choice, date = new Date()) {
  return THEMES.find(theme => theme.id === choice) || dailyTheme(date);
}

/** Put a theme on the page (and note today's, for index.html's first paint). @param {Theme} theme */
export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme.id;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme.themeColor);
  try { localStorage.setItem(TODAY_KEY, `${dayKey(new Date())} ${dailyTheme().id}`); } catch { /* fine */ }
}
