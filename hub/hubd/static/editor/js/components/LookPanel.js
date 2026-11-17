// Step 2, the look: a row of Filters and a row of Effects (no tabs, user 2026-11-16).
// Tap a filter to choose it; tap an effect to turn it on, again to turn it off. Under each
// row: the strength sliders of the chosen filter and of every effect that is on. The tiles
// show the current picture through each choice, refreshed twice a second. Below them the
// Adjust sliders: the primary correction (brightness, contrast, highlights, shadows,
// saturation, warmth, tint, fade), applied before the filter as in a DI grade.
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
          renderer.setLook(store.look.adjust, looks.adjustments, store.filterSettings(id),
                           id === store.look.filter ? store.look.filter_strength : 1);
          renderer.render(store.pictureCanvas, {}, store.playheadSeconds);
        } else {
          const effect = looks.effects.find(e => e.id === id);
          renderer.setLook(store.look.adjust, looks.adjustments, store.filterSettings(), store.look.filter_strength);
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

  const effectsOn = Object.keys(store.look.effects).length;
  const adjusted = Object.keys(store.look.adjust || {}).length;
  return html`
    <section class="look-panel" aria-label="Look">
      <div class="look-section">
        <div class="row-title">Filters</div>
        ${[["studio", "Studio"], ["instagram", "Instagram"]].map(([group, title]) => html`
          <div class="filter-group">${title}</div>
          <div class="look-row">
            ${looks.filters.filter(filter => (filter.group || "studio") === group).map(filter =>
              tile(`filter:${filter.id}`, filter, store.look.filter === filter.id, false, () => store.tapFilter(filter.id)))}
          </div>`)}
        ${store.look.filter !== "normal" && strengthRow(
          looks.filters.find(f => f.id === store.look.filter)?.name, store.look.filter_strength,
          value => store.setStrength("filter", value))}
      </div>
      <div class="look-section">
        <div class="row-title">Effects${effectsOn ? ` · ${effectsOn} on` : ""}</div>
        <div class="look-row">
          ${looks.effects.map(effect => tile(`effect:${effect.id}`, effect, store.look.effects[effect.id] != null,
                                            store.look.effects[effect.id] != null, () => store.tapEffect(effect.id)))}
        </div>
        ${looks.effects.filter(effect => store.look.effects[effect.id] != null).map(effect =>
          strengthRow(effect.name, store.look.effects[effect.id], value => store.setStrength(effect.id, value)))}
        ${!effectsOn && html`<p class="look-hint">Tap an effect to turn it on, tap it again to turn it off. Effects stack.</p>`}
      </div>
      <div class="look-section">
        <div class="section-header">
          <div class="row-title">Adjust${adjusted ? ` · ${adjusted} changed` : ""}</div>
          <button class="button small" disabled=${!adjusted} onClick=${() => store.resetAdjustments()}
                  title="Set every colour slider back to 0">Reset</button>
        </div>
        <div class="adjust-grid">
          ${looks.adjustments.map(adjustment => {
            const value = store.look.adjust[adjustment.id] || 0;
            const shown = Math.round(value * 100);
            return html`
              <label class=${"adjust-slider" + (value ? " changed" : "")} key=${adjustment.id}
                     title="Double-click to set back to 0" onDblClick=${() => store.setAdjustment(adjustment.id, 0)}>
                <span>${adjustment.name}</span>
                <input type="range" min=${adjustment.positive_only ? 0 : -100} max="100" value=${shown}
                       aria-label=${adjustment.name} onInput=${event => store.setAdjustment(adjustment.id, event.target.value / 100)} />
                <output>${shown > 0 ? "+" : ""}${shown}</output>
              </label>`;
          })}
        </div>
      </div>
    </section>`;
}

/** One strength slider (0-100) under the Filters or Effects row. */
function strengthRow(name, value, onChange) {
  if (!name || value == null) return null;
  return html`
    <label class="strength-row" key=${name}>
      <span>${name}</span>
      <input type="range" min="0" max="100" value=${Math.round(value * 100)} aria-label=${`${name} strength`}
             onInput=${event => onChange(event.target.value / 100)} />
      <output>${Math.round(value * 100)}</output>
    </label>`;
}
