// What a camera shows (hub/hubd/camera_tags.py; docs/ai-cut-plan.md): one or more of the band's
// instruments or the whole band, a shot size and a note. Shared by the hub page (the camera's
// current role) and the editor (one take's), so both look and behave the same.
// Styles: shared/styles/camera-role.css.
import { useState } from "preact/hooks";
import { html } from "./html.js";

/** @typedef {{ shows: string[], shot: string | null, note: string }} CameraTags */
/** @typedef {{ instruments: string[], whole_band: string, shots: string[] }} TagVocabulary */

export const NOTE_LONGEST = 200;                 // as camera_tags.py

/** @returns {CameraTags} */
export function emptyTags() {
  return { shows: [], shot: null, note: "" };
}

/** The role in a few words: "tenor sax, trumpet · close", or "" when nothing is said.
 *  @param {CameraTags | null | undefined} tags */
export function describeRole(tags) {
  if (!tags) return "";
  const parts = [];
  if (tags.shows && tags.shows.length) parts.push(tags.shows.join(", "));
  if (tags.shot) parts.push(tags.shot);
  return parts.join(" · ");
}

/** The three settings as controls. onChange gets the whole new tags. The instruments are a
 *  list kept on the hub that grows (user, 2026-11-28: who comes in changes from take to take):
 *  "+ instrument" adds one (onAddInstrument(name), which saves it on the hub) and picks it.
 *  @param {{ vocabulary: TagVocabulary, tags: CameraTags, onChange: (tags: CameraTags) => void,
 *            onAddInstrument: (name: string) => Promise<void> }} props */
export function RoleEditor({ vocabulary, tags, onChange, onAddInstrument }) {
  const [adding, setAdding] = useState(false);
  const known = [vocabulary.whole_band, ...vocabulary.instruments];
  const choices = [...known, ...tags.shows.filter(name => !known.includes(name))];      // an old tag's name too
  const toggle = name => {
    const shows = tags.shows.includes(name) ? tags.shows.filter(n => n !== name) : [...tags.shows, name];
    onChange({ ...tags, shows });
  };
  return html`
    <div class="role-editor">
      <div class="role-row">
        <span class="role-label">Shows</span>
        <div class="role-chips">
          ${choices.map(name => html`
            <button type="button" class=${"role-chip" + (tags.shows.includes(name) ? " on" : "")}
                    aria-pressed=${tags.shows.includes(name)} onClick=${() => toggle(name)}>${name}</button>`)}
          ${adding
            ? html`<input class="role-new" type="text" maxlength="40" autofocus placeholder="e.g. cello"
                          onKeyDown=${async event => {
                            if (event.key === "Escape") setAdding(false);
                            if (event.key !== "Enter") return;
                            const name = event.target.value.trim().toLowerCase().replace(/\s+/g, " ");
                            if (!name) return setAdding(false);
                            await onAddInstrument(name);
                            if (!tags.shows.includes(name)) onChange({ ...tags, shows: [...tags.shows, name] });
                            setAdding(false);
                          }}
                          onBlur=${() => setAdding(false)} />`
            : html`<button type="button" class="role-chip add" onClick=${() => setAdding(true)}
                           title="An instrument that isn't in the list: standard names help the smart cut">+ instrument</button>`}
        </div>
      </div>
      <div class="role-row">
        <span class="role-label">Shot</span>
        <div class="role-chips">
          ${vocabulary.shots.map(shot => html`
            <button type="button" class=${"role-chip" + (tags.shot === shot ? " on" : "")} aria-pressed=${tags.shot === shot}
                    onClick=${() => onChange({ ...tags, shot: tags.shot === shot ? null : shot })}>${shot}</button>`)}
        </div>
      </div>
      <label class="role-row">
        <span class="role-label">Note</span>
        <input class="role-note" type="text" maxlength=${NOTE_LONGEST} value=${tags.note}
               placeholder="what else the editor should know: from the back, bass hardly visible…"
               onInput=${event => onChange({ ...tags, note: event.target.value })} />
      </label>
    </div>`;
}
