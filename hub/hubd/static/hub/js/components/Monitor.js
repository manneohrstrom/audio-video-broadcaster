// The monitor: one panel per device (the hub, each Pi camera, each phone) with its health,
// calibration, and small charts of the last 30 minutes (Trend.js).
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";
import { fmt, hms, shownMode, when } from "../format.js";
import { setPhoneMount } from "../api.js";
import { Trends } from "./Trend.js";

/** Calibration (Phase 4 Track B): each camera waits for the card and calibrates itself. */
function CalibrationLine({ entry, watching, phone }) {
  if (!entry) return null;
  const at = entry.at ? when(entry.at) : "";
  const values = phone ? ` · ISO ${entry.iso} · ${entry.k} K`
    : entry.gain ? ` · gain ${entry.gain.toFixed(1)} · WB ${entry.awb_gains.map(x => x.toFixed(2)).join("/")}` : "";
  let text, tone = "";
  if (entry.status === "calibrating") { text = "calibrating…"; tone = "warn"; }
  else if (entry.at && (phone || entry.status === "calibrated" || entry.status === "waiting")) {
    text = `✓ calibrated ${at}${values}` + (entry.status === "waiting" && watching ? " · show the card to recalibrate" : "");
    tone = "good";
  } else text = watching ? `Waiting for the card: show it to this ${phone ? "phone" : "camera"}` : "Not calibrated" + (entry.at ? ` (last ${at})` : "");
  return html`<p class=${"calibration " + tone}>${text}${entry.message && html` <span class="warn">${entry.message}</span>`}</p>`;
}

/** A panel: a status LED, the name, what it's doing, then rows of name / value.
 * shooting (cameras only): true while the camera takes a still; false keeps the icon's
 * place, so the header doesn't move when it appears. */
function Panel({ tone, name, mode, shooting, children }) {
  return html`
    <article class=${"panel " + tone}>
      <header><i class=${"led " + LED_FOR[tone]} aria-hidden="true"></i><h2>${name}</h2>
        ${shooting != null && html`<span class=${"shooting" + (shooting ? " on" : "")} title=${shooting ? `${name} is taking a still (the calibration check)` : ""}><${Icon} name="camera" size=${16} /></span>`}
        <span class="mode">${mode}</span></header>
      ${children}
    </article>`;
}
const LED_FOR = { ok: "green", warn: "amber", recording: "red", live: "blue", off: "off" };

function Rows({ rows }) {
  return html`<dl class="rows">${rows.filter(Boolean).map(([name, value, tone]) =>
    html`<dt>${name}</dt><dd class=${tone || ""}>${value}</dd>`)}</dl>`;
}

function HubPanel({ hub, history }) {
  if (!hub || hub.temp_c == null) return null;
  const throttled = hub.throttled;
  const now = throttled != null && (throttled & 0xF), earlier = throttled != null && (throttled & 0xF0000);
  const hot = hub.temp_c >= 75;
  return html`<${Panel} tone=${now || hot ? "warn" : "ok"} name="studio-hub" mode="the brain">
    <${Rows} rows=${[
      ["temperature", `${hub.temp_c} °C (throttles at 80)`, hot ? "bad" : ""],
      ["fan", hub.fan_rpm == null ? "–" : hub.fan_rpm ? hub.fan_rpm + " rpm" : "off"],
      ["CPU", `${hub.cpu_mhz ?? "–"} MHz · load ${hub.load ?? "–"}`],
      ["memory", `${hub.mem_available_mb ?? "–"} MB free of ${hub.mem_total_mb ?? "–"}`, hub.mem_available_mb != null && hub.mem_available_mb < 150 ? "bad" : ""],
      ["swap", `${hub.swap_used_mb ?? "–"} MB used`],
      ["throttling", throttled == null ? "–" : now ? `now (0x${throttled.toString(16)})` : earlier ? `earlier (0x${throttled.toString(16)})` : "none", now ? "bad" : earlier ? "warn" : ""],
      ["SD free", `${hub.free_gb ?? "–"} GB`],
    ]} />
    <${Trends} scope="hub" id="hub" history=${history} totalMemory=${hub.mem_total_mb} />
  <//>`;
}

function CameraPanel({ name, node, calibration, history }) {
  if (!node.online) {
    return html`<${Panel} tone="off" name=${name} mode="offline">
      <p class="note">Asleep, unplugged or off the Wi-Fi. REC goes on without it.</p><//>`;
  }
  const st = node.status || {};
  const mode = shownMode(st.mode);
  const tone = mode === "recording" ? "recording" : mode === "live" ? "live" : (node.problems || []).length || node.slow ? "warn" : "ok";
  const take = st.take;
  const dropped = take ? take.dropped_so_far || 0 : 0;
  const syncUs = take && take.sync_difference_us;
  return html`<${Panel} tone=${tone} name=${name} shooting=${st.mode === "snapshot"}
                       mode=${(mode === "recording" ? "● rec" : mode) + (take ? " · " + take.role : "")}>
    <${Rows} rows=${[
      take && ["take", `${take.take_id} · ${hms(take.elapsed_s)} · ${take.frames ?? take.frames_est} fr`],
      take && ["dropped", dropped ? `${dropped} frames (${take.gaps_so_far} gaps)` : "0", dropped ? "bad" : "good"],
      take && ["sync Δ", syncUs == null ? "–" : (syncUs / 1000).toFixed(3) + " ms", syncUs != null && Math.abs(syncUs) > 1000 ? "bad" : ""],
      ["temperature", fmt(st.temp_c, " °C")],
      ["free", `${fmt(st.free_gb, " GB")} · ${fmt(st.free_hours, " h")}`],
      ["clock", `${st.clock_synced ? "synced" : "not synced"} · ${fmt(st.clock_offset_ms, " ms", 3)}`, st.clock_synced ? "" : "bad"],
      ["Wi-Fi", `${fmt(st.wifi_dbm, " dBm", 0)}${st.wifi_mbps != null ? " · " + fmt(st.wifi_mbps, " Mbit/s", 0) : ""} · power save ${st.wifi_powersave === false ? "off" : "ON"}`,
       (st.wifi_dbm != null && st.wifi_dbm < -65) || st.wifi_powersave !== false ? "bad" : ""],
      st.last_error && ["error", st.last_error, "bad"],
    ]} />
    <${Trends} scope="camera" id=${name} history=${history} />
    <${CalibrationLine} entry=${(calibration.devices || {})[name]} watching=${calibration.watching} />
    ${node.slow && html`<p class="note warn">Slow to answer (busy Wi-Fi?): showing its last status.</p>`}
    ${(node.problems || []).length > 0 && mode !== "recording" && html`<p class="note warn">${node.problems.join(" · ")}</p>`}
    <a class=${"key small" + (mode === "live" ? "" : " off")} href=${`http://${name}.local:8889/cam/`} target="_blank" rel="noopener"
       title=${mode === "live" ? "Open the live picture" : "Turn LIVE on to preview"}>preview</a>
  <//>`;
}

function PhonePanel({ id, phone, calibration, onChange }) {
  const name = phone.label || id;
  if (!phone.online) {
    return html`<${Panel} tone="off" name=${name} mode="gone">
      <p class="note">Page closed or phone off ${Math.round(phone.age_s)} s ago. REC goes on without it.</p><//>`;
  }
  const st = phone.status || {};
  const recording = st.state === "recording";
  const settings = st.settings || {};
  const mode = recording ? "● rec" : { ready: "ready", off: "camera off", finishing: "finishing", uploading: "uploading" }[st.state] || st.state || "–";
  async function mount(choice) { await setPhoneMount(id, choice).catch(() => {}); onChange(); }
  return html`<${Panel} tone=${recording ? "recording" : (phone.problems || []).length ? "warn" : "ok"} name=${name} mode=${mode + " · phone"}>
    <${Rows} rows=${[
      ["files", `${st.part || id}.mov`],
      st.take && ["take", `${st.take} · ${st.frames ?? 0} fr`],
      st.take && ["dropped", st.dropped ? `${st.dropped} frames` : "0", st.dropped ? "bad" : "good"],
      st.take && ["upload", `${st.uploaded ?? 0} pieces · ${st.queue ? st.queue + " waiting" : "up to date"}`, st.queue ? "warn" : ""],
      ["picture", st.locked ? `locked · ISO ${settings.iso ?? "–"} · ${settings.colorTemperature ?? "–"} K` : "not calibrated: locks itself at REC", st.locked ? "" : "warn"],
      ["battery", `${st.battery ?? "–"}%${st.charging ? " · charging" : st.charging === false ? " · not charging" : ""}`, st.charging === false ? "bad" : ""],
      ["clock", st.rtt_ms != null ? `round trip ${st.rtt_ms} ms` : "–"],
      ["model", st.model || "–"],
    ]} />
    <${CalibrationLine} entry=${(calibration.phones || {})[id]} watching=${calibration.watching} phone />
    ${!recording && html`<div class="segmented" role="group" aria-label="Focus">
      ${["tripod", "handheld"].map(choice => html`
        <button aria-pressed=${String((phone.mount || "tripod") === choice)} onClick=${() => mount(choice)}
                title=${choice === "tripod" ? "On a tripod: focus measured once, then locked" : "Handheld: the phone keeps refocusing"}>
          ${choice === "tripod" ? "focus locked" : "focus follows"}</button>`)}
    </div>`}
    ${(phone.problems || []).length > 0 && !recording && html`<p class="note warn">${phone.problems.join(" · ")}</p>`}
  <//>`;
}

export function Monitor({ status, history }) {
  if (!status) return html`<p class="empty">Warming up the valves…</p>`;
  const calibration = status.calibration || {};
  return html`
    <section class="monitor" aria-label="Monitor">
      <${HubPanel} hub=${status.hub} history=${history} />
      ${Object.entries(status.nodes || {}).map(([name, node]) =>
        html`<${CameraPanel} key=${name} name=${name} node=${node} calibration=${calibration} history=${history} />`)}
      ${Object.entries(status.phones || {}).map(([id, phone]) =>
        html`<${PhonePanel} key=${id} id=${id} phone=${phone} calibration=${calibration} onChange=${() => {}} />`)}
    </section>`;
}
