// The deck: what you touch to make a take. Three big keys (REC, STOP, LIVE), a row of LEDs,
// one per camera and phone (tap it to see that device on the monitor), and how much fits.
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";
import { hms, hoursMinutes, shownMode } from "../format.js";
import { Name, cameraName, givenCameraName, nameWithId } from "../names.js";

const LIMIT_NAMES = { hub: "hub SD card" };

/** How long the rig can record, and what limits it (the smallest SD card). */
function budgetText(budget, state) {
  if (!budget || budget.total_s == null) return "";
  const by = LIMIT_NAMES[budget.limited_by] || budget.limited_by + " SD card";
  const who = `${budget.cameras} camera${budget.cameras === 1 ? "" : "s"}` +
    (budget.phones ? ` + ${budget.phones} phone${budget.phones === 1 ? "" : "s"}` : "");
  if (state === "recording") return `${hoursMinutes(budget.remaining_s)} left · stops itself at ${hoursMinutes(budget.total_s)} (${by})`;
  return `≈ ${hoursMinutes(budget.total_s)} of recording space · ${who} · limit: ${by}`;
}

/** How much of this kind of take fits on the STUDIO drive. */
function driveText(budget, state) {
  const drive = budget && budget.drive;
  if (!drive) return budget && budget.total_s != null ? "STUDIO drive: not plugged in (or not read yet)" : "";
  const waiting = drive.waiting_gb ? `, ${drive.waiting_gb} GB waiting for ingest` : "";
  if (state === "recording") return `STUDIO drive: room for ${hoursMinutes(drive.remaining_s)} more of this take (${drive.free_gb} GB free${waiting})`;
  return `STUDIO drive: ≈ ${hoursMinutes(drive.total_s)} of takes fit (${drive.free_gb} GB free${waiting})`;
}

/** The hub's USB mic, which records a scratch track of every take for the automatic sync. */
function micText(scratch) {
  const sc = scratch || {};
  return {
    recording: html`Hub mic: <span class="bad">● recording</span> ${hms(sc.seconds || 0)}`,
    ready: "Hub mic: ready, listening along",
    "no mic": "Hub mic: none plugged in (takes record without it)",
    error: html`Hub mic: <span class="bad">${sc.error || "error"}</span>`,
  }[sc.state] || "";
}

/** One LED per device: red recording, blue live, green fine, amber needs a look, dark offline. */
function deviceLeds(status) {
  const nodes = Object.entries(status.nodes || {}).map(([id, node]) => {
    const mode = node.online ? shownMode((node.status || {}).mode) : "offline";
    const colour = !node.online ? "off" : mode === "recording" ? "red" : mode === "live" ? "blue"
      : (node.problems || []).length || node.slow ? "amber" : "green";
    return { id, label: cameraName(status.names, id), colour, says: node.online ? mode : "offline" };
  });
  const phones = Object.entries(status.phones || {}).map(([id, phone]) => {
    const state = (phone.status || {}).state;
    const colour = !phone.online ? "off" : state === "recording" ? "red" : (phone.problems || []).length ? "amber" : "green";
    return { id, label: cameraName(status.names, id, phone.label), colour, says: phone.online ? state || "ready" : "gone" };
  });
  return [...nodes, ...phones];
}

export function Deck({ status, busy, act, openDialog, go }) {
  if (!status) return html`<p class="empty">Warming up the valves…</p>`;
  const state = status.state;
  const live = Object.values(status.nodes || {}).some(node => node.status && node.status.mode === "live");
  const recording = state === "recording";
  const budgetWarn = status.budget && status.budget.warn;
  const drive = status.budget && status.budget.drive;
  const driveShort = drive && status.budget.total_s != null && drive.total_s < status.budget.total_s;

  return html`
    <section class="deck" aria-label="Deck">
      <div class="big-keys">
        <button id="rec" class=${"big-key rec" + (recording ? " lit" : "")} disabled=${busy || state !== "idle"}
                onClick=${() => act("/api/rec", {}, "Arming cameras")}>
          <i class="key-led" aria-hidden="true"></i>
          <span class="key-glyph"><${Icon} name="recDot" size=${30} /></span>
          <span class="key-name">rec</span><small class="key-caption">${recording ? "rolling" : "roll it"}</small>
        </button>
        <button id="stop" class="big-key stop" disabled=${busy || !recording}
                onClick=${() => act("/api/stop", {}, "Stopping")}>
          <i class="key-led" aria-hidden="true"></i>
          <span class="key-glyph"><${Icon} name="stopSquare" size=${30} /></span>
          <span class="key-name">stop</span><small class="key-caption">that's a wrap</small>
        </button>
        <button id="live" class=${"big-key live" + (live ? " lit" : "")} disabled=${busy || state !== "idle"}
                onClick=${() => act("/api/live", { on: !live }, live ? "Stopping live" : "Starting live")}>
          <i class="key-led" aria-hidden="true"></i>
          <span class="key-glyph"><${Icon} name="antenna" size=${30} /></span>
          <span class="key-name">${live ? "live off" : "live"}</span><small class="key-caption">${live ? "on air" : "go on air"}</small>
        </button>
      </div>

      <div class="device-leds" aria-label="Cameras">
        ${deviceLeds(status).map(device => html`
          <button class="device-led" onClick=${() => go("monitor")} title=${`${nameWithId(givenCameraName(status.names, device.id), device.label, device.id)}: ${device.says}. Tap for details.`}>
            <i class=${"led " + device.colour} aria-hidden="true"></i><span><${Name} given=${givenCameraName(status.names, device.id)} fallback=${device.label} id=${device.id} /></span>
          </button>`)}
      </div>

      <div class="readouts">
        <p class=${budgetWarn ? "warn" : ""} title=${status.budget && status.budget.per ? Object.entries(status.budget.per).map(([k, v]) => `${k}: ${hoursMinutes(v)}`).join("\n") : ""}>
          ${budgetText(status.budget, state)}</p>
        <p class=${driveShort ? "warn" : ""} title="Information only: if the drive fills up, ingest waits for a drive with space; nothing is lost.">
          ${driveText(status.budget, state)}</p>
        <p>${micText(status.scratch)}</p>
      </div>

      <div class="small-keys">
        <button class="key" onClick=${() => openDialog({ kind: "qr" })}><${Icon} name="phone" size=${18} />use a phone as a camera</button>
      </div>
    </section>`;
}
