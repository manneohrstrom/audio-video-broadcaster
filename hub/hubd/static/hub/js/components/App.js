// The hub page: one instrument with three views.
//
//   deck     the big keys: REC, STOP, LIVE; the rig's state; one LED per camera
//   monitor  every device's health, with the last 30 minutes as small charts
//   focus    a still from every camera, the loupe, and each Pi camera's fixed focus (/snap)
//   archive  takes on the STUDIO drive: ingest, playback, songs, videos
//
// The rig's state comes from hubd every second (/api/status), the takes every 3 s
// (/api/ingest), the health history every 10 s. The view is kept in the URL (#monitor).
import { useEffect, useState, useCallback } from "preact/hooks";
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";
import { loadStatus, loadIngest, loadHistory, rigAction } from "../api.js";
import { hms } from "../format.js";
import { Deck } from "./Deck.js";
import { Monitor } from "./Monitor.js";
import { Archive } from "./Archive.js";
import { Focus } from "./Focus.js";
import { QrDialog } from "./QrDialog.js";
import { AudioDialog } from "./AudioDialog.js";
import { Player } from "./Player.js";
import { useCalibrationChime } from "../chime.js";
import { notIngested } from "../takes.js";

const VIEWS = [
  { id: "deck", label: "deck", icon: "deck" },
  { id: "monitor", label: "monitor", icon: "scope" },
  { id: "focus", label: "focus", icon: "camera" },
  { id: "archive", label: "archive", icon: "cassette" },
];

function viewFromHash() {
  const id = location.hash.slice(1) || (location.pathname === "/snap" ? "focus" : "");     // /snap: the old address
  return VIEWS.some(view => view.id === id) ? id : "deck";
}

/** Poll `load` every `ms` while mounted; returns the latest result (or null) and a refresh. */
function usePolling(load, ms, onError) {
  const [value, setValue] = useState(null);
  const refresh = useCallback(() => load().then(setValue, error => onError && onError(error)), [load]);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, ms);
    return () => clearInterval(timer);
  }, [refresh, ms]);
  return [value, refresh];
}

/** What the segment display says: IDLE, LIVE, REC 00:12:03, STARTING, … */
function rigLabel(status) {
  if (!status) return "HELLO";
  const live = Object.values(status.nodes || {}).some(node => node.status && node.status.mode === "live");
  if (status.state === "recording" && status.take) return "REC " + hms(Date.now() / 1000 - status.take.recording_at);
  if (status.state === "idle" && live) return "LIVE";
  return String(status.state || "?").toUpperCase();
}

export function App() {
  const [view, setView] = useState(viewFromHash());
  const [message, setMessage] = useState(null);           // { text, error }
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState(null);             // { kind: "qr" | "audio" | "player", ... }
  const [unreachable, setUnreachable] = useState(false);

  const fetchStatus = useCallback(() => loadStatus(!document.hidden).then(status => { setUnreachable(false); return status; }), []);
  const [status, refreshStatus] = usePolling(fetchStatus, 1000, () => setUnreachable(true));
  const [ingest, refreshIngest] = usePolling(loadIngest, 3000);
  const [history] = usePolling(loadHistory, 10000);
  useCalibrationChime(status);

  useEffect(() => {
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    const onKey = event => { if (event.key === "Escape") setDialog(null); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const go = id => { location.hash = id === "deck" ? "" : id; setView(id); };
  const say = (text, error = false) => setMessage(text ? { text, error } : null);

  /** A rig action with the keys locked meanwhile; hubd's reasons become the message. */
  async function act(path, body, label) {
    setBusy(true);
    say(label + "…");
    try {
      const answer = await rigAction(path, body);
      const errors = answer.errors && Object.entries(answer.errors);
      say(errors && errors.length ? errors.map(([name, error]) => `${name}: ${error}`).join("\n") : null, true);
    } catch (error) {
      say(error.message, true);
    }
    setBusy(false);
    refreshStatus();
    refreshIngest();
  }

  const shown = message || (status && status.message && !busy ? { text: status.message } : null);
  const recording = status && status.state === "recording";
  const context = { status, ingest, history, busy, act, say, refreshIngest, openDialog: setDialog, go };
  return html`
    <div class=${"hub" + (recording ? " is-recording" : "")}>
      <header class="topbar">
        <div class="brand">
          <span class="grille" aria-hidden="true"></span>
          <span class="brand-name">studio multicam</span>
          <small class="version">${status ? "hubd " + status.hub_version : ""}</small>
        </div>
        <${SegmentDisplay} text=${unreachable ? "NO HUB" : rigLabel(status)} state=${unreachable ? "error" : status && status.state} />
        <nav class="views" aria-label="Views">
          ${VIEWS.map(item => html`
            <button class="view-key" aria-current=${view === item.id ? "page" : null} onClick=${() => go(item.id)}>
              <${Icon} name=${item.icon} size=${20} /><span>${item.label}</span>
              <i class=${"view-led " + viewLed(item.id, status, ingest)} aria-hidden="true"></i>
            </button>`)}
        </nav>
      </header>
      ${unreachable && html`<p class="banner error" role="alert">Can't reach the hub. Unplugged? Rebooting? Sulking? Trying again every second.</p>`}
      ${shown && html`<p class=${"banner" + (shown.error ? " error" : "")} role="status">${shown.text}</p>`}
      <main class="view">
        ${view === "deck" && html`<${Deck} ...${context} />`}
        ${view === "monitor" && html`<${Monitor} ...${context} />`}
        ${view === "focus" && html`<${Focus} ...${context} />`}
        ${view === "archive" && html`<${Archive} ...${context} />`}
      </main>
      <footer class="plate-row">
        <span class="model-plate"><span class="grille" aria-hidden="true"></span>SM-1 <small>no cloud · no subscription · no mercy</small></span>
      </footer>
      ${dialog && dialog.kind === "qr" && html`<${QrDialog} onClose=${() => setDialog(null)} />`}
      ${dialog && dialog.kind === "audio" && html`<${AudioDialog} take=${dialog.take} onClose=${() => { setDialog(null); refreshIngest(); }} onChange=${refreshIngest} />`}
      ${dialog && dialog.kind === "player" && html`<${Player} ...${dialog} onSwitch=${next => setDialog({ ...dialog, ...next })} onClose=${() => setDialog(null)} />`}
    </div>`;
}

/** Each view key has an LED: red while recording (deck), amber when a device needs a look
 *  (monitor) or takes wait for ingest (archive), off otherwise. */
function viewLed(id, status, ingest) {
  if (!status) return "";
  if (id === "deck") return status.state === "recording" ? "red" : "";
  if (id === "monitor") {
    const devices = [...Object.values(status.nodes || {}), ...Object.values(status.phones || {})];
    return devices.some(device => device.online && ((device.problems || []).length || device.slow)) ? "amber" : "";
  }
  if (/^ingesting/.test((ingest && ingest.status) || "")) return "green";
  const takes = Object.values((ingest && ingest.takes) || {});
  return takes.some(take => take.recorded && notIngested(take)) ? "amber" : "";
}

/** The 14-segment display: unlit segments faintly behind the lit ones. */
function SegmentDisplay({ text, state }) {
  const width = 12;
  const shown = text.length > width ? text.slice(0, width) : text.padStart(width, "!");
  return html`
    <div class=${"segments " + (state || "")} role="status" aria-label=${"Rig: " + text}>
      <span class="segments-unlit" aria-hidden="true">${"~".repeat(width)}</span>
      <span class="segments-lit" aria-hidden="true">${shown}</span>
    </div>`;
}
