// A camera's role on the hub page (docs/ai-cut-plan.md): what it shows, the shot size and a
// note, for Pis and phones alike. The button on a camera tile says the role in a few words;
// it opens a dialog to change it. Saved as the camera's current role: each take copies it at REC.
import { useState } from "preact/hooks";
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";
import { RoleEditor, describeRole, emptyTags } from "shared/camera-role.js";
import { Dialog } from "./Dialog.js";
import { addInstrument, saveCameraTags } from "../api.js";

/** A phone that rejoined mid-take records "pauls-pixel-part2": it's still pauls-pixel. */
const baseCamera = id => id.replace(/-part\d{1,2}$/, "");

export function RoleButton({ id, who, roles, say, refreshStatus }) {
  const [open, setOpen] = useState(false);
  if (!roles) return null;                               // an older hubd without camera tags
  const tags = (roles.cameras || {})[baseCamera(id)] || emptyTags();
  const summary = describeRole(tags);
  return html`
    <button class="key small role-button" onClick=${() => setOpen(true)}
            title=${summary ? `What ${who} shows: ${summary}${tags.note ? " · " + tags.note : ""}` : `Say what ${who} shows`}>
      <${Icon} name="eye" size=${14} />${summary || "role"}</button>
    ${open && html`<${RoleDialog} id=${id} who=${who} roles=${roles} tags=${tags} say=${say}
                                  onClose=${() => setOpen(false)} refreshStatus=${refreshStatus} />`}`;
}

function RoleDialog({ id, who, roles, tags, say, onClose, refreshStatus }) {
  const [draft, setDraft] = useState(tags);
  const [vocabulary, setVocabulary] = useState(roles);
  const [saving, setSaving] = useState(false);
  async function addOne(name) {
    try {
      setVocabulary({ ...vocabulary, ...(await addInstrument(name)) });
      refreshStatus();
    } catch (error) {
      say(error.message, true);
    }
  }
  async function save() {
    setSaving(true);
    try {
      await saveCameraTags(baseCamera(id), draft);
      refreshStatus();
      onClose();
    } catch (error) {
      say(error.message, true);
      setSaving(false);
    }
  }
  return html`
    <${Dialog} title=${`What ${who} shows`} onClose=${onClose}>
      <p class="note">For the smart cut. Each new take copies it at REC; the editor can change it for one take.</p>
      <${RoleEditor} vocabulary=${vocabulary} tags=${draft} onChange=${setDraft} onAddInstrument=${addOne} />
      <div class="dialog-keys">
        <button class="key" onClick=${onClose}>cancel</button>
        <button class="key signal" disabled=${saving} onClick=${save}><${Icon} name="check" size=${16} />save</button>
      </div>
    <//>`;
}
