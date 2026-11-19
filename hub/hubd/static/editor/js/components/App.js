// The editor page, step 1 (the cut): header, video, transport, splitter, timeline, footer.
// Loads the project from the hub, then hands one EditorStore to everything below.
// In Sync mode (sync by hand) the header is the SyncBar and there are no steps.
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
import { TitlePanel } from "./TitlePanel.js";
import { MakeVideo } from "./MakeVideo.js";
import { SyncBar } from "./SyncBar.js";
import { Icon } from "shared/icons.js";
import { formatSeconds } from "../time-format.js";

export function App({ takeId, songId, version, mode = "edit" }) {
  const [project, setProject] = useState(null);
  const [problem, setProblem] = useState(null);

  useEffect(() => {
    loadProject(takeId, songId, version, mode).then(setProject, error => setProblem(error.message));
  }, [takeId, songId, version, mode]);

  if (problem) return html`<p class="message problem">Can't open this song: ${problem}</p>`;
  if (!project) return html`<p class="message">Opening the pod bay doors…</p>`;
  return html`<${Editor} project=${project} />`;
}

/** Sync mode for this bounce version: check or fix where the bounce sits, by hand. */
async function openSyncMode(store) {
  if (store.saveState !== "saved") await store.saveNow();
  const query = new URLSearchParams(location.search);
  query.set("mode", "sync");
  location.search = query.toString();
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
    fetch("/api/overlays").then(r => r.json()).then(d => { store.overlays = d.overlays || []; store.notify(); }).catch(() => {});
    fetch(project.bounce.waveform_url).then(r => (r.ok ? r.json() : null)).then(setWaveform).catch(() => {});
    transport.player.load().catch(error => store.showToast(`The bounce didn't load: ${error.message}`));
  }, [project]);
  useEffect(() => {                                   // don't lose an edit when the tab closes
    const flush = event => {
      if (store.saveState === "unsaved") store.saveNow();
      if (store.syncMode && store.bounceMovedSeconds) {      // ask before losing a placement
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", flush);
    return () => window.removeEventListener("beforeunload", flush);
  }, [store]);

  const timeline = store.step === "cut" || store.syncMode
    ? html`<${Timeline} key="cut" store=${store} transport=${transport} thumbnailsByCamera=${thumbnailsByCamera} waveform=${waveform} />`
    : html`<${Timeline} key="look" compact store=${store} transport=${transport} thumbnailsByCamera=${thumbnailsByCamera} waveform=${waveform} />`;
  const saveLabel = { saved: "Saved", unsaved: "Not saved yet", saving: "Saving…", error: "Not saved" }[store.saveState];
  return html`
    <div class=${"editor" + (store.step !== "cut" && !store.syncMode ? " look-step" : "")}
         style=${videoHeight ? { "--video-area-height": `${videoHeight}px` } : null}>
      ${store.syncMode ? html`<${SyncBar} store=${store} />` : html`
      <header class="editor-header">
        <div class="song-title">
          <strong>${project.song.name}</strong>
          <span class="details">${project.take_name ? `${project.take_name} (${project.take_id})` : `take ${project.take_id}`} · ${formatSeconds(store.songSeconds)}</span>
          ${project.bounce_versions.length > 1
            ? html`<label class="bounce-choice">bounce
                <select aria-label="Bounce version" value=${project.version}
                        onChange=${event => switchBounce(store, Number(event.target.value))}>
                  ${project.bounce_versions.map(b => html`<option value=${b.version}>v${b.version}</option>`)}
                </select></label>`
            : html`<span class="details">bounce v${project.version}</span>`}
        </div>
        <nav class="steps" aria-label="Steps">
          <button aria-current=${store.step === "cut" ? "step" : null} onClick=${() => store.setStep("cut")}><${Icon} name="scissors" />Cut</button>
          <button aria-current=${store.step === "look" ? "step" : null} onClick=${() => store.setStep("look")}><${Icon} name="eye" />Look</button>
          <button aria-current=${store.step === "title" ? "step" : null} onClick=${() => store.setStep("title")}><${Icon} name="title" />Title</button>
        </nav>
        <div class="header-actions">
          <span class=${"save-state " + store.saveState} title=${store.saveProblem || ""}>${saveLabel}</span>
          <button class="round-key" disabled=${!store.history.canUndo} onClick=${() => store.undo()} aria-label="Undo" title="Undo (⌘Z)"><${Icon} name="undo" /></button>
          <button class="round-key" disabled=${!store.history.canRedo} onClick=${() => store.redo()} aria-label="Redo" title="Redo (⇧⌘Z)"><${Icon} name="redo" /></button>
          <button class="button" onClick=${() => openSyncMode(store)}
                  title=${`Check or fix where the bounce sits (sync: ${project.bounce.sync.anchor || "by hand"}${project.bounce.sync.confidence ? ", confidence " + project.bounce.sync.confidence : ""})`}><${Icon} name="tuningFork" size=${16} />Sync</button>
          <${MakeVideo} store=${store} />
        </div>
      </header>`}
      <div class="video-area">
        <div class="video-column">
          <${VideoWindow} store=${store} thumbnailsByCamera=${thumbnailsByCamera} player=${player} />
          <${TransportBar} store=${store} transport=${transport} />
        </div>
        ${store.step === "look" && !store.syncMode && html`<${LookPanel} store=${store} />`}
        ${store.step === "title" && !store.syncMode && html`<${TitlePanel} store=${store} transport=${transport} />`}
      </div>
      ${(store.step === "cut" || store.syncMode) && html`<${Splitter} onResize=${setVideoHeight} />`}
      <div class="below-splitter">${timeline}</div>
      ${store.syncMode ? html`
      <footer class="editor-footer">
        <span class="shortcuts">
          Pick a camera that shows a hit (a drum stroke, a clap) and find it with <kbd>←</kbd><kbd>→</kbd>;
          then move the bounce until the sound lands on it: drag the waveform ·
          <kbd>,</kbd><kbd>.</kbd> 10 ms · <kbd>⇧</kbd><kbd>,</kbd><kbd>.</kbd> 1 frame ·
          <kbd>1</kbd>–<kbd>${store.cameras.length}</kbd> camera · <kbd>space</kbd> play
        </span>
        <${ModelPlate} />
      </footer>` : html`
      <footer class="editor-footer">
        <label for="dissolve">Dissolve
          <input id="dissolve" type="range" min="0" max="0.8" step="0.05" value=${store.dissolveSeconds}
                 onInput=${event => store.setDissolve(Number(event.target.value))} />
          <output>${store.dissolveSeconds.toFixed(2)} s</output>
        </label>
        <label for="branding" title="The white border and the Brockley Live logo, on top of everything">
          <input id="branding" type="checkbox" checked=${store.branding}
                 onChange=${event => store.setBranding(event.target.checked)} />
          Border & logo
        </label>
        <input type="color" class="brand-colour" value=${store.brandingColour} disabled=${!store.branding}
               aria-label="Colour of the border and logo" title="Colour of the border and logo"
               onInput=${event => store.setBrandingColour(event.target.value)} />
        ${store.branding && store.brandingColour !== "#ffffff" && html`
          <button class="text-button" onClick=${() => store.setBrandingColour("#ffffff")}>back to white</button>`}
        <span class="shortcuts">
          drag on a camera to use it there · drag a shot to slide it · <kbd>space</kbd> play ·
          <kbd>1</kbd>–<kbd>${store.cameras.length}</kbd> camera from the playhead · <kbd>←</kbd><kbd>→</kbd> frame ·
          <kbd>⌫</kbd> remove cut · <kbd>⌘Z</kbd> undo
        </span>
        <${ModelPlate} />
      </footer>`}
      ${store.toast && html`<div class="toast" role="status">${store.toast}</div>`}
    </div>`;
}

/** The little plate on the instrument's corner, as on the back of a 1970s radio. */
function ModelPlate() {
  return html`<span class="model-plate" title="Studio Multicam editor · made at home, not on Jupiter">
    <span class="grille" aria-hidden="true"></span>SM-1 <small>multicam</small></span>`;
}
