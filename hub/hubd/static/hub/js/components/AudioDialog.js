// Add a bounce to a take: a new song or the next version of one. Resumable upload in 8 MB
// pieces (hub/hubd/audio.py): pieces wait (409) while a take or LIVE runs and go on by
// themselves; if the page closes mid-upload, the same file for the same song carries on.
import { useEffect, useState } from "preact/hooks";
import { html } from "shared/html.js";
import { Dialog } from "./Dialog.js";
import { loadSongs, newSong, newVersion, finishUpload, chooseVersion, putPiece } from "../api.js";
import { fmtBytes, fmtDur, fmtEta } from "../format.js";
import { takeName } from "../names.js";

const PIECE_BYTES = 8 << 20;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const NEW_SONG = "";

export function AudioDialog({ take, names, onClose, onChange }) {
  const [songs, setSongs] = useState([]);
  const [songId, setSongId] = useState(null);          // null until loaded; "" = a new song
  const [newName, setNewName] = useState("");
  const [file, setFile] = useState(null);
  const [progress, setProgress] = useState(0);
  const [note, setNote] = useState("");
  const [uploading, setUploading] = useState(false);

  async function refresh(keep) {
    const doc = await loadSongs(take).catch(() => ({ songs: [] }));
    const list = doc.songs || [];
    setSongs(list);
    setSongId(current => {
      const wanted = keep !== undefined ? keep : current;
      if (wanted !== null && (wanted === NEW_SONG || list.some(s => s.id === wanted))) return wanted;
      return list.length ? list[list.length - 1].id : NEW_SONG;
    });
    return list;
  }
  useEffect(() => { refresh(); }, [take]);

  async function choose(song, version) {
    try { await chooseVersion(take, song, version); } catch (error) { setNote(error.message); }
    refresh(); onChange();
  }

  async function upload() {
    if (!file) { setNote("Pick a file first."); return; }
    setUploading(true);
    try {
      let song = songId;
      if (song === NEW_SONG) song = (await newSong(take, newName)).id;
      const version = (await newVersion(take, song, file.name, file.size)).version;
      const list = await refresh(song);
      let offset = ((list.find(s => s.id === song) || {}).versions || []).find(v => v.version === version).received || 0;
      const startedAt = Date.now(), startOffset = offset;
      while (offset < file.size) {
        setProgress(offset / file.size);
        let response;
        try { response = await putPiece(take, song, version, offset, file.slice(offset, offset + PIECE_BYTES)); }
        catch (error) { setNote("Network hiccup, trying again…"); await sleep(3000); continue; }
        const answer = await response.json().catch(() => ({}));
        if (response.status === 409) { setNote("Waiting: a take or LIVE is running. The upload goes on afterwards."); await sleep(5000); continue; }
        if (!response.ok) {
          const has = /has (\d+) bytes/.exec(answer.error || "");
          if (has) { offset = +has[1]; continue; }              // the hub has a different amount: go on from there
          setNote(`Upload problem: ${answer.error || response.status}. Trying again…`); await sleep(3000); continue;
        }
        offset = answer.received;
        const rate = (offset - startOffset) / Math.max(1, (Date.now() - startedAt) / 1000);
        setNote(`${fmtBytes(offset)} of ${fmtBytes(file.size)}` + (rate > 0 ? ` · ${fmtEta((file.size - offset) / rate)} left` : ""));
      }
      setProgress(1);
      setNote("Checking the file on the hub…");
      const done = await finishUpload(take, song, version);
      setNote(`✓ ${done.filename}: v${done.version}, ${fmtDur(done.duration_s)}, ${done.sample_rate / 1000} kHz. It goes to the drive with the next ingest.`);
      setFile(null);
    } catch (error) {
      setNote("Upload failed: " + error.message);
    }
    setUploading(false);
    refresh(); onChange();
  }

  return html`
    <${Dialog} title=${`Add a bounce · ${takeName(names, take) || take}`} onClose=${onClose}>
      <div class="song-list">
        ${songs.length ? songs.map(song => html`<p><b>${song.name}</b>: ${song.versions.map((v, i) => html`${i ? " · " : ""}v${v.version} ${v.filename}
          ${v.complete
            ? html`${v.duration_s ? ` (${fmtDur(v.duration_s)})` : ""} ${v.version === song.current
                ? html`<span class="good">✓ used</span>` : html`<button class="key small" onClick=${() => choose(song.id, v.version)}>use this</button>`}`
            : html` <span class="warn">${fmtBytes(v.received)} of ${fmtBytes(v.bytes)}</span>`}`)}</p>`)
          : html`<p class="note">No audio for this take yet.</p>`}
      </div>
      <label class="field">Song
        <select value=${songId ?? NEW_SONG} onChange=${event => setSongId(event.target.value)}>
          ${songs.map(song => html`<option value=${song.id}>${song.name} (new version)</option>`)}
          <option value=${NEW_SONG}>New song…</option>
        </select></label>
      ${songId === NEW_SONG && html`<label class="field">Name <input type="text" value=${newName} maxlength="60"
        placeholder="Name of the new song (default: Track N)" onInput=${event => setNewName(event.target.value)} /></label>`}
      <label class="field">Bounce (WAV, AIFF, FLAC, MP3, M4A)
        <input type="file" accept=".wav,.aif,.aiff,.flac,.mp3,.m4a,.ogg,audio/*" onChange=${event => setFile(event.target.files[0] || null)} /></label>
      <div class="meter wide"><i style=${{ width: (progress * 100).toFixed(1) + "%" }}></i></div>
      <p class="note">${note}</p>
      <div class="dialog-keys"><button class="key signal" disabled=${uploading} onClick=${upload}>upload</button></div>
      <p class="note">Each upload to an existing song becomes its next version; every version is kept.</p>
    <//>`;
}
