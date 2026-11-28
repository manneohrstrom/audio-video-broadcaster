// The video window: the camera that is on at the playhead (with the dissolve between
// shots), or the camera under the mouse while skimming. Pictures come from the WebCodecs
// player (media/video-player.js); until a camera's frame is decoded, its strip thumbnail
// stands in, enlarged. In the Story step a 9:16 frame on top shows the story's slice
// (story.js): drag it for the camera that's showing; the slice is copied every frame into
// the Story panel's preview.
import { useEffect, useRef } from "preact/hooks";
import { html } from "./html.js";
import { everyFrame } from "../frame-loop.js";
import { cutIndexAt } from "../edit-model.js";
import { formatSeconds } from "../time-format.js";
import { fullVideoAvailable } from "../media/video-player.js";
import { LookRenderer } from "../look/look-renderer.js";
import { CENTRE, SLICE_SHARE, sliceLeftAt, sliceLeftShare, incomingShiftShare } from "../story.js";
import { drawCard, drawStrip, opacitiesAt, logoOpacityAt, borderOpacityAt, titleText, linesBelow, BRAND_TEXT, BRAND_STYLE, CREAM, CARD_SIZE, DETAIL_SIZE, FONT_FAMILY } from "../title/title-layout.js";

// Pictures are put together (camera, dissolve) on this hidden canvas, then shown through
// the look (filter and effects) on the visible WebGL canvas.
const PICTURE_WIDTH = 1280;
const PICTURE_HEIGHT = 720;

const NO_FULL_VIDEO_NOTE = "thumbnails only: full video needs a secure page (see the hub runbook, Editor)";

export function VideoWindow({ store, thumbnailsByCamera, player }) {
  const canvasRef = useRef(null);
  const badgeRef = useRef(null);
  const whyRef = useRef(null);            // the smart cut's reason for the shot on screen
  const wrapperRef = useRef(null);

  const noteRef = useRef(null);
  // The title sequence's layers (title/title-layout.js): black, "BROCKLEY LIVE", the title, the strip.
  const blackRef = useRef(null);
  const brandRef = useRef(null);
  const titleRef = useRef(null);
  const stripRef = useRef(null);
  const logoRef = useRef(null);
  const borderRef = useRef(null);
  const storyFrameRef = useRef(null);
  const storyDragRef = useRef(null);       // { camera, grabbedAt (share of the slice's width from its left edge) }

  // The title sequence's cards: drawn again when the title changes (and once Outfit has
  // loaded); their opacities follow the playhead in the frame loop below.
  const titleKey = JSON.stringify(store.title);
  useEffect(() => {
    const draw = () => {
      drawCard(brandRef.current.getContext("2d"), BRAND_TEXT, CREAM, BRAND_STYLE);
      drawCard(titleRef.current.getContext("2d"), titleText(store.title), store.title.colour || CREAM);
      drawStrip(stripRef.current.getContext("2d"), linesBelow(store.title));
    };
    draw();
    // allSettled: a font that doesn't load (2026-11-21: the hub didn't serve .otf) mustn't keep the others from showing.
    Promise.allSettled([document.fonts.load(`700 ${CARD_SIZE}px ${FONT_FAMILY}`), document.fonts.load(`700 ${DETAIL_SIZE}px ${FONT_FAMILY}`),
                        document.fonts.load(`${BRAND_STYLE.weight} ${CARD_SIZE}px ${BRAND_STYLE.family}`)])
      .then(draw);
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
    if (store.step === "story") drawStory(store, skimCamera, seconds, renderer ? canvasRef.current : picture, storyFrameRef.current);
    // The title sequence, timed from the video's start (not while skimming a camera, nor before
    // the start, nor in the Story step: a story has none).
    const showSequence = !skimCamera && store.step !== "story" && seconds >= store.inSeconds - 1e-6;
    const layers = opacitiesAt(showSequence ? store.title : null, seconds - store.inSeconds);
    for (const [ref, opacity] of [[blackRef, layers.black], [brandRef, layers.brand], [titleRef, layers.title], [stripRef, layers.strip]]) {
      const value = String(Math.round(opacity * 1000) / 1000);
      if (ref.current.style.opacity !== value) ref.current.style.opacity = value;
    }
    if (logoRef.current) {                   // the logo (and with the sequence the border) fades in after it, or the logo at 5 s
      const logoOpacity = String(logoOpacityAt(store.title, seconds, store.inSeconds));
      if (logoRef.current.style.opacity !== logoOpacity) logoRef.current.style.opacity = logoOpacity;
      const borderOpacity = String(borderOpacityAt(store.title, seconds, store.inSeconds));
      if (borderRef.current.style.opacity !== borderOpacity) borderRef.current.style.opacity = borderOpacity;
    }
    const shown = skimCamera || store.cameraAt(seconds);
    wrapperRef.current.style.setProperty("--frame-colour", skimCamera ? `var(--color-camera-${(shown.index % 6) + 1})` : "transparent");
    const text = `${shown.number} ${shown.label}` + (skimCamera ? ` · ${formatSeconds(seconds)}` : "");
    if (badgeRef.current.textContent !== text) badgeRef.current.textContent = text;
    showWhy(whyRef.current, store, skimCamera ? null : seconds);
    });
  }, []);

  const hold = on => () => { if (store.step === "look") { store.comparing = on; store.notify(); } };

  // The Story step: press on the slice to grab it (or anywhere else to bring it there), drag
  // it sideways. It moves the camera showing when the drag starts.
  const shareAt = event => {
    const box = wrapperRef.current.getBoundingClientRect();
    return (event.clientX - box.left) / box.width;
  };
  const moveSliceTo = event => {
    const drag = storyDragRef.current;
    const left = shareAt(event) - drag.grabbedAt * SLICE_SHARE;
    store.setFraming(drag.camera.id, left / (1 - SLICE_SHARE));
  };
  const onPointerDown = event => {
    if (store.step !== "story") { hold(true)(); return; }
    const { camera: skimCamera, seconds } = whatToShow(store);
    const camera = skimCamera || store.cameraAt(seconds);
    const left = sliceLeftShare(store.storyFraming[camera.id] ?? CENTRE);
    const inside = (shareAt(event) - left) / SLICE_SHARE;
    storyDragRef.current = { camera, grabbedAt: inside >= 0 && inside <= 1 ? inside : 0.5 };
    wrapperRef.current.setPointerCapture(event.pointerId);
    moveSliceTo(event);
  };
  const onPointerMove = event => { if (storyDragRef.current) moveSliceTo(event); };
  const onPointerUp = () => { storyDragRef.current = null; hold(false)(); };

  return html`
    <div class="video-stage">
      <div class=${"video-window" + (store.step === "story" ? " story-step" : "")} ref=${wrapperRef}
           onPointerDown=${onPointerDown} onPointerMove=${onPointerMove} onPointerUp=${onPointerUp}
           onPointerCancel=${onPointerUp} onPointerLeave=${() => { if (!storyDragRef.current) hold(false)(); }}>
        <canvas ref=${canvasRef} width="1920" height="1080" aria-label="Video preview"></canvas>
        <div class="title-black" ref=${blackRef} aria-hidden="true"></div>
        <canvas class="title-layer" ref=${brandRef} width="1920" height="1080" aria-hidden="true"></canvas>
        <canvas class="title-layer" ref=${titleRef} width="1920" height="1080" aria-hidden="true"></canvas>
        <canvas class="title-layer" ref=${stripRef} width="1920" height="1080" aria-hidden="true"></canvas>
        ${store.branding && store.step !== "story" && html`
          <div class="brand-border" ref=${borderRef} style=${{ background: store.brandingColour }}></div>
          <div class="brand-logo" ref=${logoRef} style=${{ background: store.brandingColour }}></div>`}
        ${store.step === "story" && html`<div class="story-frame" ref=${storyFrameRef} aria-hidden="true"></div>`}
        ${store.step === "look" && html`<div class="video-hold">${store.comparing ? "Without the look" : "Press and hold to compare"}</div>`}
        <div class="video-badge" ref=${badgeRef}></div>
        <div class="video-why" ref=${whyRef} hidden></div>
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

/**
 * The Story step, every frame: place the 9:16 frame where the slice is at this time (the
 * skimmed camera's place, or the program's with its glide at cuts), dim it outside the story's
 * start and end, and copy the slice of the finished picture into the Story panel's preview.
 * @param {HTMLCanvasElement} source  the picture with its look (or without, if WebGL is missing)
 */
function drawStory(store, skimCamera, seconds, source, frame) {
  const left = skimCamera
    ? sliceLeftShare(store.storyFraming[skimCamera.id] ?? CENTRE)
    : sliceLeftAt(store.cuts, store.storyFraming, store.dissolveSeconds, seconds);
  const outside = seconds < store.storyInSeconds - 1e-6 || seconds > store.storyOutSeconds + 1e-6;
  if (frame) {
    frame.style.setProperty("--left", `${(left * 100).toFixed(3)}%`);
    frame.style.setProperty("--right", `${((left + SLICE_SHARE) * 100).toFixed(3)}%`);
    frame.classList.toggle("outside", outside);
  }
  const preview = store.storyPreviewCanvas;
  if (!preview) return;
  const context = preview.getContext("2d");
  context.drawImage(source, left * source.width, 0, SLICE_SHARE * source.width, source.height, 0, 0, preview.width, preview.height);
  if (outside) {
    context.fillStyle = "rgba(0, 0, 0, 0.6)";
    context.fillRect(0, 0, preview.width, preview.height);
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

/** One camera's picture: the decoded frame, else the thumbnail (and note that we're waiting).
 *  shift: moved sideways by this share of the width (the story's dissolve, story.js). */
function drawCamera(context, store, pictures, camera, seconds, canvas, shift = 0) {
  if (!store.isRecording(camera, seconds)) return;
  const x = shift * canvas.width;
  const frame = pictures.player.pictureFor(camera, seconds, pictures.now);
  if (frame) {
    context.drawImage(frame, x, 0, canvas.width, canvas.height);
    return;
  }
  pictures.waiting = true;
  pictures.thumbnailsByCamera.get(camera.id)?.draw(context, store.cameraFileSeconds(camera, seconds), x, 0, canvas.width, canvas.height);
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
  // The Story step: at a cut where the story dissolves, the incoming picture is moved so its
  // slice lies on the outgoing one's, as in the render.
  const shift = store.step === "story" ? incomingShiftShare(from, to, store.storyFraming) : 0;
  drawCamera(context, store, pictures, camera(to.cameraId), seconds, canvas, shift);
  context.globalAlpha = 1;
}

/** The edit decisions while playing (user, 2026-11-28): in the Cut step, the smart cut's
 *  reason for the shot on screen, what the hub changed, and the section the music is in.
 *  Hidden while skimming a camera, for cuts made by hand, and in the other steps. Only the
 *  preview shows it; the render never does. */
function showWhy(element, store, seconds) {
  const cut = seconds == null || store.step !== "cut" || store.syncMode ? null : store.cuts[cutIndexAt(store.cuts, seconds)];
  const section = cut && store.music ? store.music.sections.find(s => s.start <= seconds && seconds < s.end) : null;
  const parts = cut && cut.reason ? [["why-section", section ? section.label : ""], ["why-reason", cut.reason],
                                     ["why-note", cut.note ? `hub: ${cut.note}` : ""]].filter(([, text]) => text) : [];
  const key = parts.map(([, text]) => text).join("|");
  if (element.dataset.text === key) return;
  element.dataset.text = key;
  element.replaceChildren(...parts.map(([kind, text]) => Object.assign(document.createElement("span"), { className: kind, textContent: text })));
  element.hidden = !parts.length;
}
