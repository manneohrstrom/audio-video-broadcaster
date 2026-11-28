// The Cut step's camera move control (camera-move.js; user, 2026-11-28): the slow zoom or
// drift of the shot under the playhead. None, push in, pull out, drift left or right, and how
// much. The smart cut sets moves too; this is where they're changed by hand.
import { useEffect, useState } from "preact/hooks";
import { html } from "./html.js";
import { everyFrame } from "../frame-loop.js";
import { cutIndexAt } from "../edit-model.js";
import { DEFAULT_AMOUNT, MAX_GAIN, MOVE_KINDS, MOVE_NAMES } from "../camera-move.js";

const BUTTON_TEXT = { none: "still", in: "in", out: "out", left: "←", right: "→" };

export function MoveBar({ store }) {
  // The playhead moves without notifying the store while playing: follow the shot ourselves.
  const [index, setIndex] = useState(() => cutIndexAt(store.cuts, store.playheadSeconds));
  useEffect(() => everyFrame(() => {
    const now = cutIndexAt(store.cuts, store.playheadSeconds);
    setIndex(previous => (previous === now ? previous : now));
  }), [store]);

  const shotIndex = Math.min(index, store.cuts.length - 1);
  const cut = store.cuts[shotIndex];
  const camera = store.camerasById.get(cut.cameraId);
  const move = cut.move || null;
  const choose = kind => store.setShotMove(shotIndex, kind === "none" ? null : { kind, amount: move ? move.amount : DEFAULT_AMOUNT });
  const shotName = `shot ${shotIndex + 1}, ${camera ? camera.label : cut.cameraId}`;
  return html`
    <div class="move-bar" role="group" aria-label=${`Camera move of ${shotName}`}
         title=${`A slow zoom or drift over the shot under the playhead (${shotName}). Close-ups zoom on the middle of the picture.`}>
      <span class="move-label">Move</span>
      <span class="move-kinds">
        ${["none", ...MOVE_KINDS].map(kind => {
          const on = (move ? move.kind : "none") === kind;
          return html`<button class=${"button small" + (on ? " selected" : "")} aria-pressed=${String(on)}
                              title=${kind === "none" ? "No move" : MOVE_NAMES[kind]}
                              onClick=${() => { if (!on) choose(kind); }}>${BUTTON_TEXT[kind]}</button>`;
        })}
      </span>
      ${move && html`
        <input class="move-amount" type="range" min="0.1" max="1" step="0.05" value=${move.amount}
               aria-label="How much it moves" title="How much it moves"
               onInput=${event => store.setShotMove(shotIndex, { kind: move.kind, amount: Number(event.target.value) })} />
        <output class="move-zoom" title="The most it zooms (less on a short shot)">${(move.amount * MAX_GAIN * 100).toFixed(1)} %</output>`}
    </div>`;
}
