// The focus view (was the snapshots page, /snap): a still from every camera with the
// take's settings, the middle of each Pi camera's picture at full resolution (the loupe),
// and its fixed focus: nudge it, let it find the sharpest, save it for every take.
//
// Stills pause while the rig records. Phones send their picture on their next poll (~1 s).
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";

const LENS_HINT = "0 = far away, 0.5 ≈ 2 m, 1 ≈ 1 m";
const AUTO_REFRESH_MS = 5000;

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

function CameraFocus({ id, node, savedLens, rigIdle, shotRequest, say, onSaved }) {
  const [still, setStill] = useState(null);
  const [lens, setLens] = useState(savedLens ?? null);
  const [best, setBest] = useState(null);                 // { lens, sharpness }
  const [busy, setBusy] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [problem, setProblem] = useState(null);
  const loupeRef = useRef(null);
  const lensRef = useRef(lens);
  lensRef.current = lens;

  const mode = !node.online ? "offline" : (node.status || {}).mode || "?";
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

  // "Refresh all" (and the 5 s timer) ask every camera for a new still.
  useEffect(() => { if (shotRequest && canShoot && !sweeping) shoot(lensRef.current); }, [shotRequest]);

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
      say(`${id}: sharpest at ${top.lens.toFixed(2)}. Tap save focus to keep it.`);
    }
    setSweeping(false);
  }

  async function save() {
    if (lens == null) return;
    const response = await fetch(`/api/camera/${id}/settings`, { method: "POST", body: JSON.stringify({ lens_position: lens }) });
    const answer = await response.json().catch(() => ({}));
    if (!response.ok) { say(answer.error || "Didn't save.", true); return; }
    onSaved();
    say(`${id}: focus ${lens.toFixed(2)} saved. Every take uses it from now on.`);
  }

  const locked = !canShoot || busy || sweeping;
  const sharpness = still && still.info.sharpness;
  const isBest = best && sharpness != null && sharpness >= best.sharpness;
  const unsaved = lens != null && lens !== savedLens;
  return html`
    <article class="panel focus-panel">
      <header><i class=${"led " + (!node.online ? "off" : mode === "recording" ? "red" : mode.startsWith("live") ? "blue" : "green")} aria-hidden="true"></i>
        <h2>${id}</h2><span class="mode">${sweeping ? "hunting for focus…" : busy ? "taking a still…" : mode}</span></header>
      <${Picture} still=${still} onLoad=${event => drawLoupe(loupeRef.current, event.currentTarget)} />
      <p class="note">${problem ? html`<span class="bad">${problem}</span>`
        : still ? `${still.info.source === "live" ? "from the live stream" : "still"} · ${(still.info.ms / 1000).toFixed(1)} s` : ""}</p>
      <div class="focus-tools">
        <canvas class="loupe" ref=${loupeRef} width="640" height="360" title="The middle of the picture at full resolution"></canvas>
        <div class="focus-controls">
          <div class="readout"><span>sharpness</span><b class=${"lcd-number" + (isBest ? " best" : "")}>${sharpness == null ? "---" : Math.round(sharpness)}</b></div>
          <div class="readout"><span>focus</span><b class="lcd-number">${lens == null ? "auto" : lens.toFixed(2)}</b></div>
          <div class="nudges">
            ${[-0.1, -0.02, 0.02, 0.1].map(delta => html`
              <button class="round-key" disabled=${locked} onClick=${() => nudge(delta)} aria-label=${`Focus ${delta > 0 ? "nearer" : "farther"} by ${Math.abs(delta)}`}>
                ${delta > 0 ? "+" : "−"}${Math.abs(delta) === 0.1 ? ".1" : ".02"}</button>`)}
          </div>
          <button class="key" disabled=${locked} onClick=${findSharpest} title="Tries focus positions and picks the sharpest middle">
            <${Icon} name="magnifier" size=${16} />find sharpest</button>
          <button class=${"key" + (unsaved ? " signal" : "")} disabled=${locked || lens == null} onClick=${save}>
            <${Icon} name="check" size=${16} />save focus</button>
        </div>
      </div>
      <p class="note">${savedLens == null ? "nothing saved yet" : `saved: ${savedLens.toFixed(2)}`}${unsaved ? " · not saved" : ""} · ${LENS_HINT}</p>
    </article>`;
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
    <article class="panel focus-panel">
      <header><i class="led green" aria-hidden="true"></i><h2>${phone.label || id}</h2><span class="mode">phone</span></header>
      <${Picture} still=${still} />
      <p class="note">${still ? `${(phone.status || {}).locked ? "settings locked" : "not locked"} · picture ${still.info.age_s} s old` : ""}</p>
    </article>`;
}

export function Focus({ status, say }) {
  const [shotRequest, setShotRequest] = useState(0);      // goes up by one per "refresh all"
  const [auto, setAuto] = useState(false);
  const [, setSavedTick] = useState(0);
  const rigIdle = status && status.state === "idle";

  function refreshAll() {
    if (!rigIdle) return;
    fetch("/api/snap/phones", { method: "POST" }).catch(() => {});
    setShotRequest(n => n + 1);
  }
  useEffect(() => { if (rigIdle) refreshAll(); }, [Boolean(status)]);       // a first round when the view opens
  useEffect(() => {
    if (!auto) return undefined;
    const timer = setInterval(() => { if (!document.hidden) refreshAll(); }, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [auto, rigIdle]);

  if (!status) return html`<p class="empty">Warming up the valves…</p>`;
  const settings = status.camera_settings || {};
  const phones = Object.entries(status.phones || {}).filter(([, phone]) => phone.online);
  return html`
    <section class="focus-view" aria-label="Focus">
      <div class="archive-bar">
        <div class="archive-status">${rigIdle ? "Stills with the take's settings. The dashed box is what sharpness measures."
          : html`<b class="warn">The rig is ${status.state}</b>: stills pause during a take.`}</div>
        <div class="archive-keys">
          <div class="segmented" role="group" aria-label="Refresh every 5 seconds">
            <button aria-pressed=${String(!auto)} onClick=${() => setAuto(false)}>by hand</button>
            <button aria-pressed=${String(auto)} onClick=${() => setAuto(true)}>every 5 s</button>
          </div>
          <button class="key" disabled=${!rigIdle} onClick=${refreshAll}><${Icon} name="camera" size=${18} />refresh all</button>
        </div>
      </div>
      <div class="focus-grid">
        ${Object.entries(status.nodes || {}).map(([id, node]) => html`
          <${CameraFocus} key=${id} id=${id} node=${node} savedLens=${(settings[id] || {}).lens_position}
                          rigIdle=${rigIdle} shotRequest=${shotRequest} say=${say} onSaved=${() => setSavedTick(n => n + 1)} />`)}
        ${phones.map(([id, phone]) => html`<${PhoneStill} key=${id} id=${id} phone=${phone} shotRequest=${shotRequest} />`)}
      </div>
    </section>`;
}
