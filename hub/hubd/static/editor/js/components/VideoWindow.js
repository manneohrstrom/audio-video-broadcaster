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
import { LookRenderer } from "../look/look-renderer.js";
import { drawTitle, opacityAt, logoOpacityAt } from "../title/title-layout.js";

// Pictures are put together (camera, dissolve) on this hidden canvas, then shown through
// the look (filter and effects) on the visible WebGL canvas.
const PICTURE_WIDTH = 1280;
const PICTURE_HEIGHT = 720;

const NO_FULL_VIDEO_NOTE = "thumbnails only: full video needs a secure page (see the hub runbook, Editor)";

export function VideoWindow({ store, thumbnailsByCamera, player }) {
  const canvasRef = useRef(null);
  const badgeRef = useRef(null);
  const wrapperRef = useRef(null);

  const noteRef = useRef(null);
  const titleRef = useRef(null);
  const logoRef = useRef(null);

  // The title layer: drawn again when the title changes (and once Figtree has loaded); its
  // opacity follows the playhead in the frame loop below.
  const titleKey = JSON.stringify(store.title);
  useEffect(() => {
    const draw = () => drawTitle(titleRef.current.getContext("2d"), store.title);
    draw();
    document.fonts.load("600 84px Figtree").then(() => document.fonts.load("400 42px Figtree")).then(draw, () => {});
  }, [titleKey]);

  useEffect(() => {
    const picture = document.createElement("canvas");
    picture.width = PICTURE_WIDTH;
    picture.height = PICTURE_HEIGHT;
    store.pictureCanvas = picture;                       // the Look panel's tiles use it too
    let renderer = null;
    try { renderer = new LookRenderer(canvasRef.current); } catch (error) { store.showToast(`No look preview: ${error.message}`); }
    return everyFrame(now => {
    const canvas = picture;
    const pictures = { player, thumbnailsByCamera, now, waiting: false };
    player.prerollNextShot(now);
    player.keepAllRunning(store.playheadSeconds, now);    // only with editing proxies: instant switches
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
    if (renderer) {
      const withLook = !store.comparing;
      const active = withLook ? store.activeOverlay() : null;
      const overlay = overlayVideo(active);
      playOnlyOverlay(active ? active.id : null);
      if (withLook) {
        renderer.setLook(store.look.adjust, store.looks?.adjustments, store.filterSettings(), store.look.filter_strength,
                         store.look.effects.colorpop || 0);
        // The bars in song seconds (the bounce's, minus where the song starts in it).
        renderer.setDownbeats((store.project.bounce.downbeats_s || []).map(d => d - store.bounceOffsetSeconds));
      }
      else renderer.setFilter({}, 0);
      renderer.render(picture, withLook ? store.look.effects : {}, seconds, overlay);
    }
    const titleOpacity = skimCamera ? 0 : opacityAt(store.title, seconds);   // not while skimming a camera
    if (titleRef.current.style.opacity !== String(titleOpacity)) titleRef.current.style.opacity = String(titleOpacity);
    if (logoRef.current) {                   // the logo fades in after the title (or at 5 s)
      const logoOpacity = String(logoOpacityAt(store.title, seconds));
      if (logoRef.current.style.opacity !== logoOpacity) logoRef.current.style.opacity = logoOpacity;
    }
    const shown = skimCamera || store.cameraAt(seconds);
    wrapperRef.current.style.setProperty("--frame-colour", skimCamera ? `var(--color-camera-${(shown.index % 6) + 1})` : "transparent");
    const text = `${shown.number} ${shown.label}` + (skimCamera ? ` · ${formatSeconds(seconds)}` : "");
    if (badgeRef.current.textContent !== text) badgeRef.current.textContent = text;
    });
  }, []);

  const hold = on => () => { if (store.step === "look") { store.comparing = on; store.notify(); } };

  return html`
    <div class="video-stage">
      <div class="video-window" ref=${wrapperRef}
           onPointerDown=${hold(true)} onPointerUp=${hold(false)} onPointerLeave=${hold(false)}>
        <canvas ref=${canvasRef} width="1920" height="1080" aria-label="Video preview"></canvas>
        <canvas class="title-layer" ref=${titleRef} width="1920" height="1080" aria-hidden="true"></canvas>
        ${store.branding && html`
          <div class="brand-border" style=${{ background: store.brandingColour }}></div>
          <div class="brand-logo" ref=${logoRef} style=${{ background: store.brandingColour }}></div>`}
        ${store.step === "look" && html`<div class="video-hold">${store.comparing ? "Without the look" : "Press and hold to compare"}</div>`}
        <div class="video-badge" ref=${badgeRef}></div>
        <div class="video-note" ref=${noteRef} hidden></div>
      </div>
    </div>`;
}

/**
 * A muted, looping <video> of a film overlay's preview, kept per overlay.
 * Only the overlay that's on plays (playOnlyOverlay); the others stay paused on one frame,
 * which is all a Film tile needs. Five overlays all playing made the browser sluggish
 * (2026-11-17).
 */
const overlayVideos = new Map();
export function overlayVideo(overlay) {
  if (!overlay) return null;
  let video = overlayVideos.get(overlay.id);
  if (!video) {
    video = Object.assign(document.createElement("video"),
                          { src: overlay.preview_url, muted: true, loop: true, playsInline: true, preload: "auto" });
    overlayVideos.set(overlay.id, video);
  }
  return { video, mode: overlay.mode, strength: overlay.strength };
}

/** Play the overlay with this id (null: none) and pause every other one. */
export function playOnlyOverlay(id) {
  for (const [overlayId, video] of overlayVideos) {
    if (overlayId === id) {
      if (video.paused) video.play().catch(() => {});
    } else if (!video.paused) {
      video.pause();
    }
  }
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
