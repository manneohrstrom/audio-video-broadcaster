// The cameras view (was "focus", and before that the snapshots page, /snap): see through
// every camera at once, for placing them and setting up (user, 2026-11-17). A grid sized to
// fit the screen, a still from each camera with the take's settings, refreshed on its own.
// Each tile's bar has "adjust": a large panel with the middle of the picture at full
// resolution (the loupe) and the camera's fixed focus: nudge it, find the sharpest, save it.
//
// Same stills as before (no backend change), every 5 s as the old "every 5 s" switch did,
// for 2 minutes after the view opens or "refresh all" is pressed, then it pauses (user,
// 2026-11-17). Each still starts and stops the camera, and cam2's driver froze after ~590
// of those (2026-11-16): the pause keeps a view left open from wearing the cameras out.
// Stills pause while the rig records. Phones send their picture on their next poll (~1 s).
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";
import { focusDistance, focusParts, shownMode } from "../format.js";

// Focus is stored as libcamera's lens position (dioptres, 1 / metres) and shown in metres.
// The nudges step in dioptres too: the same step moves far focus a lot in metres and near
// focus a little, which matches how much sharpness changes.
const NUDGES = [
  { delta: -0.1, label: "◀◀", name: "farther, big step" },
  { delta: -0.02, label: "◀", name: "farther, small step" },
  { delta: 0.02, label: "▶", name: "nearer, small step" },
  { delta: 0.1, label: "▶▶", name: "nearer, big step" },
];
const REFRESH_MS = 5000;            // a new still from every camera this often...
const REFRESH_FOR_MS = 2 * 60000;   // ...for this long after the view opens or "refresh all"

/** A still from a Pi camera, at a lens position (or its saved one): { url, info }. */
async function cameraStill(camera, lens) {
  const response = await fetch(`/api/snap/${camera}.jpg${lens == null ? "" : "?lens=" + lens.toFixed(3)}`);
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || response.status);
  return { url: URL.createObjectURL(await response.blob()), info: JSON.parse(response.headers.get("X-Snap-Info") || "{}") };
}

async function phoneStill(phoneId) {
  const response = await fetch(`/api/snap/phone/${encodeURIComponent(phoneId)}.jpg`);
  if (!response.ok) return null;
  return { url: URL.createObjectURL(await response.blob()), info: JSON.parse(response.headers.get("X-Snap-Info") || "{}") };
}

/** The picture, with the middle third marked (that's what the loupe and sharpness look at). */
function Picture({ still, onLoad }) {
  return html`
    <div class="still">
      ${still ? html`<img src=${still.url} alt="" onLoad=${onLoad} />` : html`<span class="still-none">no picture yet</span>`}
      <i class="still-middle" aria-hidden="true"></i>
    </div>`;
}

/** The middle 640×360 at full resolution: 1:1 on a 1080p still. */
function drawLoupe(canvas, image) {
  if (!canvas || !image) return;
  const context = canvas.getContext("2d");
  context.imageSmoothingEnabled = false;
  context.drawImage(image, (image.naturalWidth - 640) / 2, (image.naturalHeight - 360) / 2, 640, 360, 0, 0, 640, 360);
}

/** The status light's colour for a Pi camera. */
function ledColour(node, mode) {
  return !node.online ? "off" : mode === "recording" ? "red" : mode.startsWith("live") ? "blue" : "green";
}

/** One Pi camera: a tile in the grid; "adjust" opens its focus panel (same state, so the
 * still and the focus being set carry over). */
function CameraTile({ id, node, savedLens, rigIdle, shotRequest, say, onSaved }) {
  const [open, setOpen] = useState(false);
  const [still, setStill] = useState(null);
  const [lens, setLens] = useState(savedLens ?? null);
  const [best, setBest] = useState(null);                 // { lens, sharpness }
  const [busy, setBusy] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [problem, setProblem] = useState(null);
  const loupeRef = useRef(null);
  const lensRef = useRef(lens);
  lensRef.current = lens;

  const cameraMode = (node.status || {}).mode;
  const mode = !node.online ? "offline" : shownMode(cameraMode) || "?";
  const shooting = busy || !!node.shooting || cameraMode === "snapshot";   // this view's still, the calibration watcher's (hub-reported)
  const canShoot = rigIdle && node.online;

  /** Take one still at `at`; remembers the sharpest seen. Returns its info or null. */
  async function shoot(at) {
    setBusy(true);
    setProblem(null);
    try {
      const next = await cameraStill(id, at);
      setStill(previous => { if (previous) URL.revokeObjectURL(previous.url); return next; });
      const sharpness = next.info.sharpness;
      if (sharpness != null) setBest(current => (!current || sharpness > current.sharpness ? { lens: at, sharpness } : current));
      return next.info;
    } catch (error) {
      setProblem(error.message);
      return null;
    } finally {
      setBusy(false);
    }
  }

  // The view's timer and "refresh all" ask every camera for a new still, except one whose
  // adjust panel is open: there the nudges take the stills, and a background one would grey
  // out the controls every 5 s (user: "the buttons pop up intermittently").
  useEffect(() => { if (shotRequest && canShoot && !sweeping && !open) shoot(lensRef.current); }, [shotRequest]);

  async function nudge(delta) {
    const at = Math.max(0, Math.min(15, +((lens ?? savedLens ?? 1.0) + delta).toFixed(3)));
    setLens(at);
    await shoot(at);
  }

  /** Coarse sweep over the useful range (infinity to ~0.5 m), then refine around the best. */
  async function findSharpest() {
    setSweeping(true);
    let top = null;
    const tryAt = async at => {
      setLens(+at.toFixed(3));
      const info = await shoot(+at.toFixed(3));
      if (info && info.sharpness != null && (!top || info.sharpness > top.sharpness)) top = { lens: +at.toFixed(3), sharpness: info.sharpness };
    };
    for (let at = 0; at <= 2.0001; at += 0.25) await tryAt(at);
    const centre = top ? top.lens : 1.0;
    for (const delta of [-0.15, -0.1, -0.05, 0.05, 0.1, 0.15]) if (centre + delta >= 0) await tryAt(centre + delta);
    if (top) {
      setLens(top.lens);
      await shoot(top.lens);
      say(`${id}: sharpest at about ${focusDistance(top.lens)}. Tap save focus to keep it.`);
    }
    setSweeping(false);
  }

  async function save() {
    if (lens == null) return;
    const response = await fetch(`/api/camera/${id}/settings`, { method: "POST", body: JSON.stringify({ lens_position: lens }) });
    const answer = await response.json().catch(() => ({}));
    if (!response.ok) { say(answer.error || "Didn't save.", true); return; }
    onSaved();
    say(`${id}: focus at about ${focusDistance(lens)} saved. Every take uses it from now on.`);
  }

  useEffect(() => {                                       // Esc closes the panel
    if (!open) return undefined;
    const onKey = event => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const locked = !canShoot || busy || sweeping;
  const sharpness = still && still.info.sharpness;
  const isBest = best && sharpness != null && sharpness >= best.sharpness;
  const unsaved = lens != null && lens !== savedLens;
  const shown = lens ?? savedLens;
  const tile = html`
    <article class="camera-tile">
      <${Picture} still=${still} />
      <footer class="tile-bar">
        <i class=${"led " + ledColour(node, mode)} aria-hidden="true"></i>
        <b>${id}</b>
        <span class=${"shooting" + (shooting ? " on" : "")} title=${shooting ? "taking a still" : ""}><${Icon} name="camera" size=${14} /></span>
        <span class="tile-note">${problem ? html`<span class="bad" title=${problem}>no picture</span>`
          : mode !== "idle" ? mode : shown == null ? "focus not set" : `focus ≈ ${focusDistance(shown)}${unsaved ? " (not saved)" : ""}`}</span>
        <button class="key small" disabled=${!node.online} onClick=${() => setOpen(true)}><${Icon} name="magnifier" size=${14} />adjust</button>
      </footer>
    </article>`;
  if (!open) return tile;
  return html`${tile}
    <div class="dialog-backdrop" onClick=${event => { if (event.target === event.currentTarget) setOpen(false); }}>
      <section class="dialog wide focus-panel" role="dialog" aria-label=${id + " focus"}>
        <header><i class=${"led " + ledColour(node, mode)} aria-hidden="true"></i>
          <h2>${id}</h2>
          <span class=${"shooting" + (shooting ? " on" : "")} title=${shooting ? "taking a still" : ""}><${Icon} name="camera" size=${16} /></span>
          <span class="mode">${sweeping ? "hunting for focus…" : mode}</span>
          <button class="round-key" onClick=${() => setOpen(false)} aria-label="Close"><${Icon} name="close" size=${16} /></button></header>
        <div class="focus-body">
          <div class="focus-picture">
            <${Picture} still=${still} onLoad=${event => drawLoupe(loupeRef.current, event.currentTarget)} />
            <p class="note">${problem ? html`<span class="bad">${problem}</span>`
              : still ? `${still.info.source === "live" ? "from the live stream" : "still"} · ${(still.info.ms / 1000).toFixed(1)} s · the dashed box is what sharpness measures` : ""}</p>
          </div>
          <div class="focus-side">
            <canvas class="loupe" ref=${loupeRef} width="640" height="360" title="The middle of the picture at full resolution"></canvas>
            <div class="focus-controls">
            <div class="readout"><span>sharpness</span><b class=${"lcd-number" + (isBest ? " best" : "")}>${sharpness == null ? "---" : Math.round(sharpness)}</b></div>
            <div class="readout"><span>focus</span><b class="lcd-number">${focusParts(lens).value}<small class="lcd-unit">${focusParts(lens).unit}</small></b></div>
            <div class="nudges">
              ${NUDGES.map(({ delta, label, name }) => html`
                <button class="round-key" disabled=${locked} onClick=${() => nudge(delta)} aria-label=${"Focus " + name} title=${"Focus " + name}>
                  ${label}</button>`)}
            </div>
            <div class="nudge-caption"><span>farther</span><span>nearer</span></div>
            <button class="key" disabled=${locked} onClick=${findSharpest} title="Tries focus positions and picks the sharpest middle">
              <${Icon} name="magnifier" size=${16} />find sharpest</button>
            <button class=${"key" + (unsaved ? " signal" : "")} disabled=${locked || lens == null} onClick=${save}>
              <${Icon} name="check" size=${16} />save focus</button>
            <p class="note">${savedLens == null ? "nothing saved yet" : `saved: about ${focusDistance(savedLens)}`}${unsaved ? " · not saved" : ""}</p>
            </div>
          </div>
        </div>
      </section>
    </div>`;
}

function PhoneStill({ id, phone, shotRequest }) {
  const [still, setStill] = useState(null);
  useEffect(() => {
    if (!shotRequest) return undefined;
    const timer = setTimeout(async () => {                // phones send theirs on their next poll
      const next = await phoneStill(id).catch(() => null);
      if (next) setStill(previous => { if (previous) URL.revokeObjectURL(previous.url); return next; });
    }, 1500);
    return () => clearTimeout(timer);
  }, [shotRequest]);
  return html`
    <article class="camera-tile">
      <${Picture} still=${still} />
      <footer class="tile-bar">
        <i class="led green" aria-hidden="true"></i>
        <b>${phone.label || id}</b>
        <span class="tile-note">phone${still ? ` · ${(phone.status || {}).locked ? "settings locked" : "not locked"} · ${still.info.age_s} s old` : ""}</span>
      </footer>
    </article>`;
}

/** Columns for n tiles: 2×2 up to 4, 3×3 up to 9, then 4 across (4×3, 4×4). */
function columnsFor(count) {
  return count <= 1 ? 1 : count <= 4 ? 2 : count <= 9 ? 3 : 4;
}

export function Cameras({ status, say }) {
  const [shotRequest, setShotRequest] = useState(0);      // goes up by one per round of stills
  const [refreshUntil, setRefreshUntil] = useState(() => Date.now() + REFRESH_FOR_MS);
  const [refreshing, setRefreshing] = useState(true);
  const [, setSavedTick] = useState(0);
  const rigIdle = status && status.state === "idle";

  function takeRound() {
    if (!rigIdle) return;
    fetch("/api/snap/phones", { method: "POST" }).catch(() => {});
    setShotRequest(n => n + 1);
  }
  function refreshAll() {                                  // a round now, and 2 more minutes of them
    setRefreshUntil(Date.now() + REFRESH_FOR_MS);
    setRefreshing(true);
    takeRound();
  }
  useEffect(() => { if (rigIdle) takeRound(); }, [Boolean(status), rigIdle]);   // a round when the view opens
  useEffect(() => {
    if (!refreshing) return undefined;
    const timer = setInterval(() => {
      if (Date.now() > refreshUntil) { setRefreshing(false); return; }
      if (!document.hidden) takeRound();
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, [rigIdle, refreshing, refreshUntil]);

  if (!status) return html`<p class="empty">Warming up the valves…</p>`;
  const settings = status.camera_settings || {};
  const nodes = Object.entries(status.nodes || {});
  const phones = Object.entries(status.phones || {}).filter(([, phone]) => phone.online);
  const count = nodes.length + phones.length;
  const columns = columnsFor(count);
  return html`
    <section class="cameras-view" aria-label="Cameras">
      <div class="archive-bar">
        <div class="archive-status">${!rigIdle ? html`<b class="warn">The rig is ${status.state}</b>: pictures pause during a take.`
          : refreshing ? "A new picture from every camera every 5 s, for 2 minutes."
          : "Paused after 2 minutes, to spare the cameras. Refresh all for 2 more."}</div>
        <div class="archive-keys">
          <button class="key" disabled=${!rigIdle} onClick=${refreshAll}><${Icon} name="camera" size=${18} />refresh all</button>
        </div>
      </div>
      <div class="camera-grid" style=${{ "--columns": columns, "--rows": Math.ceil(count / columns) || 1 }}>
        ${nodes.map(([id, node]) => html`
          <${CameraTile} key=${id} id=${id} node=${node} savedLens=${(settings[id] || {}).lens_position}
                         rigIdle=${rigIdle} shotRequest=${shotRequest} say=${say} onSaved=${() => setSavedTick(n => n + 1)} />`)}
        ${phones.map(([id, phone]) => html`<${PhoneStill} key=${id} id=${id} phone=${phone} shotRequest=${shotRequest} />`)}
      </div>
    </section>`;
}
