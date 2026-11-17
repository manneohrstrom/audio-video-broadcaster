// The editor's pictograms: one stroke weight, round ends, drawn on a 24-unit grid, coloured
// by the text they sit in (currentColor). A few have a face or a wink on purpose: the
// editor's design language is a 1970s desk instrument with a sense of humour.
//
// Usage: html`<${Icon} name="scissors" />` (decorative: hidden from screen readers; the
// button or label next to it carries the words).
import { html } from "./html.js";

const PATHS = {
  // Steps
  scissors: () => html`<circle cx="6" cy="17" r="3" /><circle cx="18" cy="17" r="3" />
    <path d="M8.2 15 16 4M15.8 15 8 4" /><circle cx="12" cy="9.6" r="0.6" fill="currentColor" />`,
  eye: () => html`<path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z" />
    <circle cx="12" cy="12" r="3" /><circle cx="13" cy="11" r="0.8" fill="currentColor" stroke="none" />`,
  // Transport
  play: () => html`<path d="M8 5.5v13l10.5-6.5z" fill="currentColor" />`,
  pause: () => html`<path d="M8 5.5v13M16 5.5v13" stroke-width="3.2" />`,
  frameBack: () => html`<path d="M17 6 9 12l8 6M6 6v12" />`,
  frameForward: () => html`<path d="m7 6 8 6-8 6M18 6v12" />`,
  // History: a little loop that goes back (undo) or on (redo)
  undo: () => html`<path d="M9 14 4.5 9.5 9 5" /><path d="M4.5 9.5H14a5.5 5.5 0 0 1 0 11h-3" />`,
  redo: () => html`<path d="m15 14 4.5-4.5L15 5" /><path d="M19.5 9.5H10a5.5 5.5 0 0 0 0 11h3" />`,
  // Sync: a tuning fork, ringing
  tuningFork: () => html`<path d="M9 3v7a3 3 0 0 0 6 0V3M12 13v8" /><path d="M18.5 5.5c1 1 1 3 0 4M20.5 4c2 2 2 5 0 7" />`,
  // Make video: a film reel with a smile
  reel: () => html`<circle cx="12" cy="12" r="9" /><circle cx="12" cy="7.2" r="1.6" /><circle cx="7.4" cy="10.6" r="1.6" />
    <circle cx="16.6" cy="10.6" r="1.6" /><path d="M9 15.2c1.6 1.6 4.4 1.6 6 0" />`,
  // Nudging the bounce: a note stepping left or right
  nudgeEarlier: () => html`<path d="M15 6v9.5" /><circle cx="12.5" cy="16" r="2.5" /><path d="M15 6l3 1.5" /><path d="M8 12H3m2-2-2 2 2 2" />`,
  nudgeLater: () => html`<path d="M9 6v9.5" /><circle cx="6.5" cy="16" r="2.5" /><path d="M9 6l3 1.5" /><path d="M16 12h5m-2-2 2 2-2 2" />`,
  // Small things
  check: () => html`<path d="m5 12.5 4.5 4.5L19 7.5" />`,
  close: () => html`<path d="M6 6l12 12M18 6 6 18" />`,
  reset: () => html`<path d="M4 12a8 8 0 1 0 2.4-5.7" /><path d="M4 4v4.5h4.5" />`,
  magnifier: () => html`<circle cx="10.5" cy="10.5" r="6" /><path d="m15 15 5 5" /><path d="M8 10.5h5" />`,
  watch: () => html`<rect x="3" y="5" width="18" height="12" rx="2.5" /><path d="M10 9v4.5l4-2.25z" fill="currentColor" /><path d="M8 20h8" />`,
};

export function Icon({ name, size = 18 }) {
  return html`<svg class="icon" width=${size} height=${size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
                   focusable="false">${PATHS[name]()}</svg>`;
}
