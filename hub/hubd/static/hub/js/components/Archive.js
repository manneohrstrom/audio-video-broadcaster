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
import { STEPS, PREP_STEPS, camerasOf, notIngested, labelOf, prepOf, prepUnfinished } from "../takes.js";
import { EditableName, takeName } from "../names.js";

/** The take's heading: its name (click the pencil to change it), the id in brackets once named. */
function TakeTitle({ id, names, say }) {
  return html`<h3><${EditableName} kind="takes" id=${id} given=${takeName(names, id)} fallback=${id} say=${say} /></h3>`;
}

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

const PREP_LATER = { proxy: "after a song is synced", motion: "after the preview copy" };

/** The steps after Mark on camera: thumbnails, preview copy and movement for the editor. */
function PrepChips({ prep, adopted, paused }) {
  return PREP_STEPS.filter(([step]) => prep[step]).map(([step, name]) => {
    const { state, error, progress, phase } = prep[step];
    if (state === "done") return html`<${Chip} state="done" name=${name} />`;
    if (state === "failed") return html`<${Chip} state="error" name=${name} title=${error || ""} />`;
    if (state === "running") {
      return html`<${Chip} state="running" name=${name} paused=${paused}
                          percent=${progress != null ? Math.round(progress * 100) : null}
                          extra=${phase === "checking" ? " · checking it" : ""} />`;
    }
    if (state === "later") return html`<span class="chip">○ ${name} · ${adopted ? "when it's edited" : PREP_LATER[step]}</span>`;
    return html`<${Chip} name=${name} />`;
  });
}

/** Is the render (and its YouTube upload, if any) still worth showing as steps? */
function renderInFlight(render) {
  if (!render) return false;
  if (RENDER_RUNNING.includes(render.status) || render.status === "error") return true;
  if (render.status !== "done" || !render.youtube) return false;
  return !!render.upload_error || !render.upload || render.upload.status !== "done";
}

/** The fourth step, after Write to drive: hub-upload sending the video to the channel (private). */
function YouTubeChip({ render }) {
  const upload = render.upload;
  const name = "YouTube";
  if (render.status !== "done") return html`<${Chip} name=${name} />`;
  if (render.upload_error) return html`<${Chip} state="error" name=${name} title=${render.upload_error} />`;
  if (!upload) return html`<${Chip} name=${name} />`;
  switch (upload.status) {
    case "done": return html`<${Chip} state="done" name=${name} />`;
    case "error": return html`<${Chip} state="error" name=${name} title=${upload.error || ""} />`;
    case "uploading": return html`<${Chip} state="running" name=${name} percent=${Math.round((upload.progress || 0) * 100)} />`;
    case "paused": return html`<${Chip} state="running" name=${name} paused=${true} />`;
    case "retrying": case "waiting":
      return html`<${Chip} state="running" name=${name} extra=${` · ${upload.note || "trying again soon"}`} />`;
    default: return html`<span class="chip">○ ${name} · waiting to upload</span>`;
  }
}

/** A video render's progress (the editor's "Make video"): Render → Check → Write (→ YouTube). */
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
  })}${render.youtube && render.status !== "error" && html`<${YouTubeChip} render=${render} />`}${render.status === "queued" && html`<span class="chip">queued${render.status_note ? ": " + render.status_note : ""}</span>`}`;
}

/** Dropped-frame stretches to avoid in an edit, from ingest. */
function RoughNote({ camera }) {
  const rough = camera && camera.rough;
  if (!rough || !rough.length) return null;
  const seconds = rough.reduce((sum, r) => sum + (r.end_s - r.start_s), 0);
  const tip = rough.map(r => `${fmtDur(r.start_s)}–${fmtDur(r.end_s)} (${r.missing} missing)`).join("\n");
  return html` <span class="warn" title=${tip}>· ${rough.length} rough section${rough.length > 1 ? "s" : ""}, ${seconds < 60 ? seconds.toFixed(1) + " s" : fmtDur(seconds)}</span>`;
}

/** A download key: the file itself (same origin, so the browser keeps the given name). */
function Download({ href, name, what, disabled, tip }) {
  if (disabled) return html`<span class="key small off download" title=${tip}><${Icon} name="download" size=${14} /></span>`;
  return html`<a class="key small download" href=${href} download=${name} title=${"Download " + what}
                 aria-label=${"Download " + what}><${Icon} name="download" size=${14} /></a>`;
}

/** "Sunday jam - Blue in Green - video 3.mp4"; the take's date when it has no name. */
const videoFileName = (takeLabel, song, video) => `${takeLabel} - ${song.name} - video ${video.number}.mp4`;

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

/** Where a staged file stands with the drive: on it, being copied now, or waiting. */
const copyingTo = (activity, take) => activity && activity.kind === "audio" && activity.take === take;

function SongGroup({ take, song, say, refresh, openDialog, activity, names, driveOk }) {
  const findingBeats = v => activity && activity.kind === "beats" && activity.take === take
    && activity.song === song.id && activity.version === "v" + v.version;
  const done = song.versions.filter(v => v.complete);
  const uploading = song.versions.filter(v => !v.complete);
  const synced = done.filter(v => v.synced).sort((a, b) => b.version - a.version);
  const render = song.render;
  const rendering = renderInFlight(render);
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
        ${done.map(v => html`<span class="bounce">bounce v${v.version}
          <${Download} href=${`/media-audio/${take}/${song.id}/v${v.version}`} name=${v.original_name || v.filename} what=${`bounce v${v.version} (${v.original_name || v.filename})`} />
          <${SyncState} take=${take} song=${song.id} version=${v} say=${say} refresh=${refresh} />
          ${findingBeats(v) && html`<span class="sync"><span class="spinner"></span> finding the beats</span>`}
          ${!v.on_drive && html`<small>· ${copyingTo(activity, take) ? "copying to the drive" : "waiting for ingest"}</small>`}</span>`)}
        ${uploading.length > 0 && html`<span class="bounce warn">v${uploading.map(v => v.version).join(", v")} uploading…</span>`}
      </div>
      ${rendering && html`<div class="camera-row"><span class="camera-name">video</span><div class="chips"><${RenderChips} render=${render} /></div></div>`}
      ${(song.videos || []).slice().reverse().map(video => html`
        <div class="video-version">
          <button class="key small" onClick=${() => openDialog({ kind: "player", take, file: video.file })}>▶ watch</button>
          video ${video.number} <small>· bounce v${video.bounce_version}${video.length_s ? " · " + fmtDur(video.length_s) : ""}${video.bytes ? " · " + fmtBytes(video.bytes) : ""}</small>
          <${Download} href=${`/media/${take}/${video.file}`} name=${videoFileName(takeName(names, take) || take.slice(0, 4) + "-" + take.slice(4, 6) + "-" + take.slice(6, 8), song, video)}
                       what=${`video ${video.number}`} disabled=${!driveOk} tip="The STUDIO drive isn't in the hub" />
          ${video.youtube && video.youtube.status === "done" && html`
            <a class="key small" href=${video.youtube.studio_url || video.youtube.url} target="_blank" rel="noopener"
               title="Private on the channel: open it in YouTube Studio to check it and publish">On YouTube</a>`}
        </div>`)}
    </div>`;
}

function Songs({ id, take, say, refresh, openDialog, activity, names, driveOk }) {
  const songs = (take.audio || {}).songs || [];
  if (!take.recorded && !songs.length) return null;
  return html`
    <div class="songs">
      <div class="songs-head"><span>songs</span>
        ${take.recorded && html`<button class="key small" onClick=${() => openDialog({ kind: "audio", take: id })}>♪ add a bounce</button>`}
      </div>
      ${songs.length
        ? songs.map(song => html`<${SongGroup} key=${song.id} take=${id} song=${song} say=${say} refresh=${refresh} openDialog=${openDialog}
                                           activity=${activity} names=${names} driveOk=${driveOk && !take.drive_absent} />`)
        : html`<p class="note">No bounce yet. Drop in the mix of a song to edit and render its video.</p>`}
    </div>`;
}

function HubMic({ id, take, activity }) {
  const scratch = (take.audio || {}).scratch;
  if (!scratch) return null;
  return html`<p class="note"><${Icon} name="mic" size=${14} /> hub mic recorded ${fmtDur(scratch.seconds)}
    ${scratch.on_drive ? html` <span class="good">on drive</span>` : copyingTo(activity, id) ? " · copying to the drive" : " · waiting for ingest"}
    ${scratch.problems.length > 0 && html` <span class="bad">⚠ ${scratch.problems[0]}</span>`}</p>`;
}

function Problems({ recorded }) {
  return recorded && recorded.problems && recorded.problems.length
    ? html`<p class="note bad">⚠ ${recorded.problems.join(" · ")}</p>` : null;
}

/** A take that's still only on the cameras: what was done in the session, at a glance. */
function WaitingTake({ id, take, queued, names, ...rest }) {
  const cameras = camerasOf(take);
  const recorded = take.recorded;
  const rows = recorded ? Object.entries(recorded.cameras)
    : cameras.map(([c, v]) => [c, { frames: v.frames_cam, dropped: v.dropped }]);
  const length = recorded ? recorded.length_s : Math.max(0, ...cameras.map(([, v]) => v.duration_s || 0));
  const dropped = rows.reduce((sum, [, v]) => sum + (v.dropped || 0), 0);
  return html`
    <article class="take waiting">
      <header><i class="led amber" aria-hidden="true"></i><${TakeTitle} id=${id} names=${names} say=${rest.say} />
        <small>${[takeStart(id), length ? fmtDur(length) : "", rows.length + (rows.length === 1 ? " camera" : " cameras")].filter(Boolean).join(" · ")}</small>
        <span class="take-state warn">${queued ? "queued for ingest" : "on the cameras · not ingested yet"}</span></header>
      <p class="note">${rows.map(([c, v], i) => html`${i ? " · " : ""}${labelOf(take, c, names)}${v.uploading ? ": uploading…" : v.frames != null ? ": " + v.frames + " frames" : ""}${v.dropped ? html` <span class="bad">(${v.dropped} dropped)</span>` : ""}<${RoughNote} camera=${take[c]} />`)}
        ${!dropped && html` · <span class="good">no dropped frames</span>`}</p>
      <${Problems} recorded=${recorded} />
      <${HubMic} id=${id} take=${take} activity=${rest.activity} />
      <${Songs} id=${id} take=${take} names=${names} ...${rest} />
    </article>`;
}

/** The foot of an ingested take: "delete take", or how its delete is going. */
function TakeFoot({ id, take, openDialog }) {
  if (take.deleting) return html`<div class="take-foot">being deleted from the drive and the hub (waits while recording)</div>`;
  return html`<div class="take-foot">
    ${take.delete_error && html`<span class="bad">couldn't delete it: ${take.delete_error}</span>`}
    <button class="key small quiet" disabled=${take.drive_absent} title=${take.drive_absent ? "Its drive isn't plugged in" : "Delete the take from the drive and the hub"}
            onClick=${() => openDialog({ kind: "delete-take", take: id })}><${Icon} name="trash" size=${14} />delete take</button></div>`;
}

function TakeCard({ id, take, paused, driveOk, names, ...rest }) {
  const activity = rest.activity;
  const label = camera => labelOf(take, camera, names);
  const cameras = camerasOf(take);
  const recorded = take.recorded;
  const preps = Object.fromEntries(cameras.map(([c, v]) => [c, prepOf(v, id, c, activity)]));
  const prepSteps = Object.values(preps).flatMap(prep => Object.values(prep));
  const prepRunning = prepSteps.some(step => step.state === "running");
  const ingested = cameras.length > 0 && cameras.every(([, v]) => (v.done || []).includes("marked"));
  const allDone = ingested && !Object.values(preps).some(prepUnfinished);
  const active = cameras.some(([, v]) => v.step) || prepRunning;
  const failed = cameras.some(([, v]) => v.error) || prepSteps.some(step => step.state === "failed");
  const length = Math.max(recorded ? recorded.length_s : 0, ...cameras.map(([, v]) => v.duration_s || 0));
  const etas = cameras.map(([, v]) => v.eta_s).concat(prepRunning ? [activity.eta_s] : []).filter(x => x != null);
  // Size: what's on the drive once written, otherwise what will be pulled from the cameras.
  const onDrive = cameras.reduce((sum, [, v]) => sum + (v.mov_bytes || 0), 0);
  const toPull = cameras.reduce((sum, [, v]) => sum + ((v.done || []).includes("written") ? 0 : v.src_bytes || 0), 0);
  const size = [onDrive ? fmtBytes(onDrive) + " on drive" : "", toPull ? fmtBytes(toPull) + " from cameras" : ""].filter(Boolean).join(" + ");
  const tone = take.drive_absent || take.deleting ? "absent" : allDone ? "done" : failed ? "error" : active ? "active" : "";
  const led = { done: "green", error: "red", active: "blue", absent: "off" }[tone] || "amber";
  const state = take.deleting ? html`<span class="take-state"><span class="spinner"></span> deleting</span>`
    : take.drive_absent ? html`<span class="take-state">drive not plugged in</span>`
    : allDone ? html`<span class="take-state good">✓ done</span>` : failed ? html`<span class="take-state bad">needs attention</span>`
    : active ? html`<span class="take-state">${etas.length ? fmtEta(Math.max(...etas)) + " left" : "working"}</span>`
    : ingested ? html`<span class="take-state">getting it ready for the editor</span>` : html`<span class="take-state">queued</span>`;
  const playable = cameras.filter(([, v]) => (v.done || []).includes("written")).map(([c]) => c);
  return html`
    <article class=${"take " + tone}>
      <header><i class=${"led " + led} aria-hidden="true"></i><${TakeTitle} id=${id} names=${names} say=${rest.say} />
        <small>${[takeStart(id), length ? fmtDur(length) + " of video" : "", size].filter(Boolean).join(" · ")}</small>${state}</header>
      ${allDone
        ? html`<p class="note">${cameras.map(([c, v], i) => html`${i ? " · " : ""}${label(c)}: ${v.frames_out != null ? v.frames_out + " frames" : "done"}${v.repeated > 0 ? ` (${v.repeated} repeated)` : v.repeated < 0 ? ` (${-v.repeated} skipped to fit 30 fps)` : ""}${v.dropped ? `, ${v.dropped} dropped on camera` : ""}<${RoughNote} camera=${v} />`)}</p>`
        : cameras.map(([c, v]) => html`
          <div class="camera-row"><span class="camera-name" title=${label(c) !== c ? `${label(c)} (${c})` : c}>${label(c)}</span>
            <div class="chips">${(v.done || []).includes("marked") ? html`<${Chip} state="done" name="Ingested" />` : html`<${IngestChips} camera=${v} paused=${paused} />`}<${PrepChips} prep=${preps[c]} adopted=${take.adopted} paused=${paused} />${v.rough && v.rough.length ? html`<span class="chip"><${RoughNote} camera=${v} /></span>` : null}</div></div>
          ${v.error && html`<p class="note bad">${v.error}</p>`}
          ${PREP_STEPS.filter(([step]) => (preps[c][step] || {}).state === "failed").map(([step, name]) =>
            html`<p class="note bad">${name}: ${preps[c][step].error || "failed"} (tried again after the next update)</p>`)}`)}
      <${Problems} recorded=${recorded} />
      ${playable.length > 0 && html`<div class="plays">${playable.map(c => html`
        <button class="key small" disabled=${!driveOk || take.drive_absent} title=${driveOk ? "" : "STUDIO drive not in the hub"}
                onClick=${() => rest.openDialog({ kind: "player", take: id, camera: c, cameras: playable,
                                                  labels: Object.fromEntries(playable.map(p => [p, label(p)])) })}>▶ ${label(c)}</button>`)}</div>`}
      <${HubMic} id=${id} take=${take} activity=${rest.activity} />
      <${Songs} id=${id} take=${take} names=${names} driveOk=${driveOk} ...${rest} />
      ${take.complete && html`<${TakeFoot} id=${id} take=${take} openDialog=${rest.openDialog} />`}
    </article>`;
}

export function Archive({ ingest, status: rig, act, say, refreshIngest, openDialog }) {
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
  const shared = { say, refresh: refreshIngest, openDialog, names: rig && rig.names, activity: ingest.activity };
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
        ? html`<${WaitingTake} key=${id} id=${id} take=${take} queued=${camerasOf(take).length > 0 && (running || paused)} driveOk=${driveOk} ...${shared} />`
        : html`<${TakeCard} key=${id} id=${id} take=${take} paused=${paused} driveOk=${driveOk} ...${shared} />`)}
    </section>`;
}
