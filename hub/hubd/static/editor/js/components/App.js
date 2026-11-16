// The editor page, step 1 (the cut): header, video, transport, splitter, timeline, footer.
// Loads the project from the hub, then hands one EditorStore to everything below.
import { useEffect, useMemo, useState } from "preact/hooks";
import { html } from "./html.js";
import { loadProject } from "../api.js";
import { EditorStore } from "../editor-store.js";
import { Transport } from "../transport.js";
import { VideoPlayer } from "../media/video-player.js";
import { CameraThumbnails } from "../media/thumbnails.js";
import { attachKeyboard } from "../keyboard.js";
import { VideoWindow } from "./VideoWindow.js";
import { TransportBar } from "./TransportBar.js";
import { Splitter, savedVideoHeight } from "./Splitter.js";
import { Timeline } from "./Timeline.js";
import { LookPanel } from "./LookPanel.js";
import { MakeVideo } from "./MakeVideo.js";
import { formatSeconds } from "../time-format.js";

export function App({ takeId, songId, version }) {
  const [project, setProject] = useState(null);
  const [problem, setProblem] = useState(null);

  useEffect(() => {
    loadProject(takeId, songId, version).then(setProject, error => setProblem(error.message));
  }, [takeId, songId, version]);

  if (problem) return html`<p class="message problem">Can't open this song: ${problem}</p>`;
  if (!project) return html`<p class="message">Opening the song…</p>`;
  return html`<${Editor} project=${project} />`;
}

/** Open the song with another bounce version: the edit belongs to the song, so it comes along. */
async function switchBounce(store, version) {
  if (store.saveState !== "saved") await store.saveNow();
  const query = new URLSearchParams(location.search);
  query.set("v", String(version));
  location.search = query.toString();
}

function Editor({ project }) {
  const store = useMemo(() => new EditorStore(project), [project]);
  const transport = useMemo(() => new Transport(store), [store]);
  const player = useMemo(() => new VideoPlayer(store), [store]);
  useEffect(() => () => player.close(), [player]);
  window.editorDebug = { store, player, transport };       // for the browser console and automated checks
  const [, rerender] = useState(0);
  const [videoHeight, setVideoHeight] = useState(savedVideoHeight());
  const [waveform, setWaveform] = useState(null);
  const thumbnailsByCamera = useMemo(() => new Map(store.cameras.map(camera =>
    [camera.id, new CameraThumbnails(camera.thumbnails_url, () => {})])), [store]);

  useEffect(() => store.subscribe(() => rerender(n => n + 1)), [store]);
  useEffect(() => attachKeyboard(store, transport), [store, transport]);
  useEffect(() => {
    fetch("/editor/looks.json").then(r => r.json()).then(looks => { store.looks = looks; store.notify(); }).catch(() => {});
    fetch(project.bounce.waveform_url).then(r => (r.ok ? r.json() : null)).then(setWaveform).catch(() => {});
    transport.player.load().catch(error => store.showToast(`The bounce didn't load: ${error.message}`));
  }, [project]);
  useEffect(() => {                                   // don't lose an edit when the tab closes
    const flush = () => { if (store.saveState === "unsaved") store.saveNow(); };
    window.addEventListener("beforeunload", flush);
    return () => window.removeEventListener("beforeunload", flush);
  }, [store]);

  const saveLabel = { saved: "Saved", unsaved: "Not saved yet", saving: "Saving…", error: "Not saved" }[store.saveState];
  return html`
    <div class=${"editor" + (store.step === "look" ? " look-step" : "")}
         style=${videoHeight ? { "--video-area-height": `${videoHeight}px` } : null}>
      <header class="editor-header">
        <div class="song-title">
          <strong>${project.song.name}</strong>
          <span class="details">take ${project.take_id} · ${formatSeconds(store.songSeconds)}</span>
          ${project.bounce_versions.length > 1
            ? html`<label class="bounce-choice">bounce
                <select aria-label="Bounce version" value=${project.version}
                        onChange=${event => switchBounce(store, Number(event.target.value))}>
                  ${project.bounce_versions.map(b => html`<option value=${b.version}>v${b.version}</option>`)}
                </select></label>`
            : html`<span class="details">bounce v${project.version}</span>`}
        </div>
        <nav class="steps" aria-label="Steps">
          <button aria-current=${store.step === "cut" ? "step" : null} onClick=${() => store.setStep("cut")}><span class="step-number">1</span>Cut</button>
          <button aria-current=${store.step === "look" ? "step" : null} onClick=${() => store.setStep("look")}><span class="step-number">2</span>Look</button>
        </nav>
        <div class="header-actions">
          <span class=${"save-state " + store.saveState} title=${store.saveProblem || ""}>${saveLabel}</span>
          <button class="button" disabled=${!store.history.canUndo} onClick=${() => store.undo()} title="Undo (⌘Z)">Undo</button>
          <button class="button" disabled=${!store.history.canRedo} onClick=${() => store.redo()} title="Redo (⇧⌘Z)">Redo</button>
          <${MakeVideo} store=${store} />
        </div>
      </header>
      <div class="video-area">
        <div class="video-column">
          <${VideoWindow} store=${store} thumbnailsByCamera=${thumbnailsByCamera} player=${player} />
          <${TransportBar} store=${store} transport=${transport} />
        </div>
        ${store.step === "look" && html`<${LookPanel} store=${store} />`}
      </div>
      ${store.step === "cut" && html`<${Splitter} onResize=${setVideoHeight} />`}
      <div class="below-splitter">
        ${store.step === "cut"
          ? html`<${Timeline} key="cut" store=${store} transport=${transport} thumbnailsByCamera=${thumbnailsByCamera} waveform=${waveform} />`
          : html`<${Timeline} key="look" compact store=${store} transport=${transport} thumbnailsByCamera=${thumbnailsByCamera} waveform=${waveform} />`}
      </div>
      <footer class="editor-footer">
        <label for="dissolve">Dissolve
          <input id="dissolve" type="range" min="0" max="0.8" step="0.05" value=${store.dissolveSeconds}
                 onInput=${event => store.setDissolve(Number(event.target.value))} />
          <output>${store.dissolveSeconds.toFixed(2)} s</output>
        </label>
        <span class="shortcuts">
          drag on a camera to use it there · drag a shot to slide it · <kbd>space</kbd> play ·
          <kbd>1</kbd>–<kbd>${store.cameras.length}</kbd> camera from the playhead · <kbd>←</kbd><kbd>→</kbd> frame ·
          <kbd>⌫</kbd> remove cut · <kbd>⌘Z</kbd> undo
        </span>
      </footer>
      ${store.toast && html`<div class="toast" role="status">${store.toast}</div>`}
    </div>`;
}
