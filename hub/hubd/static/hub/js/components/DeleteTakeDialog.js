// "Delete take" from the archive (user, 2026-11-21: with YouTube the drive rarely leaves the
// hub, so takes are cleared from here). It says what goes, then asks hubd; hub-ingest deletes
// the take's folder on the STUDIO drive and everything the hub keeps for it when the rig is quiet.
import { html } from "shared/html.js";
import { useState } from "preact/hooks";
import { Icon } from "shared/icons.js";
import { Dialog } from "./Dialog.js";
import { deleteTake } from "../api.js";
import { fmtBytes, takeStart } from "../format.js";
import { nameWithId, takeName } from "../names.js";
import { camerasOf } from "../takes.js";

/** What's in the take, in a few words: "3 cameras (4.2 GB) · 2 songs, 3 bounces · 2 videos". */
function contents(take) {
  const cameras = camerasOf(take);
  const bytes = cameras.reduce((sum, [, v]) => sum + (v.mov_bytes || ((v.done || []).includes("written") ? 0 : v.src_bytes || 0)), 0);
  const songs = (take.audio || {}).songs || [];
  const bounces = songs.reduce((sum, song) => sum + song.versions.length, 0);
  const videos = songs.reduce((sum, song) => sum + (song.videos || []).length, 0);
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  return [
    cameras.length ? plural(cameras.length, "camera") + (bytes ? ` (${fmtBytes(bytes)})` : "") : "",
    songs.length ? `${plural(songs.length, "song")}, ${plural(bounces, "bounce")}` : "",
    videos ? plural(videos, "video") : "",
  ].filter(Boolean).join(" · ");
}

export function DeleteTakeDialog({ id, take, names, onClose, onDeleted }) {
  const [asking, setAsking] = useState(false);
  const [refusal, setRefusal] = useState("");
  const onYouTube = ((take.audio || {}).songs || []).some(song => (song.videos || []).some(v => v.youtube && v.youtube.status === "done"));
  const confirm = async () => {
    setAsking(true);
    try {
      await deleteTake(id);
      onDeleted();
    } catch (error) {
      setRefusal(error.message);
      setAsking(false);
    }
  };
  return html`
    <${Dialog} title="Delete this take?" onClose=${onClose}>
      <p><b>${nameWithId(takeName(names, id), id, id)}</b> · ${takeStart(id)}</p>
      <p class="note">${contents(take) || "no files"}</p>
      <p>This deletes the take's folder on the STUDIO drive: every camera, the bounces, the edits and the
        finished videos, everything the hub keeps for it, and any recording still left on the cameras.
        It can't be undone.</p>
      ${onYouTube && html`<p class="note">Videos already on YouTube stay on the channel.</p>`}
      ${refusal && html`<p class="note bad">Can't delete it now: ${refusal}</p>`}
      <div class="dialog-keys">
        <button class="key" onClick=${onClose}>keep it</button>
        <button class="key danger" disabled=${asking} onClick=${confirm}><${Icon} name="trash" size=${16} />delete take</button>
      </div>
    <//>`;
}
