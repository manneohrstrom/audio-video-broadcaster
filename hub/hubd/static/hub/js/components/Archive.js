// The archive: every take hub-ingest knows, newest first. A take's card has two parts
// (user, 2026-11-16): the recording on top (cameras and their ingest steps, the hub mic),
// and under a divider the music: one group per song with its bounces, sync, "Edit video"
// and the videos made in the editor.
//
// The list follows the STUDIO drive (docs/stick-db-plan.md): takes on another drive are
// only counted; while the drive is out, its takes stay listed, greyed.
import { html } from "shared/html.js";
import { Icon } from "shared/icons.js";
import { requestSync } from "../api.js";
import { fmtBytes, fmtDur, fmtEta, takeStart } from "../format.js";
import { STEPS, camerasOf, notIngested, labelOf } from "../takes.js";

const RENDER_STEPS = [["rendering", "Render"], ["checking", "Check"], ["writing", "Write to drive"]];
const RENDER_RUNNING = ["queued", "rendering", "paused", "checking", "writing"];

/** A step chip: done, failed, running (spinner, %, bar), or still to come. */
function Chip({ state, name, percent, paused, extra, title }) {
  if (state === "done") return html`<span class="chip done">✓ ${name}</span>`;
  if (state === "error") return html`<span class="chip error" title=${title || ""}>✗ ${name}</span>`;
  if (state === "running") {
    return html`<span class="chip running">${paused ? "⏸" : html`<span class="spinner"></span>`} ${name}
      ${percent != null && html` ${percent}% <span class="meter"><i style=${{ width: percent + "%" }}></i></span>`}${extra || ""}${paused ? " · paused" : ""}</span>`;
  }
  return html`<span class="chip">○ ${name}</span>`;
}

function IngestChips({ camera, paused }) {
  const done = camera.done || [];
  const next = STEPS.find(([step]) => !done.includes(step));
  return STEPS.map(([step, name]) => {
    if (done.includes(step)) return html`<${Chip} state="done" name=${name} />`;
    if (camera.error && next && step === next[0]) return html`<${Chip} state="error" name=${name} />`;
    if (camera.step === step) {
      return html`<${Chip} state="running" name=${name} paused=${paused}
                          percent=${camera.progress != null ? Math.round(camera.progress * 100) : null} />`;
    }
    return html`<${Chip} name=${name} />`;
  });
}

/** A video render's progress (the editor's "Make video"): Render → Check → Write. */
function RenderChips({ render }) {
  const error = render.error || "";
  const failed = render.status !== "error" ? null
    : /^render check failed/.test(error) ? "checking" : /copy|drive full|did not verify/.test(error) ? "writing" : "rendering";
  const order = RENDER_STEPS.map(([key]) => key);
  const at = render.status === "done" ? order.length : order.indexOf(failed || (render.status === "paused" ? "rendering" : render.status));
  return html`${RENDER_STEPS.map(([key, name], i) => {
    if (i < at) return html`<${Chip} state="done" name=${name} />`;
    if (key === failed) return html`<${Chip} state="error" name=${name} title=${error} />`;
    if (i === at && render.status !== "queued") {
      const paused = render.status === "paused";
      return html`<${Chip} state="running" name=${name} paused=${paused}
        percent=${key === "rendering" && render.progress != null ? Math.round(render.progress * 100) : null}
        extra=${key === "rendering" && render.eta_s && !paused ? ` · ${fmtEta(render.eta_s)} left` : ""} />`;
    }
    return html`<${Chip} name=${name} />`;
  })}${render.status === "queued" && html`<span class="chip">queued${render.status_note ? ": " + render.status_note : ""}</span>`}`;
}

/** Dropped-frame stretches to avoid in an edit, from ingest. */
function RoughNote({ camera }) {
  const rough = camera && camera.rough;
  if (!rough || !rough.length) return null;
  const seconds = rough.reduce((sum, r) => sum + (r.end_s - r.start_s), 0);
  const tip = rough.map(r => `${fmtDur(r.start_s)}–${fmtDur(r.end_s)} (${r.missing} missing)`).join("\n");
  return html` <span class="warn" title=${tip}>· ${rough.length} rough section${rough.length > 1 ? "s" : ""}, ${seconds < 60 ? seconds.toFixed(1) + " s" : fmtDur(seconds)}</span>`;
}

const editorLink = (take, song, version, mode) =>
  `/editor/?take=${encodeURIComponent(take)}&song=${encodeURIComponent(song)}&v=${version}${mode ? "&mode=" + mode : ""}`;

/** Where a bounce version stands with its sync, and what can be done about it. */
function SyncState({ take, song, version, say, refresh }) {
  const sync = version.sync || {};
  const syncAgain = async () => {
    try { await requestSync(take, song, version.version); say("Syncing: it runs when no take or live session is on."); }
    catch (error) { say(error.message, true); }
    refresh();
  };
  const byHand = (label, tip) => html`<a class="key small" href=${editorLink(take, song, version.version, "sync")} target="_blank" rel="noopener" title=${tip}>${label}</a>`;
  if (sync.state === "syncing") return html`<span class="sync"><span class="spinner"></span> syncing to the video${sync.note ? ` (${sync.note})` : ""}</span>`;
  if (sync.state === "needs sync") {
    return html`<span class="sync bad" title=${sync.reason || ""}>needs sync: ${sync.reason || ""}</span>
      <button class="key small" onClick=${syncAgain}>sync again</button>
      ${byHand("sync by hand", "Line the bounce up with the video yourself, in the editor")}`;
  }
  if (sync.state === "synced") {
    const isByHand = sync.anchor === "by hand";
    const check = !isByHand && sync.confidence && sync.confidence !== "high";
    const tip = `${sync.anchor || "by hand"}${sync.confidence ? ", confidence " + sync.confidence : ""}${(sync.notes || []).length ? " · " + sync.notes.join("; ") : ""}`;
    return html`<span class=${"sync " + (check ? "warn" : "good")} title=${tip}>${isByHand ? "✓ synced by hand" : check ? "synced, check it" : "✓ synced"}</span>
      ${check && byHand("check sync", "Watch the bounce against the video and move it if it's off")}`;
  }
  if (!version.complete) return null;
  return html`<span class="sync">not synced</span> <button class="key small" onClick=${syncAgain}>sync</button>
    ${byHand("sync by hand", "Line the bounce up with the video yourself, in the editor")}`;
}

function SongGroup({ take, song, say, refresh, openDialog }) {
  const done = song.versions.filter(v => v.complete);
  const uploading = song.versions.filter(v => !v.complete);
  const synced = done.filter(v => v.synced).sort((a, b) => b.version - a.version);
  const render = song.render;
  const rendering = render && (RENDER_RUNNING.includes(render.status) || render.status === "error");
  return html`
    <div class="song">
      <div class="song-head">
        <span class="song-name"><${Icon} name="note" size=${16} />${song.name}</span>
        ${synced.length
          ? html`<a class="key signal" href=${editorLink(take, song.id, synced[0].version)} target="_blank" rel="noopener"
                    title="Open the editor: choose the cameras, set the look, make the video"><${Icon} name="pencil" size=${16} />edit video</a>`
          : html`<span class="key off" title="The bounce has to be synced to the take first"><${Icon} name="pencil" size=${16} />edit video</span>`}
      </div>
      <div class="bounces">
        ${done.map(v => html`<span class="bounce">bounce v${v.version} <${SyncState} take=${take} song=${song.id} version=${v} say=${say} refresh=${refresh} />
          ${!v.on_drive && html`<small>· waiting for ingest</small>`}</span>`)}
        ${uploading.length > 0 && html`<span class="bounce warn">v${uploading.map(v => v.version).join(", v")} uploading…</span>`}
      </div>
      ${rendering && html`<div class="camera-row"><span class="camera-name">video</span><div class="chips"><${RenderChips} render=${render} /></div></div>`}
      ${(song.videos || []).slice().reverse().map(video => html`
        <div class="video-version">
          <button class="key small" onClick=${() => openDialog({ kind: "player", take, file: video.file })}>▶ watch</button>
          video ${video.number} <small>· bounce v${video.bounce_version}${video.length_s ? " · " + fmtDur(video.length_s) : ""}</small>
        </div>`)}
    </div>`;
}

function Songs({ id, take, say, refresh, openDialog }) {
  const songs = (take.audio || {}).songs || [];
  if (!take.recorded && !songs.length) return null;
  return html`
    <div class="songs">
      <div class="songs-head"><span>songs</span>
        ${take.recorded && html`<button class="key small" onClick=${() => openDialog({ kind: "audio", take: id })}>♪ add a bounce</button>`}
      </div>
      ${songs.length
        ? songs.map(song => html`<${SongGroup} key=${song.id} take=${id} song=${song} say=${say} refresh=${refresh} openDialog=${openDialog} />`)
        : html`<p class="note">No bounce yet. Drop in the mix of a song to edit and render its video.</p>`}
    </div>`;
}

function HubMic({ take }) {
  const scratch = (take.audio || {}).scratch;
  if (!scratch) return null;
  return html`<p class="note"><${Icon} name="mic" size=${14} /> hub mic recorded ${fmtDur(scratch.seconds)}
    ${scratch.on_drive ? html` <span class="good">on drive</span>` : " · waiting for ingest"}
    ${scratch.problems.length > 0 && html` <span class="bad">⚠ ${scratch.problems[0]}</span>`}</p>`;
}

function Problems({ recorded }) {
  return recorded && recorded.problems && recorded.problems.length
    ? html`<p class="note bad">⚠ ${recorded.problems.join(" · ")}</p>` : null;
}

/** A take that's still only on the cameras: what was done in the session, at a glance. */
function WaitingTake({ id, take, queued, ...rest }) {
  const cameras = camerasOf(take);
  const recorded = take.recorded;
  const rows = recorded ? Object.entries(recorded.cameras)
    : cameras.map(([c, v]) => [c, { frames: v.frames_cam, dropped: v.dropped }]);
  const length = recorded ? recorded.length_s : Math.max(0, ...cameras.map(([, v]) => v.duration_s || 0));
  const dropped = rows.reduce((sum, [, v]) => sum + (v.dropped || 0), 0);
  return html`
    <article class="take waiting">
      <header><i class="led amber" aria-hidden="true"></i><h3>${id}</h3>
        <small>${[takeStart(id), length ? fmtDur(length) : "", rows.length + (rows.length === 1 ? " camera" : " cameras")].filter(Boolean).join(" · ")}</small>
        <span class="take-state warn">${queued ? "queued for ingest" : "on the cameras · not ingested yet"}</span></header>
      <p class="note">${rows.map(([c, v], i) => html`${i ? " · " : ""}${v.label || c}${v.uploading ? ": uploading…" : v.frames != null ? ": " + v.frames + " frames" : ""}${v.dropped ? html` <span class="bad">(${v.dropped} dropped)</span>` : ""}<${RoughNote} camera=${take[c]} />`)}
        ${!dropped && html` · <span class="good">no dropped frames</span>`}</p>
      <${Problems} recorded=${recorded} />
      <${HubMic} take=${take} />
      <${Songs} id=${id} take=${take} ...${rest} />
    </article>`;
}

function TakeCard({ id, take, paused, driveOk, ...rest }) {
  const cameras = camerasOf(take);
  const recorded = take.recorded;
  const allDone = cameras.length > 0 && cameras.every(([, v]) => (v.done || []).includes("marked"));
  const active = cameras.some(([, v]) => v.step);
  const failed = cameras.some(([, v]) => v.error);
  const length = Math.max(recorded ? recorded.length_s : 0, ...cameras.map(([, v]) => v.duration_s || 0));
  const etas = cameras.map(([, v]) => v.eta_s).filter(x => x != null);
  // Size: what's on the drive once written, otherwise what will be pulled from the cameras.
  const onDrive = cameras.reduce((sum, [, v]) => sum + (v.mov_bytes || 0), 0);
  const toPull = cameras.reduce((sum, [, v]) => sum + ((v.done || []).includes("written") ? 0 : v.src_bytes || 0), 0);
  const size = [onDrive ? fmtBytes(onDrive) + " on drive" : "", toPull ? fmtBytes(toPull) + " from cameras" : ""].filter(Boolean).join(" + ");
  const tone = take.drive_absent ? "absent" : allDone ? "done" : failed ? "error" : active ? "active" : "";
  const led = { done: "green", error: "red", active: "blue", absent: "off" }[tone] || "amber";
  const state = take.drive_absent ? html`<span class="take-state">drive not plugged in</span>`
    : allDone ? html`<span class="take-state good">✓ done</span>` : failed ? html`<span class="take-state bad">needs attention</span>`
    : active ? html`<span class="take-state">${etas.length ? fmtEta(Math.max(...etas)) + " left" : "working"}</span>` : html`<span class="take-state">queued</span>`;
  const playable = cameras.filter(([, v]) => (v.done || []).includes("written")).map(([c]) => c);
  return html`
    <article class=${"take " + tone}>
      <header><i class=${"led " + led} aria-hidden="true"></i><h3>${id}</h3>
        <small>${[takeStart(id), length ? fmtDur(length) + " of video" : "", size].filter(Boolean).join(" · ")}</small>${state}</header>
      ${allDone
        ? html`<p class="note">${cameras.map(([c, v], i) => html`${i ? " · " : ""}${labelOf(take, c)}: ${v.frames_out != null ? v.frames_out + " frames" : "done"}${v.repeated > 0 ? ` (${v.repeated} repeated)` : v.repeated < 0 ? ` (${-v.repeated} skipped to fit 30 fps)` : ""}${v.dropped ? `, ${v.dropped} dropped on camera` : ""}<${RoughNote} camera=${v} />`)}</p>`
        : cameras.map(([c, v]) => html`
          <div class="camera-row"><span class="camera-name" title=${labelOf(take, c)}>${labelOf(take, c)}</span>
            <div class="chips"><${IngestChips} camera=${v} paused=${paused} />${v.rough && v.rough.length ? html`<span class="chip"><${RoughNote} camera=${v} /></span>` : null}</div></div>
          ${v.error && html`<p class="note bad">${v.error}</p>`}`)}
      <${Problems} recorded=${recorded} />
      ${playable.length > 0 && html`<div class="plays">${playable.map(c => html`
        <button class="key small" disabled=${!driveOk || take.drive_absent} title=${driveOk ? "" : "STUDIO drive not in the hub"}
                onClick=${() => rest.openDialog({ kind: "player", take: id, camera: c, cameras: playable })}>▶ ${labelOf(take, c)}</button>`)}</div>`}
      <${HubMic} take=${take} />
      <${Songs} id=${id} take=${take} ...${rest} />
    </article>`;
}

export function Archive({ ingest, act, say, refreshIngest, openDialog }) {
  if (!ingest) return html`<p class="empty">Rewinding the tapes…</p>`;
  const status = ingest.status || "–";
  const paused = /^paused/.test(status);
  const running = status === "ingesting";
  const takes = Object.entries(ingest.takes || {});
  const doneCount = takes.filter(([, t]) => t.complete || (camerasOf(t).length && camerasOf(t).every(([, v]) => (v.done || []).includes("marked")))).length;
  const waitingCount = takes.filter(([, t]) => notIngested(t)).length;
  const ejected = ingest.drive === "ejected" && /^no .* drive/.test(status);
  const driveOk = !/^no .* drive|needs repair/.test(status) && ingest.drive !== "ejected";
  const driveView = ingest.drive_view || {};
  const shared = { say, refresh: refreshIngest, openDialog };
  return html`
    <section class="archive" aria-label="Archive">
      <div class="archive-bar">
        <div class="archive-status">
          ${ejected ? html`<b class="good">Drive ejected: safe to unplug</b>` : html`<b>${status}</b>`}
          ${takes.length > 0 && html` · ${doneCount} of ${takes.length} takes ingested`}
          ${waitingCount > 0 && html`, <b class="warn">${waitingCount} only on the cameras</b>`}
          ${(running || paused) && ingest.eta_s != null && ` · ${fmtEta(ingest.eta_s)} left`}
          ${ingest.drive_free_gb != null && ` · drive ${ingest.drive_free_gb} GB free`}
          ${driveView.present === false && !ejected && html` · <b>drive not plugged in</b>: its takes are greyed`}
          ${driveView.other_drive_takes ? ` · ${driveView.other_drive_takes} ${driveView.other_drive_takes === 1 ? "take" : "takes"} on another drive` : ""}
        </div>
        <div class="archive-keys">
          <button class="key" onClick=${() => act("/api/ingest/start", {}, "Queued ingest")}><${Icon} name="ingest" size=${18} />ingest now</button>
          <button class="key" onClick=${() => act("/api/drive/eject", {}, "Ejecting drive")}><${Icon} name="eject" size=${18} />eject drive</button>
        </div>
      </div>
      ${takes.length === 0 && html`<p class="empty">Nothing here yet. Go make some noise.</p>`}
      ${takes.slice().reverse().map(([id, take]) => notIngested(take)
        ? html`<${WaitingTake} key=${id} id=${id} take=${take} queued=${camerasOf(take).length > 0 && (running || paused)} ...${shared} />`
        : html`<${TakeCard} key=${id} id=${id} take=${take} paused=${paused} driveOk=${driveOk} ...${shared} />`)}
    </section>`;
}
