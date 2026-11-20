// The grille on the footer plate: tap it for the themes (themes.js). Each choice is drawn
// in its own theme (data-theme on the card), so the list is its own preview. The first
// card, "a new one every day", is drawn in today's theme.
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "shared/html.js";
import { DAILY, THEMES, dailyTheme } from "../themes.js";

/** A small piece of the theme: its background, a panel with the display and the three keys. */
function Swatch({ theme }) {
  return html`
    <span class="theme-swatch" aria-hidden="true"><span class="theme-panel">
      <span class=${"theme-display" + (theme.display === "plain" ? " plain" : "")}>REC</span>
      <span class="theme-keys"><i class="rec"></i><i class="stop"></i><i class="live"></i></span>
    </span></span>`;
}

export function ThemePicker({ theme, choice, onPick }) {
  const [open, setOpen] = useState(false);
  const placeRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = event => { if (event.key === "Escape") setOpen(false); };
    const onDown = event => { if (placeRef.current && !placeRef.current.contains(event.target)) setOpen(false); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("pointerdown", onDown); };
  }, [open]);

  const pick = next => { onPick(next); setOpen(false); };
  const today = dailyTheme();
  const daily = choice === DAILY;
  const label = daily ? `Theme: ${theme.name}, today's. Change theme` : `Theme: ${theme.name}. Change theme`;
  return html`
    <span class="theme-place" ref=${placeRef}>
      <button class="theme-key" aria-haspopup="true" aria-expanded=${String(open)} onClick=${() => setOpen(!open)}
              title=${daily ? `Today's theme: ${theme.name}. Tap to change.` : `Theme: ${theme.name}. Tap to change.`} aria-label=${label}>
        <span class="grille" aria-hidden="true"></span>
      </button>
      ${open && html`
        <div class="theme-menu" role="menu" aria-label="Themes">
          <button class="theme-option daily" data-theme=${today.id} role="menuitemradio" aria-checked=${String(daily)}
                  onClick=${() => pick(DAILY)}>
            <${Swatch} theme=${today} />
            <span class="theme-words"><b>A new one every day</b><small>today: ${today.name}</small></span>
          </button>
          ${THEMES.map(item => html`
            <button class="theme-option" data-theme=${item.id} role="menuitemradio" aria-checked=${String(item.id === choice)}
                    onClick=${() => pick(item.id)}>
              <${Swatch} theme=${item} />
              <span class="theme-words"><b>${item.name}</b><small>${item.note}</small></span>
            </button>`)}
        </div>`}
    </span>`;
}
