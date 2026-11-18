// "Make video" in the editor's header: saves the edit, asks the hub to render the next
// video version of this song (video 1, 2, 3, … each with the bounce version chosen in the
// header; hub-ingest's edit_render.py, full-quality originals, YouTube settings), and
// shows its progress until it can be watched. With "YouTube" on (when the hub is signed in),
// the finished video then goes to the channel as a private video (hub-upload), and its
// progress and a link to it in YouTube Studio show here too.
import { useEffect, useState } from "preact/hooks";
import { html } from "./html.js";
import { requestRender, renderStatus, youtubeStatus } from "../api.js";
import { Icon } from "shared/icons.js";

const POLL_MS = 3000;
const RUNNING = ["queued", "rendering", "paused", "checking", "writing"];
const YOUTUBE_CHOICE_KEY = "editor.uploadToYouTube";      // this browser's choice; on unless turned off

function rememberedYouTubeChoice() {
  try { return localStorage.getItem(YOUTUBE_CHOICE_KEY) !== "off"; } catch { return true; }
}

function rememberYouTubeChoice(on) {
  try { localStorage.setItem(YOUTUBE_CHOICE_KEY, on ? "on" : "off"); } catch { /* private window: fine */ }
}

/** What the upload is doing, in a few words, or null. */
function uploadLabel(upload) {
  if (!upload) return null;
  switch (upload.status) {
    case "queued": return "YouTube: waiting to upload";
    case "uploading": return `YouTube: uploading ${Math.round((upload.progress || 0) * 100)}%`;
    case "paused": return "YouTube: paused while recording";
    case "retrying": case "waiting": return `YouTube: ${upload.note || "trying again soon"}`;
    case "error": return `YouTube: failed: ${upload.error || ""}`;
    default: return null;
  }
}

function minutes(seconds) {
  return seconds >= 90 ? `${Math.round(seconds / 60)} min` : `${Math.max(1, Math.round(seconds))} s`;
}

export function MakeVideo({ store }) {
  const { take_id: takeId, song, version } = store.project;
  const [job, setJob] = useState({});
  const [problem, setProblem] = useState(null);
  const [signedIn, setSignedIn] = useState(false);
  const [toYouTube, setToYouTube] = useState(rememberedYouTubeChoice());

  useEffect(() => { youtubeStatus().then(s => setSignedIn(!!(s && s.signed_in))).catch(() => {}); }, []);

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
      await requestRender(takeId, song.id, version, signedIn && toYouTube);
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
  else if (job.status === "done") label = uploadLabel(job.upload) || (job.upload_error ? `YouTube: ${job.upload_error}` : null);
  const uploadProblem = job.status === "done" && ((job.upload && job.upload.status === "error") || job.upload_error);

  return html`
    <div class="make-video">
      ${label && html`<span class=${"render-state" + (job.status === "error" || uploadProblem ? " problem" : "")} title=${label}>${label}</span>`}
      ${problem && html`<span class="render-state problem" title=${problem}>${problem}</span>`}
      ${job.status === "done" && job.file && html`
        <a class="button" href=${`/media/${takeId}/${job.file}`} target="_blank" rel="noopener"
           title=${`Bounce v${job.version}`}><${Icon} name="watch" size=${16} />Watch video ${job.video_number || ""}</a>`}
      ${job.status === "done" && job.upload && job.upload.status === "done" && html`
        <a class="button" href=${job.upload.studio_url || job.upload.url} target="_blank" rel="noopener"
           title="Private on the channel: open it in YouTube Studio to check it and publish">On YouTube</a>`}
      ${signedIn && html`
        <label class="youtube-choice" title="Upload the finished video to the channel as a private video">
          <input type="checkbox" checked=${toYouTube} disabled=${running}
                 onChange=${event => { setToYouTube(event.target.checked); rememberYouTubeChoice(event.target.checked); }} />
          YouTube
        </label>`}
      <button class="button signal" disabled=${running} onClick=${make}
              title=${`Render a new video version with bounce v${version}, from the full-quality cameras, ready for YouTube`}>
        <${Icon} name="reel" size=${16} />Make video
      </button>
    </div>`;
}
