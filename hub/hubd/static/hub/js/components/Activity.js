// What the hub is doing in the background, on every view (user, 2026-11-21: "a UI indication
// for every single task that is going on in the background"). One line per job: ingesting a
// take from the cameras, preview copies, thumbnails, movement readings, beats, songs to the
// drive (hub-ingest's "activity"), renders, syncs, YouTube uploads, phones still sending a
// take. Each with how far it is and the time left; "paused" while the rig records. Nothing
// shown when nothing runs. A line opens the archive.
import { html } from "shared/html.js";
import { fmtEta, takeStart } from "../format.js";
import { cameraName, takeName } from "../names.js";
import { STEPS, labelOf } from "../takes.js";

const STEP_WORDS = Object.fromEntries(STEPS.map(([id, word]) => [id, word.toLowerCase()]));
const RENDER_WORDS = { rendering: "rendering", checking: "checking", writing: "writing to the drive", paused: "rendering" };
const UPLOAD_WORDS = { queued: "waiting to upload", uploading: "uploading", paused: "uploading", retrying: "trying again soon",
                       waiting: "waiting (YouTube's daily limit or no network)" };

/** "Sunday jam" or "20 Nov 13:37" */
function takeLabel(id, names) {
  return takeName(names, id) || takeStart(id) || id;
}

function songLabel(ingest, take, song) {
  const songs = (((ingest.takes || {})[take] || {}).audio || {}).songs || [];
  const found = songs.find(s => s.id === song);
  return (found && found.name) || song;
}

/** Every running job as { key, text, detail, progress (0-1 or null), eta, paused }. */
export function backgroundJobs(status, ingest) {
  const jobs = [];
  if (!ingest) return jobs;
  const names = status && status.names;
  const rigBusy = /^paused/.test(ingest.status || "");
  const take = id => takeLabel(id, names);

  // Camera files of the take being ingested: pulled from the cameras, converted, written, …
  if (ingest.current && ingest.takes && ingest.takes[ingest.current]) {
    const record = ingest.takes[ingest.current];
    const working = Object.entries(record).filter(([, v]) => v && typeof v === "object" && v.step);
    if (working.length) {
      const progress = working.map(([, v]) => v.progress).filter(p => p != null);
      jobs.push({
        key: "ingest", text: `Ingesting ${take(ingest.current)}`,
        detail: working.map(([c, v]) => `${labelOf(record, c, names)} ${STEP_WORDS[v.step] || v.step}${v.progress != null ? " " + Math.round(v.progress * 100) + " %" : ""}`).join(" · "),
        progress: progress.length ? Math.min(...progress) : null, eta: ingest.eta_s, paused: rigBusy,
      });
    }
  }

  // hub-ingest's other work (ingest.py "activity")
  const act = ingest.activity;
  if (act && act.kind) {
    const phone = ((status && status.phones) || {})[act.camera];
    const camera = act.camera ? cameraName(names, act.camera, phone && phone.label) : "";
    const of = act.count > 1 ? ` · ${act.index} of ${act.count}` : "";
    const text = {
      proxy: "Making the editor's preview copies",
      thumbnails: "Making thumbnails for the archive strips",
      motion: "Measuring movement for the automatic cut",
      beats: "Finding the beats",
      audio: "Copying songs to the drive",
    }[act.kind] || act.kind;
    const what = act.kind === "beats" ? `${songLabel(ingest, act.take, act.song)} ${act.version} · ${take(act.take)}`
      : act.kind === "audio" ? `${take(act.take)} · ${act.file}`
      : `${take(act.take)} · ${camera}`;
    jobs.push({ key: "activity", text, detail: what + of + (act.phase === "checking" ? " · checking it" : ""),
                progress: act.progress, eta: act.eta_s, paused: rigBusy });
  }

  // Renders: the editor's videos and quick mixes
  for (const [key, job] of Object.entries(ingest.renders || {})) {
    if (!RENDER_WORDS[job.status]) continue;
    jobs.push({ key: "render " + key, text: `Making the video of ${job.song_name || songLabel(ingest, job.take, job.song)}`,
                detail: `${take(job.take)} · ${RENDER_WORDS[job.status]}`,
                progress: job.status === "rendering" || job.status === "paused" ? job.progress : null,
                eta: job.eta_s, paused: job.status === "paused" });
  }

  // Bounce syncs (audio to the take)
  for (const [key, job] of Object.entries(ingest.syncs || {})) {
    if (job.status !== "syncing") continue;
    jobs.push({ key: "sync " + key, text: `Syncing ${songLabel(ingest, job.take, job.song)} v${job.version} to the take`,
                detail: take(job.take), progress: null, eta: null, paused: false });
  }

  // YouTube (hub-upload)
  for (const upload of ingest.uploads || []) {
    if (upload.status === "queued") continue;                 // counted below
    jobs.push({ key: "upload " + upload.key, text: `Uploading ${upload.title || upload.song} to YouTube`,
                detail: `${upload.take ? take(upload.take) + " · " : ""}${UPLOAD_WORDS[upload.status] || upload.status}${upload.note ? " · " + upload.note : ""}`,
                progress: upload.progress, eta: null, paused: upload.status === "paused" });
  }

  // Phones still sending a take to the hub (during a take that's part of recording)
  if (status && status.state !== "recording") {
    for (const [id, phone] of Object.entries(status.phones || {})) {
      const st = phone.status || {};
      if (!phone.online || !(st.queue > 0 || st.state === "uploading" || st.state === "finishing")) continue;
      jobs.push({ key: "phone " + id, text: `${cameraName(names, id, phone.label)} sending its take to the hub`,
                  detail: st.queue ? `${st.queue} pieces left` : st.state, progress: null, eta: null, paused: false });
    }
  }
  return jobs;
}

/** Jobs queued behind the running ones, in words: "2 videos to make, 1 YouTube upload waiting". */
function waitingWords(ingest) {
  if (!ingest) return "";
  const renders = Object.values(ingest.renders || {}).filter(j => j.status === "queued").length;
  const syncs = Object.values(ingest.syncs || {}).filter(j => j.status === "queued").length;
  const uploads = (ingest.uploads || []).filter(u => u.status === "queued").length;
  return [renders && `${renders} ${renders === 1 ? "video" : "videos"} to make`,
          syncs && `${syncs} ${syncs === 1 ? "song" : "songs"} to sync`,
          uploads && `${uploads} YouTube ${uploads === 1 ? "upload" : "uploads"}`].filter(Boolean).join(", ");
}

export function Activity({ status, ingest, go }) {
  const jobs = backgroundJobs(status, ingest);
  const waiting = waitingWords(ingest);
  if (!jobs.length && !waiting) return null;
  return html`
    <section class="activity" aria-label="Working in the background" aria-live="polite">
      ${jobs.map(job => html`
        <button key=${job.key} class=${"activity-row" + (job.paused ? " paused" : "")} onClick=${() => go("archive")}
                title=${job.paused ? "Paused while the rig records; it carries on afterwards" : "Open the archive"}>
          <i class=${"led " + (job.paused ? "amber" : "blue") + " working"} aria-hidden="true"></i>
          <span class="activity-text"><b>${job.text}</b><span class="activity-detail">${job.detail}</span></span>
          ${job.progress != null && html`<span class="activity-bar" aria-hidden="true"><i style=${{ width: Math.round(job.progress * 100) + "%" }}></i></span>`}
          <span class="activity-time">${job.paused ? "paused while recording"
            : [job.progress != null ? Math.round(job.progress * 100) + " %" : "", job.eta != null ? fmtEta(job.eta) + " left" : ""].filter(Boolean).join(" · ")}</span>
        </button>`)}
      ${waiting && html`<p class="activity-waiting">Waiting: ${waiting}</p>`}
    </section>`;
}
