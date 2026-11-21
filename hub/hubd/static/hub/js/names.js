// The names the user gives cameras and takes (user, 2026-11-19; hub/hubd/names.py, in
// /api/status as "names"). The id (cam1, phone-k7q2, 20261119-140512) stays the nickname:
// shown small, in brackets, next to a name the user gave.
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";
import { saveName } from "./api.js";

/** @typedef {{ cameras?: Record<string, string>, takes?: Record<string, string> }} Names */

const LONGEST = { cameras: 32, takes: 80 };              // as names.py

/** A phone that rejoined mid-take records "pauls-pixel-part2": it's still pauls-pixel. */
const baseCamera = id => id.replace(/-part\d{1,2}$/, "");

/** The name the user gave a camera, or "". @param {Names | undefined} names @param {string} id */
export function givenCameraName(names, id) {
  return (names && names.cameras && names.cameras[baseCamera(id)]) || "";
}

/** A camera's name for people: the user's, else a phone's own label, else its id. */
export function cameraName(names, id, label) {
  return givenCameraName(names, id) || label || id;
}

/** The name the user gave a take, or "". @param {Names | undefined} names @param {string} id */
export function takeName(names, id) {
  return (names && names.takes && names.takes[id]) || "";
}

/** "Drum kit (cam2)" as text, for titles and messages; just the default when not named. */
export function nameWithId(given, fallback, id) {
  return given ? `${given} (${id})` : fallback;
}

/** A name with its id in small brackets when the user named it. */
export function Name({ given, fallback, id }) {
  return html`${given || fallback}${given && html` <small class="nick">(${id})</small>`}`;
}

/** A phone's colour dot, the same colour its screen shows (phones.py COLOURS). */
export function PhoneColour({ colour }) {
  return colour ? html`<i class="phone-colour" style=${{ background: colour.hex }} title=${colour.label} aria-hidden="true"></i>` : null;
}

/** A name that can be changed in place: the pencil (or a double click) opens a field;
 * Enter or leaving the field saves, Esc cancels, an empty name goes back to the default. */
export function EditableName({ kind, id, given, fallback, say, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [shown, setShown] = useState(null);              // the saved name until the next status poll has it
  const field = useRef(null);
  const closing = useRef(false);
  useEffect(() => { if (editing && field.current) { field.current.focus(); field.current.select(); } }, [editing]);
  useEffect(() => setShown(null), [given]);

  const current = shown ?? given;
  function open(event) {
    event.stopPropagation();
    closing.current = false;
    setDraft(current || "");
    setEditing(true);
  }
  async function finish(save) {
    if (closing.current) return;
    closing.current = true;
    setEditing(false);
    const name = draft.trim().replace(/\s+/g, " ");
    if (!save || name === (current || "")) return;
    try {
      const names = await saveName(kind, id, name);
      setShown((names[kind] || {})[id] || "");
      if (onSaved) onSaved();
    } catch (error) {
      if (say) say(error.message, true);
    }
  }
  if (editing) {
    return html`<input ref=${field} class="name-field" value=${draft} maxLength=${LONGEST[kind]}
                       placeholder=${fallback} aria-label=${`Name for ${id}`}
                       onInput=${event => setDraft(event.currentTarget.value)}
                       onKeyDown=${event => {
                         event.stopPropagation();          // Esc here doesn't close the dialog around it
                         if (event.key === "Enter") finish(true);
                         if (event.key === "Escape") finish(false);
                       }}
                       onBlur=${() => finish(true)} />`;
  }
  return html`<span class="name-edit" onDblClick=${open}>
    <${Name} given=${current} fallback=${fallback} id=${id} />
    <button class="name-pencil" onClick=${open} title=${`Rename ${current || fallback}`} aria-label=${`Rename ${current || fallback}`}>
      <${Icon} name="pencil" size=${12} /></button></span>`;
}
