// The hub page: one instrument with four views.
//
//   deck     the big keys: REC, STOP, LIVE; the rig's state; one LED per camera
//   monitor  every device's health, with the last 30 minutes as small charts
//   cameras  see through every camera (a grid of stills, every 5 s); "adjust" opens a
//            camera's focus panel: the loupe and its fixed focus (/snap, #focus: old addresses)
//   archive  takes on the STUDIO drive: ingest, playback, songs, videos
//
// The rig's state comes from hubd every second (/api/status), the takes every 3 s
// (/api/ingest), the health history every 10 s. The view is kept in the URL (#monitor).
// On a wide screen (user, 2026-11-17) deck and archive sit side by side as one "studio"
// view; monitor and cameras stay on their own.
import { useEffect, useState, useCallback } from "preact/hooks";
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";
import { loadStatus, loadIngest, loadHistory, rigAction } from "../api.js";
import { hms } from "../format.js";
import { Deck } from "./Deck.js";
import { Monitor } from "./Monitor.js";
import { Archive } from "./Archive.js";
import { Cameras } from "./Cameras.js";
import { QrDialog } from "./QrDialog.js";
import { AudioDialog } from "./AudioDialog.js";
import { Player } from "./Player.js";
import { useCalibrationChime } from "../chime.js";
import { notIngested } from "../takes.js";

const VIEWS = [
  { id: "deck", label: "deck", icon: "deck" },
  { id: "monitor", label: "monitor", icon: "scope" },
  { id: "cameras", label: "cameras", icon: "camera" },
  { id: "archive", label: "archive", icon: "cassette" },
];

function viewFromHash() {
  let id = location.hash.slice(1) || (location.pathname === "/snap" ? "cameras" : "");     // /snap: the old address
  if (id === "focus") id = "cameras";                                                         // the view's old name
  return VIEWS.some(view => view.id === id) ? id : "deck";
}

const SIDE_BY_SIDE = "(min-width: 1280px)";
const PAIRED = ["deck", "archive"];

/** True while the window is wide enough for deck and archive side by side. */
function useWideScreen() {
  const [wide, setWide] = useState(() => window.matchMedia(SIDE_BY_SIDE).matches);
  useEffect(() => {
    const query = window.matchMedia(SIDE_BY_SIDE);
    const onChange = () => setWide(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return wide;
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

/** True while a Pi camera or phone is calibrating from the card (between the start blip and the chime). */
function anyCalibrating(calibration) {
  if (!calibration) return false;
  const entries = [...Object.values(calibration.devices || {}), ...Object.values(calibration.phones || {})];
  return entries.some(entry => entry && entry.status === "calibrating");
}

/** What the segment display says: IDLE, LIVE, CALIBRATING, REC 00:12:03, STARTING, … */
function rigLabel(status) {
  if (!status) return "HELLO";
  const live = Object.values(status.nodes || {}).some(node => node.status && node.status.mode === "live");
  if (status.state === "recording" && status.take) return "REC " + hms(Date.now() / 1000 - status.take.recording_at);
  if (status.state === "idle" && anyCalibrating(status.calibration)) return "CALIBRATING";
  if (status.state === "idle" && live) return "LIVE";
  return String(status.state || "?").toUpperCase();
}

export function App() {
  const [view, setView] = useState(viewFromHash());
  const [message, setMessage] = useState(null);           // { text, error }
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState(null);             // { kind: "qr" | "audio" | "player", ... }
  const [unreachable, setUnreachable] = useState(false);
  const wide = useWideScreen();

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
  const sideBySide = wide && PAIRED.includes(view);
  const isCurrent = id => (sideBySide ? PAIRED.includes(id) : view === id);
  const context = { status, ingest, history, busy, act, say, refreshIngest, openDialog: setDialog, go };
  return html`
    <div class=${"hub" + (recording ? " is-recording" : "") + (sideBySide ? " side-by-side" : "")}>
      <header class="topbar">
        <div class="brand">
          <span class="brand-icon"><${Icon} name="filmCamera" size=${22} /></span>
          <span class="brand-name">Brockley Live</span>
          <small class="version">${status ? "hubd " + status.hub_version : ""}</small>
        </div>
        <${SegmentDisplay} text=${unreachable ? "NO HUB" : rigLabel(status)} state=${unreachable ? "error" : status && status.state} />
        <nav class="views" aria-label="Views">
          ${VIEWS.map(item => html`
            <button class="view-key" aria-current=${isCurrent(item.id) ? "page" : null} onClick=${() => go(item.id)}>
              <${Icon} name=${item.icon} size=${20} /><span>${item.label}</span>
              <i class=${"view-led " + viewLed(item.id, status, ingest)} aria-hidden="true"></i>
            </button>`)}
        </nav>
      </header>
      ${unreachable && html`<p class="banner error" role="alert">Can't reach the hub. Unplugged? Rebooting? Sulking? Trying again every second.</p>`}
      ${shown && html`<p class=${"banner" + (shown.error ? " error" : "")} role="status">${shown.text}</p>`}
      <main class="view">
        ${sideBySide && html`<div class="studio"><${Deck} ...${context} /><${Archive} ...${context} /></div>`}
        ${!sideBySide && view === "deck" && html`<${Deck} ...${context} />`}
        ${view === "monitor" && html`<${Monitor} ...${context} />`}
        ${view === "cameras" && html`<${Cameras} ...${context} />`}
        ${!sideBySide && view === "archive" && html`<${Archive} ...${context} />`}
      </main>
      <footer class="plate-row">
        <span class="model-plate"><span class="grille" aria-hidden="true"></span>SM-1 <small>Jazz – Funk – Dub</small></span>
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

const SEGMENT_CELLS = 12;                 // full-width characters the display holds
const NARROW = new Set([":", "."]);       // narrow glyphs in DSEG: they take no cell

/** The 14-segment display: unlit segments faintly behind the lit ones.
 * Colons are narrow in the segment font, so the unlit layer copies the lit text's colons
 * (otherwise its full-width cells stick out past the box: "REC 00:12:03", user 2026-11-17),
 * and an invisible sizer holds the widest possible text so the box never changes size. */
function SegmentDisplay({ text, state }) {
  const lit = fitCells(text.replace(/ /g, "!"));               // "!" is DSEG's full-width blank
  const unlit = [...lit].map(c => (NARROW.has(c) ? c : "~")).join("");
  return html`
    <div class=${"segments " + (state || "")} role="status" aria-label=${"Rig: " + text}>
      <span class="segments-sizer" aria-hidden="true">${"~".repeat(SEGMENT_CELLS)}::</span>
      <span class="segments-unlit" aria-hidden="true">${unlit}</span>
      <span class="segments-lit" aria-hidden="true">${lit}</span>
    </div>`;
}

/** The text cut or padded (blanks on the left) to exactly SEGMENT_CELLS full-width cells. */
function fitCells(text) {
  let cells = 0, out = "";
  for (const c of text) {
    const wide = !NARROW.has(c);
    if (wide && cells === SEGMENT_CELLS) break;
    out += c;
    if (wide) cells += 1;
  }
  return "!".repeat(SEGMENT_CELLS - cells) + out;
}
