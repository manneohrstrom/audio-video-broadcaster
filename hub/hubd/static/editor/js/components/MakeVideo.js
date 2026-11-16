// "Make video" in the editor's header: saves the edit, asks the hub to render the finished
// video (hub-ingest's edit_render.py, from the full-quality originals, YouTube settings),
// and shows its progress until it can be watched.
import { useEffect, useState } from "preact/hooks";
import { html } from "./html.js";
import { requestRender, renderStatus } from "../api.js";

const POLL_MS = 3000;
const RUNNING = ["queued", "rendering", "paused", "checking", "writing"];

function minutes(seconds) {
  return seconds >= 90 ? `${Math.round(seconds / 60)} min` : `${Math.max(1, Math.round(seconds))} s`;
}

export function MakeVideo({ store }) {
  const { take_id: takeId, song, version } = store.project;
  const [job, setJob] = useState({});
  const [problem, setProblem] = useState(null);

  useEffect(() => {
    let stopped = false;
    const poll = () => renderStatus(takeId, song.id, version).then(status => { if (!stopped) setJob(status || {}); }).catch(() => {});
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => { stopped = true; clearInterval(timer); };
  }, [takeId, song.id, version]);

  async function make() {
    setProblem(null);
    try {
      await store.saveNow();                       // render exactly what's on screen
      await requestRender(takeId, song.id, version);
      setJob({ status: "queued" });
    } catch (error) {
      setProblem(error.message);
    }
  }

  const running = RUNNING.includes(job.status);
  let label = null;
  if (job.status === "queued") label = `waiting to start${job.status_note ? `: ${job.status_note}` : ""}`;
  else if (job.status === "rendering") label = `rendering ${Math.round((job.progress || 0) * 100)}%${job.eta_s ? ` · ${minutes(job.eta_s)} left` : ""}`;
  else if (job.status === "paused") label = "paused while recording";
  else if (job.status === "checking" || job.status === "writing") label = "checking and saving to the drive…";
  else if (job.status === "error") label = `failed: ${job.error || ""}`;

  return html`
    <div class="make-video">
      ${label && html`<span class=${"render-state" + (job.status === "error" ? " problem" : "")} title=${label}>${label}</span>`}
      ${problem && html`<span class="render-state problem" title=${problem}>${problem}</span>`}
      ${job.status === "done" && job.file && html`
        <a class="button" href=${`/media/${takeId}/${job.file}`} target="_blank" rel="noopener">▶ Watch video</a>`}
      <button class="button primary" disabled=${running} onClick=${make}
              title="Render the finished video from the full-quality cameras, ready for YouTube">
        ${job.status === "done" ? "Make again" : "Make video"}
      </button>
    </div>`;
}
