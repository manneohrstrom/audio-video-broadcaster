// Step 2, the look: a row of Filters and a row of Effects (no tabs, user 2026-11-16),
// Instagram-style: tap to try, tap the chosen one again for its strength. The tiles show
// the current picture through each choice, refreshed twice a second.
import { useEffect, useRef } from "preact/hooks";
import { html } from "./html.js";
import { LookRenderer } from "../look/look-renderer.js";
import { everyFrame } from "../frame-loop.js";

const TILE_REFRESH_MS = 500;

export function LookPanel({ store }) {
  const tiles = useRef(new Map());          // "filter:<id>" / "effect:<id>" -> tile canvas
  const looks = store.looks;

  useEffect(() => {
    const offscreen = document.createElement("canvas");
    offscreen.width = 224;
    offscreen.height = 126;
    let renderer = null;
    try { renderer = new LookRenderer(offscreen); } catch (error) { return undefined; }
    let last = 0;
    return everyFrame(now => {
      if (now - last < TILE_REFRESH_MS || !store.pictureCanvas) return;
      last = now;
      for (const [key, tile] of tiles.current) {
        if (!tile.isConnected) continue;
        const [kind, id] = key.split(":");
        if (kind === "filter") {
          renderer.setFilter(store.filterSettings(id), id === store.look.filter ? store.look.filter_strength : 1);
          renderer.render(store.pictureCanvas, {}, store.playheadSeconds);
        } else {
          const effect = looks.effects.find(e => e.id === id);
          renderer.setFilter(store.filterSettings(), store.look.filter_strength);
          renderer.render(store.pictureCanvas, { [id]: store.look.effects[id] ?? effect.default_strength }, store.playheadSeconds);
        }
        tile.getContext("2d").drawImage(offscreen, 0, 0);
      }
    });
  }, [store]);

  if (!looks) return html`<p class="message">Loading the looks…</p>`;
  const tile = (key, item, selected, on, onTap) => html`
    <button class=${"look-tile" + (selected ? " selected" : "")} key=${key} onClick=${onTap} aria-pressed=${String(selected || on)}>
      <canvas width="224" height="126" ref=${element => { if (element) tiles.current.set(key, element); }}></canvas>
      <span>${item.name}</span>${on && html`<i class="on-dot"></i>`}
    </button>`;

  return html`
    <section class="look-panel" aria-label="Look">
      <div class="row-title">Filters</div>
      <div class="look-row">
        ${looks.filters.map(filter => tile(`filter:${filter.id}`, filter, store.look.filter === filter.id, false, () => store.tapFilter(filter.id)))}
      </div>
      <div class="row-title">Effects${Object.keys(store.look.effects).length ? ` · ${Object.keys(store.look.effects).length} on` : ""}</div>
      <div class="look-row">
        ${looks.effects.map(effect => tile(`effect:${effect.id}`, effect, store.adjusting === effect.id,
                                          store.look.effects[effect.id] != null, () => store.tapEffect(effect.id)))}
      </div>
      <div class="adjust-row">${adjustRow(store, looks)}</div>
    </section>`;
}

function adjustRow(store, looks) {
  const slider = (label, value, onChange, extra) => html`
    <strong>${label}</strong>
    <input type="range" min="0" max="100" value=${Math.round(value * 100)} aria-label=${`${label} strength`}
           onInput=${event => onChange(event.target.value / 100)} />
    <output>${Math.round(value * 100)}</output>
    ${extra}
    <button class="button small" onClick=${() => { store.adjusting = null; store.notify(); }}>Done</button>`;
  if (store.adjusting === "filter") {
    const filter = looks.filters.find(f => f.id === store.look.filter);
    return slider(filter.name, store.look.filter_strength, value => store.setStrength("filter", value));
  }
  const effect = looks.effects.find(e => e.id === store.adjusting);
  if (effect && store.look.effects[effect.id] != null) {
    return slider(effect.name, store.look.effects[effect.id], value => store.setStrength(effect.id, value),
                  html`<button class="button small" onClick=${() => store.removeEffect(effect.id)}>Remove</button>`);
  }
  return html`<span>Tap a filter to try it, an effect to add it. Tap again for its strength. Effects stack.</span>`;
}
