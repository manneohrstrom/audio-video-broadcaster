// The video window. For now it shows the strip thumbnails, enlarged: the camera that is on
// at the playhead (with the dissolve between shots), or the camera under the mouse while
// skimming. The full-quality player (WebCodecs, spike E2) replaces the picture source next.
import { useEffect, useRef } from "preact/hooks";
import { html } from "./html.js";
import { everyFrame } from "../frame-loop.js";
import { cutIndexAt } from "../edit-model.js";
import { formatSeconds } from "../time-format.js";

export function VideoWindow({ store, thumbnailsByCamera }) {
  const canvasRef = useRef(null);
  const badgeRef = useRef(null);
  const wrapperRef = useRef(null);

  useEffect(() => everyFrame(() => {
    const canvas = canvasRef.current;
    const context = canvas.getContext("2d");
    const { camera: skimCamera, seconds } = whatToShow(store);
    context.fillStyle = "#000";
    context.fillRect(0, 0, canvas.width, canvas.height);
    if (skimCamera) {
      drawCamera(context, store, thumbnailsByCamera, skimCamera, seconds, canvas);
    } else {
      drawProgram(context, store, thumbnailsByCamera, seconds, canvas);
    }
    const shown = skimCamera || store.cameraAt(seconds);
    wrapperRef.current.style.setProperty("--frame-colour", skimCamera ? `var(--color-camera-${(shown.index % 6) + 1})` : "transparent");
    const text = `${shown.number} ${shown.label}` + (skimCamera ? ` · ${formatSeconds(seconds)}` : "");
    if (badgeRef.current.textContent !== text) badgeRef.current.textContent = text;
  }), []);

  return html`
    <div class="video-stage">
      <div class="video-window" ref=${wrapperRef}>
        <canvas ref=${canvasRef} width="960" height="540" aria-label="Video preview"></canvas>
        <div class="video-badge" ref=${badgeRef}></div>
        <div class="video-note">preview from thumbnails · full video next</div>
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

function drawCamera(context, store, thumbnailsByCamera, camera, seconds, canvas) {
  if (!store.isRecording(camera, seconds)) return;
  thumbnailsByCamera.get(camera.id)?.draw(context, store.cameraFileSeconds(camera, seconds), 0, 0, canvas.width, canvas.height);
}

/** The program picture, with the dissolve centred on each cut. */
function drawProgram(context, store, thumbnailsByCamera, seconds, canvas) {
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
    drawCamera(context, store, thumbnailsByCamera, camera(cuts[index].cameraId), seconds, canvas);
    return;
  }
  drawCamera(context, store, thumbnailsByCamera, camera(from.cameraId), seconds, canvas);
  context.globalAlpha = mix * mix * (3 - 2 * mix);
  drawCamera(context, store, thumbnailsByCamera, camera(to.cameraId), seconds, canvas);
  context.globalAlpha = 1;
}
