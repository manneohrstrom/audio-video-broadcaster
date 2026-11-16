// The video window: the camera that is on at the playhead (with the dissolve between
// shots), or the camera under the mouse while skimming. Pictures come from the WebCodecs
// player (media/video-player.js); until a camera's frame is decoded, its strip thumbnail
// stands in, enlarged.
import { useEffect, useRef } from "preact/hooks";
import { html } from "./html.js";
import { everyFrame } from "../frame-loop.js";
import { cutIndexAt } from "../edit-model.js";
import { formatSeconds } from "../time-format.js";
import { fullVideoAvailable } from "../media/video-player.js";

const NO_FULL_VIDEO_NOTE = "thumbnails only: full video needs a secure page (see the hub runbook, Editor)";

export function VideoWindow({ store, thumbnailsByCamera, player }) {
  const canvasRef = useRef(null);
  const badgeRef = useRef(null);
  const wrapperRef = useRef(null);

  const noteRef = useRef(null);

  useEffect(() => everyFrame(now => {
    const canvas = canvasRef.current;
    const pictures = { player, thumbnailsByCamera, now, waiting: false };
    player.prerollNextShot(now);
    // player.keepAllRunning(...) makes mid-play switches instant, but needs ~60 Mbit/s from
    // the hub for four full-quality cameras; it waits for editing proxies.
    const context = canvas.getContext("2d");
    const { camera: skimCamera, seconds } = whatToShow(store);
    context.fillStyle = "#000";
    context.fillRect(0, 0, canvas.width, canvas.height);
    if (skimCamera) {
      drawCamera(context, store, pictures, skimCamera, seconds, canvas);
    } else {
      drawProgram(context, store, pictures, seconds, canvas);
    }
    const note = !fullVideoAvailable ? NO_FULL_VIDEO_NOTE : pictures.waiting ? "loading video…" : "";
    if (noteRef.current.textContent !== note) noteRef.current.textContent = note;
    noteRef.current.hidden = !note;
    const shown = skimCamera || store.cameraAt(seconds);
    wrapperRef.current.style.setProperty("--frame-colour", skimCamera ? `var(--color-camera-${(shown.index % 6) + 1})` : "transparent");
    const text = `${shown.number} ${shown.label}` + (skimCamera ? ` · ${formatSeconds(seconds)}` : "");
    if (badgeRef.current.textContent !== text) badgeRef.current.textContent = text;
  }), []);

  return html`
    <div class="video-stage">
      <div class="video-window" ref=${wrapperRef}>
        <canvas ref=${canvasRef} width="1920" height="1080" aria-label="Video preview"></canvas>
        <div class="video-badge" ref=${badgeRef}></div>
        <div class="video-note" ref=${noteRef} hidden></div>
      </div>
    </div>`;
}

/** Skim the camera under the mouse when not playing; otherwise the program at the playhead. */
function whatToShow(store) {
  const gesture = store.gesture;
  if (gesture?.kind === "select" && gesture.moved) return { camera: gesture.camera, seconds: gesture.currentSeconds };
  if (!store.isPlaying && !gesture && store.hover?.skimCamera) return { camera: store.hover.skimCamera, seconds: store.hover.seconds };
  if (!store.isPlaying && !gesture && store.hover?.lane && store.hover.lane.type !== "ruler") return { camera: null, seconds: store.hover.seconds };
  return { camera: null, seconds: store.playheadSeconds };
}

/** One camera's picture: the decoded frame, else the thumbnail (and note that we're waiting). */
function drawCamera(context, store, pictures, camera, seconds, canvas) {
  if (!store.isRecording(camera, seconds)) return;
  const frame = pictures.player.pictureFor(camera, seconds, pictures.now);
  if (frame) {
    context.drawImage(frame, 0, 0, canvas.width, canvas.height);
    return;
  }
  pictures.waiting = true;
  pictures.thumbnailsByCamera.get(camera.id)?.draw(context, store.cameraFileSeconds(camera, seconds), 0, 0, canvas.width, canvas.height);
}

/** The program picture, with the dissolve centred on each cut. */
function drawProgram(context, store, pictures, seconds, canvas) {
  const cuts = store.cuts;
  const index = cutIndexAt(cuts, seconds);
  const dissolve = store.dissolveSeconds;
  let from = null;
  let to = null;
  let mix = 0;
  if (dissolve > 0 && index > 0 && seconds - cuts[index].atSeconds < dissolve / 2) {
    from = cuts[index - 1]; to = cuts[index]; mix = 0.5 + (seconds - cuts[index].atSeconds) / dissolve;
  } else if (dissolve > 0 && cuts[index + 1] && cuts[index + 1].atSeconds - seconds < dissolve / 2) {
    from = cuts[index]; to = cuts[index + 1]; mix = 0.5 - (cuts[index + 1].atSeconds - seconds) / dissolve;
  }
  const camera = id => store.camerasById.get(id);
  if (!from) {
    drawCamera(context, store, pictures, camera(cuts[index].cameraId), seconds, canvas);
    return;
  }
  drawCamera(context, store, pictures, camera(from.cameraId), seconds, canvas);
  context.globalAlpha = mix * mix * (3 - 2 * mix);
  drawCamera(context, store, pictures, camera(to.cameraId), seconds, canvas);
  context.globalAlpha = 1;
}
