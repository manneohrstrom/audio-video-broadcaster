// A dialog over the page: click outside or press Escape (App.js) to close.
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";

export function Dialog({ title, onClose, wide = false, children }) {
  return html`
    <div class="dialog-backdrop" onClick=${event => { if (event.target === event.currentTarget) onClose(); }}>
      <div class=${"dialog" + (wide ? " wide" : "")} role="dialog" aria-modal="true" aria-label=${title}>
        <header><h2>${title}</h2>
          <button class="round-key" onClick=${onClose} aria-label="Close"><${Icon} name="close" size=${16} /></button></header>
        ${children}
      </div>
    </div>`;
}
