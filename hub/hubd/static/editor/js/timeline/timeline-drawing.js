// Drawing the timeline: one canvas with a ruler, the program strip (the cut as it is),
// one strip per camera (filmstrip thumbnails over the whole song), and the bounce's
// waveform. Redrawn every animation frame from the store; nothing here changes state.
// Sync mode (sync by hand): no program strip, the cameras as they are, and a taller
// waveform that moves with the bounce, over a faint copy of where it was when Sync opened.

import { cutIndexAt } from "../edit-model.js";

export const RULER_HEIGHT = 18;
export const PROGRAM_HEIGHT = 58;
export const CAMERA_HEIGHT = 44;
export const CAMERA_GAP = 6;
export const WAVEFORM_HEIGHT = 50;
export const SYNC_WAVEFORM_HEIGHT = 84;
export const MUSIC_HEIGHT = 30;       // the AI cut's Music lane: the sections and moments it heard
const GROUP_GAP = 12;
const HANDLE_GRAB_PIXELS = 7;
const BAR_HEIGHT = 3;                 // the camera-colour bar under a shot

/**
 * Lanes top to bottom: [{ type, top, height, camera? }], plus the total height.
 * compact (step 2, the look): only the ruler, the program strip and the waveform.
 * sync (Sync mode): the ruler, the cameras and a tall waveform.
 * music (the cut, after an AI cut): the Music lane under the program strip.
 */
export function layoutLanes(cameras, { compact = false, sync = false, music = false } = {}) {
  const lanes = [];
  let top = 0;
  const add = (lane, height, gapAfter) => { lanes.push({ ...lane, top, height }); top += height + gapAfter; };
  add({ type: "ruler" }, RULER_HEIGHT, 4);
  if (sync) {
    cameras.forEach((camera, i) => add({ type: "camera", camera }, CAMERA_HEIGHT, i < cameras.length - 1 ? CAMERA_GAP : GROUP_GAP));
    add({ type: "waveform" }, SYNC_WAVEFORM_HEIGHT, 0);
    return { lanes, height: top };
  }
  if (compact) {
    add({ type: "program" }, 46, 8);
    add({ type: "waveform" }, 40, 0);
    return { lanes, height: top };
  }
  add({ type: "program" }, PROGRAM_HEIGHT, music ? CAMERA_GAP : GROUP_GAP);
  if (music) add({ type: "music" }, MUSIC_HEIGHT, GROUP_GAP);
  cameras.forEach((camera, i) => add({ type: "camera", camera }, CAMERA_HEIGHT, i < cameras.length - 1 ? CAMERA_GAP : GROUP_GAP));
  add({ type: "waveform" }, WAVEFORM_HEIGHT, 0);
  return { lanes, height: top };
}

export function laneAt(lanes, y) {
  return lanes.find(lane => y >= lane.top - 3 && y < lane.top + lane.height + 3) || null;
}

/** Is there a start or end to show: the video's (when trimmed), or in the Story step the story's (always). */
function showsMarks(store) {
  return !store.syncMode && (store.step === "story" || store.isTrimmed);
}

/** On the ruler: the start ("in") or end ("out") marker under x, or null (only set ones show).
 *  The video's start and end, or in the Story step the story's (store.markIn, store.markOut). */
export function trimMarkerAt(store, x) {
  if (!showsMarks(store)) return null;
  if (store.markIn > 0.001 && Math.abs(store.xOf(store.markIn) - x) < HANDLE_GRAB_PIXELS) return "in";
  if (store.markOut < store.songSeconds - 0.001 && Math.abs(store.xOf(store.markOut) - x) < HANDLE_GRAB_PIXELS) return "out";
  return null;
}

/** Index of the cut boundary under x on the program strip, or -1. */
export function cutHandleAt(store, x) {
  for (let i = 1; i < store.cuts.length; i++) {
    if (Math.abs(store.xOf(store.cuts[i].atSeconds) - x) < HANDLE_GRAB_PIXELS) return i;
  }
  return -1;
}

/**
 * On a camera strip, what's under x: the edge of a shot of this camera that's in the cut
 * ({ kind: "edge", cutIndex, held }: held "after" for its start, "before" for its end), its middle ({ kind: "body", shotIndex }), or null.
 */
export function shotPartAt(store, camera, x) {
  const seconds = store.secondsAtX(x);
  for (let i = 0; i < store.cuts.length; i++) {
    if (store.cuts[i].cameraId !== camera.id) continue;
    const start = store.cuts[i].atSeconds;
    const end = i + 1 < store.cuts.length ? store.cuts[i + 1].atSeconds : store.songSeconds;
    if (i > 0 && Math.abs(x - store.xOf(start)) < HANDLE_GRAB_PIXELS) return { kind: "edge", cutIndex: i, held: "after" };
    if (i + 1 < store.cuts.length && Math.abs(x - store.xOf(end)) < HANDLE_GRAB_PIXELS) return { kind: "edge", cutIndex: i + 1, held: "before" };
    if (seconds >= start && seconds < end) return { kind: "body", shotIndex: i };
  }
  return null;
}

export class TimelineDrawing {
  constructor(store, thumbnailsByCamera, waveform, colours) {
    this.store = store;
    this.thumbnailsByCamera = thumbnailsByCamera;   // camera id -> CameraThumbnails
    this.waveform = waveform;                       // { rate, peaks } or null while loading
    this.colours = colours;                         // resolved CSS tokens (canvas can't read var())
    this.pixelRatio = Math.min(2, window.devicePixelRatio || 1);   // as the canvas (Timeline.js)
  }

  draw(context, width, lanes, height) {
    context.clearRect(0, 0, width, height);
    for (const lane of lanes) {
      if (lane.type === "ruler") this.drawRuler(context, lane, width);
      else if (lane.type === "program") this.drawProgram(context, lane, width);
      else if (lane.type === "camera") this.drawCamera(context, lane, width);
      else if (lane.type === "music") this.drawMusic(context, lane, width);
      else this.drawWaveform(context, lane, width);
    }
    this.drawLaneSeparators(context, lanes, width);
    this.drawTrim(context, lanes, width, height);
    this.drawHoverLine(context, lanes, width, height);
    this.drawPlayhead(context, height);
    this.drawHoverTip(context, width, height);
  }

  // -- the AI cut: what it heard, and why it cut (docs/ai-cut-plan.md)
  /** The Music lane: each section the model heard as a block with its name and leader,
   *  each moment as a small mark on top. */
  drawMusic(context, lane, width) {
    const store = this.store;
    this.laneBackground(context, lane, width);
    const music = store.music;
    if (!music) return;
    context.font = `500 11px ${this.colours.uiFont}`;
    music.sections.forEach((section, index) => {
      const x0 = store.xOf(section.start);
      const x1 = store.xOf(section.end);
      if (x1 < 0 || x0 > width) return;
      context.fillStyle = index % 2 ? this.colours.musicEven : this.colours.musicOdd;
      context.fillRect(this.snap(x0), lane.top + 2, Math.max(1, this.snap(x1) - this.snap(x0) - 1), lane.height - 4);
      const text = section.label;
      if (x1 - x0 > context.measureText(text).width + 12) {
        context.fillStyle = this.colours.text;
        context.fillText(text, Math.max(x0, 0) + 6, lane.top + lane.height / 2 + 4);
      }
    });
    context.fillStyle = this.colours.accent;
    for (const moment of music.moments) {
      const x = store.xOf(moment.at);
      if (x < -4 || x > width + 4) continue;
      context.beginPath();
      context.moveTo(x - 4, lane.top);
      context.lineTo(x + 4, lane.top);
      context.lineTo(x, lane.top + 6);
      context.closePath();
      context.fill();
    }
  }

  /** What is under the mouse, in words: on the program strip an AI cut's reason (and what
   *  the hub changed); on the Music lane the section's notes or a moment. */
  drawHoverTip(context, width, height) {
    const store = this.store;
    const hover = store.hover;
    if (!hover || store.gesture || !hover.lane) return;
    const lines = hoverLines(store, hover);
    if (!lines.length) return;
    context.font = `500 11px ${this.colours.uiFont}`;
    const wrapped = lines.flatMap(line => wrapText(context, line, Math.min(360, width - 24)));
    const boxWidth = Math.max(...wrapped.map(line => context.measureText(line).width)) + 16;
    const boxHeight = wrapped.length * 15 + 10;
    const x = Math.max(4, Math.min(width - boxWidth - 4, store.xOf(hover.seconds) + 12));
    const below = hover.lane.top + hover.lane.height + 4;
    const y = below + boxHeight < height ? below : Math.max(2, hover.lane.top - boxHeight - 4);
    context.fillStyle = this.colours.tipBackground;
    context.beginPath();
    context.roundRect(x, y, boxWidth, boxHeight, 6);
    context.fill();
    context.fillStyle = this.colours.text;
    wrapped.forEach((line, i) => context.fillText(line, x + 8, y + 17 + i * 15));
  }

  // -- pieces
  /** A faint hairline in the gap between neighbouring camera strips, so each reads as its own track. */
  drawLaneSeparators(context, lanes, width) {
    context.fillStyle = this.colours.separator;
    for (let i = 1; i < lanes.length; i++) {
      const above = lanes[i - 1];
      if (above.type !== "camera" || lanes[i].type !== "camera") continue;
      const y = this.snap((above.top + above.height + lanes[i].top) / 2);
      context.fillRect(0, y, width, 1 / this.pixelRatio * Math.max(1, Math.round(this.pixelRatio / 2)));
    }
  }

  laneBackground(context, lane, width) {
    context.fillStyle = this.colours.lane;
    context.beginPath();
    context.roundRect(0, lane.top, width, lane.height, 5);
    context.fill();
  }

  /** Filmstrip tiles for `camera` between x0 and x1, on a grid fixed to song time (no shimmer when scrolling). */
  drawFilmstrip(context, camera, top, height, x0, x1) {
    const store = this.store;
    const tileWidth = height * 16 / 9;
    const viewStartX = store.viewStartSeconds * store.pixelsPerSecond;
    const first = Math.floor((Math.max(0, x0) + viewStartX) / tileWidth);
    const last = Math.floor((x1 + viewStartX) / tileWidth);
    const thumbnails = this.thumbnailsByCamera.get(camera.id);
    for (let tile = first; tile <= last; tile++) {
      const x = tile * tileWidth - viewStartX;
      const middleSeconds = Math.min(store.songSeconds, (tile + 0.5) * tileWidth / store.pixelsPerSecond);
      if (!store.isRecording(camera, middleSeconds) || !thumbnails ||
          !thumbnails.draw(context, store.cameraFileSeconds(camera, middleSeconds), x, top, tileWidth, height)) {
        context.fillStyle = this.colours.noPicture;
        context.fillRect(x, top, tileWidth, height);
      }
    }
  }

  hatch(context, x, top, width, height, colour, spacing = 6) {
    if (width <= 0) return;
    context.save();
    context.beginPath();
    context.rect(x, top, width, height);
    context.clip();
    context.strokeStyle = colour;
    context.lineWidth = 1.5;
    context.beginPath();
    for (let i = x - height; i < x + width + height; i += spacing) {
      context.moveTo(i, top + height);
      context.lineTo(i + height, top);
    }
    context.stroke();
    context.restore();
  }

  drawRuler(context, lane, width) {
    const store = this.store;
    const steps = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300];
    const major = steps.find(step => step * store.pixelsPerSecond >= 64) || 600;
    const minor = major / (major >= 10 ? 5 : 2);
    const visibleEnd = store.viewStartSeconds + width / store.pixelsPerSecond;
    context.fillStyle = this.colours.tick;
    for (let t = Math.floor(store.viewStartSeconds / minor) * minor; t <= visibleEnd; t += minor) {
      context.fillRect(Math.round(store.xOf(t)), lane.top + lane.height - 5, 1, 5);
    }
    context.font = `500 10px ${this.colours.numberFont}`;
    for (let t = Math.floor(store.viewStartSeconds / major) * major; t <= visibleEnd; t += major) {
      const x = Math.round(store.xOf(t));
      context.fillStyle = this.colours.tick;
      context.fillRect(x, lane.top + lane.height - 10, 1, 10);
      context.fillStyle = this.colours.textMuted;
      const minutes = Math.floor(t / 60);
      const seconds = t - minutes * 60;
      const label = major < 1 ? seconds.toFixed(1).padStart(4, "0") : String(Math.round(seconds)).padStart(2, "0");
      context.fillText(`${minutes}:${label}`, x + 4, lane.top + 11);
    }
  }

  /** x snapped to the device's pixel grid, so 1-pixel lines and bars are crisp and even. */
  snap(x) {
    return Math.round(x * this.pixelRatio) / this.pixelRatio;
  }

  /** A bar from x0 to x1 (snapped at both ends, so neighbouring bars meet without seams). */
  bar(context, x0, x1, top, height) {
    const a = this.snap(x0);
    context.fillRect(a, top, this.snap(x1) - a, height);
  }

  drawProgram(context, lane, width) {
    const store = this.store;
    this.laneBackground(context, lane, width);
    for (const shot of store.shots()) {
      const x0 = store.xOf(shot.start);
      const x1 = store.xOf(shot.end);
      if (x1 < 0 || x0 > width) continue;
      const colour = this.colours.cameras[shot.camera.index % this.colours.cameras.length];
      context.save();
      context.beginPath();
      context.rect(this.snap(x0), lane.top, this.snap(x1) - this.snap(x0), lane.height);
      context.clip();
      this.drawFilmstrip(context, shot.camera, lane.top, lane.height, x0, x1);
      for (const [roughStart, roughEnd] of store.roughSections(shot.camera)) {
        if (roughStart < shot.end && shot.start < roughEnd) {
          const a = store.xOf(Math.max(roughStart, shot.start));
          this.hatch(context, a, lane.top, store.xOf(Math.min(roughEnd, shot.end)) - a, lane.height, this.colours.rough, 5);
        }
      }
      context.fillStyle = colour;
      this.bar(context, x0, x1, lane.top + lane.height - BAR_HEIGHT, BAR_HEIGHT);
      this.drawShotLabel(context, shot.camera.label, colour, x0, x1, lane.top);
      context.restore();
    }
    this.drawSelectionPreview(context, lane);
    store.cuts.forEach((cut, index) => {
      if (index > 0) this.drawSplice(context, lane, width, cut, index);
    });
  }

  /** A cut on the program strip: a hairline with a small notch on top, like a tape splice.
   *  Hover thickens it; the selected cut turns the selection colour. */
  drawSplice(context, lane, width, cut, index) {
    const store = this.store;
    const x = this.snap(store.xOf(cut.atSeconds));
    if (x < -8 || x > width + 8) return;
    const selected = index === store.selectedCutIndex;
    const hot = store.hover?.cutHandle === index || (store.gesture?.kind === "moveCut" && Math.abs(store.gesture.edgeSeconds - cut.atSeconds) < 1e-6);
    const lineWidth = selected || hot ? 2 : 1;
    context.fillStyle = selected ? this.colours.accent : cut.by === "user" ? this.colours.userCut : this.colours.autoCut;
    context.fillRect(x - lineWidth / 2, lane.top, lineWidth, lane.height);
    const notch = selected || hot ? 5 : 3.5;
    context.beginPath();
    context.moveTo(x - notch, lane.top);
    context.lineTo(x + notch, lane.top);
    context.lineTo(x, lane.top + notch * 1.2);
    context.closePath();
    context.fill();
  }

  /** The camera's name on its shot: a small dark pill with a dot in the camera's colour. */
  drawShotLabel(context, text, colour, x0, x1, top) {
    if (x1 - x0 < 40) return;
    context.font = `500 11px ${this.colours.uiFont}`;
    const textWidth = context.measureText(text).width;
    if (x1 - x0 < textWidth + 28) return;
    const left = this.snap(x0 + 6);
    context.fillStyle = "rgba(17,16,15,0.72)";
    context.beginPath();
    context.roundRect(left, top + 7, textWidth + 21, 16, 8);
    context.fill();
    context.fillStyle = colour;
    context.beginPath();
    context.arc(left + 8, top + 15, 3, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = this.colours.text;
    context.fillText(text, left + 15, top + 19);
  }

  /** While dragging out a stretch on a camera strip: show it on the program strip too. */
  drawSelectionPreview(context, lane) {
    const gesture = this.store.gesture;
    if (gesture?.kind !== "select" || !gesture.moved) return;
    const store = this.store;
    const start = Math.min(gesture.startSeconds, gesture.currentSeconds);
    const end = Math.max(gesture.startSeconds, gesture.currentSeconds);
    const colour = this.colours.cameras[gesture.camera.index % this.colours.cameras.length];
    const x0 = store.xOf(start);
    const x1 = store.xOf(end);
    context.globalAlpha = 0.35;
    context.fillStyle = colour;
    this.bar(context, x0, x1, lane.top, lane.height);
    context.globalAlpha = 1;
    this.bar(context, x0, x1, lane.top + lane.height - BAR_HEIGHT, BAR_HEIGHT);
    this.drawShotLabel(context, `${gesture.camera.label} · ${(end - start).toFixed(1)} s`, colour, x0, Math.max(x1, x0 + 200), lane.top);
  }

  drawCamera(context, lane, width) {
    const store = this.store;
    const camera = lane.camera;
    const colour = this.colours.cameras[camera.index % this.colours.cameras.length];
    this.laneBackground(context, lane, width);
    context.save();
    context.beginPath();
    context.rect(0, lane.top, width, lane.height);
    context.clip();
    this.drawFilmstrip(context, camera, lane.top, lane.height, 0, width);
    const bottom = lane.top + lane.height - BAR_HEIGHT;
    if (store.syncMode) {                     // Sync mode: the camera that's showing gets the bar
      this.drawRecordingMarks(context, lane, camera);
      if (store.cameraAt(0) !== camera) {
        context.fillStyle = this.colours.dimmedLight;
        context.fillRect(0, lane.top, width, lane.height);
      } else {
        context.fillStyle = colour;
        context.fillRect(0, bottom, width, BAR_HEIGHT);
      }
      context.restore();
      return;
    }
    const shots = store.shots();
    // Dim what isn't in the cut, so the shots that are stand out at full brightness.
    for (const shot of shots) {
      if (shot.cameraId === camera.id) continue;
      context.fillStyle = this.colours.dimmed;
      this.bar(context, store.xOf(shot.start), store.xOf(shot.end), lane.top, lane.height);
    }
    this.drawRecordingMarks(context, lane, camera);
    // This camera's shots in the cut: a bar in its colour along the bottom. No outlines.
    for (const shot of shots) {
      if (shot.cameraId !== camera.id) continue;
      const sliding = store.gesture?.kind === "slide" && store.gesture.shotIndex === shot.index;
      if (sliding) {
        context.fillStyle = this.colours.lift;
        this.bar(context, store.xOf(shot.start), store.xOf(shot.end), lane.top, lane.height);
      }
      context.fillStyle = sliding ? this.colours.accent : colour;
      this.bar(context, store.xOf(shot.start), store.xOf(shot.end), bottom, BAR_HEIGHT);
    }
    // A stretch being dragged out on this strip: lifted, with the selection colour along the bottom.
    const gesture = store.gesture;
    if (gesture?.kind === "select" && gesture.moved && gesture.camera === camera) {
      const x0 = store.xOf(Math.min(gesture.startSeconds, gesture.currentSeconds));
      const x1 = store.xOf(Math.max(gesture.startSeconds, gesture.currentSeconds));
      context.fillStyle = this.colours.lift;
      this.bar(context, x0, x1, lane.top, lane.height);
      context.fillStyle = this.colours.accent;
      this.bar(context, x0, x1, bottom, BAR_HEIGHT);
    }
    context.restore();
  }

  /** Not recording: dark and striped. Rough sections (dropped frames): striped. */
  drawRecordingMarks(context, lane, camera) {
    const store = this.store;
    const [recordStart, recordEnd] = store.recordedSpan(camera);
    for (const [a, b] of [[0, recordStart], [recordEnd, store.songSeconds]]) {
      if (b <= a) continue;
      const x0 = store.xOf(a);
      context.fillStyle = this.colours.noPicture;
      context.fillRect(x0, lane.top, store.xOf(b) - x0, lane.height);
      this.hatch(context, x0, lane.top, store.xOf(b) - x0, lane.height, this.colours.notRecording);
    }
    for (const [a, b] of store.roughSections(camera)) {
      this.hatch(context, store.xOf(a), lane.top, store.xOf(b) - store.xOf(a), lane.height, this.colours.rough, 5);
    }
  }

  drawWaveform(context, lane, width) {
    const store = this.store;
    if (store.syncMode) {
      this.laneBackground(context, lane, width);
    } else {
      for (const shot of store.shots()) {                    // the cut, faintly, behind the music
        context.fillStyle = this.colours.cameras[shot.camera.index % this.colours.cameras.length] + "1c";
        context.fillRect(store.xOf(shot.start), lane.top, store.xOf(shot.end) - store.xOf(shot.start), lane.height);
      }
    }
    if (!this.waveform) return;
    if (store.syncMode && store.bounceMovedSeconds !== 0) {  // where the bounce was when Sync opened
      const startOffset = (store.rangeStartNs - store.syncStartNs) / 1e9;
      this.drawPeaks(context, lane, width, startOffset, this.colours.waveformGhost, this.colours.waveformGhost);
    }
    const dragging = store.gesture?.kind === "moveBounce";
    this.drawPeaks(context, lane, width, store.bounceOffsetSeconds,
                   dragging ? this.colours.accent : this.colours.waveformPlayed, dragging ? this.colours.accent : this.colours.waveform);
    if (store.syncMode) this.drawBounceEdges(context, lane, width);
  }

  /** The bounce's start and end as lines, so it's clear where the music begins. */
  drawBounceEdges(context, lane, width) {
    const store = this.store;
    const start = -store.bounceOffsetSeconds;
    const end = start + store.project.bounce.duration_s;
    context.fillStyle = this.colours.accent;
    for (const seconds of [start, end]) {
      const x = Math.round(store.xOf(seconds));
      if (x >= 0 && x <= width) context.fillRect(x, lane.top, 1, lane.height);
    }
  }

  /** The waveform with the bounce's t=0 at song time -offset: lighter left of the playhead. */
  drawPeaks(context, lane, width, bounceOffset, playedColour, colour) {
    const store = this.store;
    const { rate, peaks } = this.waveform;
    const middle = lane.top + lane.height / 2;
    const playheadX = store.xOf(store.playheadSeconds);
    for (let x = 0; x < width; x += 2) {
      const from = Math.floor((store.secondsAtX(x) + bounceOffset) * rate);
      const to = Math.floor((store.secondsAtX(x + 2) + bounceOffset) * rate);
      let loudest = 0;
      for (let i = Math.max(0, from); i <= Math.min(peaks.length - 1, to); i++) loudest = Math.max(loudest, peaks[i]);
      const half = Math.max(0.5, loudest / 255 * lane.height * 0.48);
      context.fillStyle = x < playheadX ? playedColour : colour;
      context.fillRect(x, middle - half, 1.5, half * 2);
    }
  }

  /** Outside the video's start and end (in the Story step: the story's): darkened, with a
   *  bracket on the ruler at each end that is set ("[" at the start, "]" at the end) and a
   *  thin line down through the lanes. */
  drawTrim(context, lanes, width, height) {
    const store = this.store;
    if (!showsMarks(store)) return;
    const ruler = lanes[0];
    const markers = [];
    if (store.markIn > 0.001) markers.push({ x: this.snap(store.xOf(store.markIn)), inward: 1 });
    if (store.markOut < store.songSeconds - 0.001) markers.push({ x: this.snap(store.xOf(store.markOut)), inward: -1 });
    context.fillStyle = this.colours.outsideVideo;
    const inX = store.xOf(store.markIn);
    const outX = store.xOf(store.markOut);
    if (inX > 0) context.fillRect(0, 0, Math.min(width, inX), height);
    if (outX < width) context.fillRect(Math.max(0, outX), 0, width - Math.max(0, outX), height);
    const hot = store.hover?.trimMarker || (store.gesture?.kind === "trim" && store.gesture.marker);
    for (const marker of markers) {
      if (marker.x < -8 || marker.x > width + 8) continue;
      const isHot = hot === (marker.inward > 0 ? "in" : "out");
      context.fillStyle = this.colours.accent;
      context.fillRect(marker.x - 0.5, ruler.top + ruler.height, 1, height - ruler.top - ruler.height);
      const bar = isHot ? 3 : 2;
      const foot = 7 * marker.inward;
      context.fillRect(marker.x - bar / 2, ruler.top, bar, ruler.height);
      context.fillRect(Math.min(marker.x, marker.x + foot), ruler.top, Math.abs(foot), bar);
      context.fillRect(Math.min(marker.x, marker.x + foot), ruler.top + ruler.height - bar, Math.abs(foot), bar);
    }
  }

  drawHoverLine(context, lanes, width, height) {
    const hover = this.store.hover;
    if (!hover || this.store.gesture) return;
    const x = Math.round(this.store.xOf(hover.seconds));
    const top = lanes[1].top;
    context.fillStyle = this.colours.hoverLine;
    context.fillRect(x, top, 1, height - top);
    const minutes = Math.floor(hover.seconds / 60);
    const label = `${minutes}:${(hover.seconds - minutes * 60).toFixed(1).padStart(4, "0")}`;
    context.font = `500 10px ${this.colours.numberFont}`;
    const labelWidth = context.measureText(label).width + 10;
    const boxX = Math.max(0, Math.min(width - labelWidth, x - labelWidth / 2));
    context.fillStyle = this.colours.raised;
    context.fillRect(boxX, 0, labelWidth, 16);
    context.fillStyle = this.colours.text;
    context.fillText(label, boxX + 5, 11);
  }

  drawPlayhead(context, height) {
    const x = Math.round(this.store.xOf(this.store.playheadSeconds));
    context.fillStyle = this.colours.playhead;
    context.fillRect(x - 1, 2, 2, height - 2);
    context.beginPath();
    context.moveTo(x - 6, 0);
    context.lineTo(x + 6, 0);
    context.lineTo(x, 8);
    context.closePath();
    context.fill();
  }
}

/** The colours the canvas needs, read once from the CSS tokens (styles/tokens.css). */
export function readColours(element) {
  const style = getComputedStyle(element);
  const token = name => style.getPropertyValue(name).trim();
  return {
    background: token("--color-background"),
    lane: token("--color-lane"),
    raised: token("--color-raised"),
    text: token("--color-text"),
    textMuted: token("--color-text-muted"),
    accent: token("--color-accent"),
    userCut: token("--color-cream"),
    playhead: token("--color-playhead"),
    cameras: [1, 2, 3, 4, 5, 6].map(n => token(`--color-camera-${n}`)),
    tick: token("--color-line"),
    noPicture: "#0b0a09",
    dimmed: "rgba(17,16,15,0.72)",
    dimmedLight: "rgba(17,16,15,0.45)",
    outsideVideo: "rgba(11,10,9,0.62)",
    lift: "rgba(236,229,214,0.10)",
    separator: "rgba(236,229,214,0.16)",
    notRecording: "rgba(236,229,214,0.07)",
    rough: "rgba(236,229,214,0.4)",
    autoCut: "rgba(228,220,203,0.5)",
    hoverLine: "rgba(228,220,203,0.5)",
    musicOdd: "rgba(236,229,214,0.13)",
    musicEven: "rgba(236,229,214,0.07)",
    tipBackground: "rgba(17,16,15,0.92)",
    waveform: "#6b6458",
    waveformPlayed: "#d9cfbb",
    waveformGhost: "rgba(217,207,187,0.3)",
    uiFont: token("--font-ui"),
    numberFont: token("--font-numbers"),
  };
}

/** The hover box's lines for the lane under the mouse (drawHoverTip), or []. */
function hoverLines(store, hover) {
  if (hover.lane.type === "program") {
    const cut = store.cuts[cutIndexAt(store.cuts, hover.seconds)];
    if (!cut || !cut.reason) return [];
    return [`Why: ${cut.reason}`, ...(cut.note ? [`Hub: ${cut.note}`] : []), ...(cut.by === "ai" ? [] : ["(changed by hand since)"])];
  }
  if (hover.lane.type === "music" && store.music) {
    const near = store.music.moments.find(moment => Math.abs(store.xOf(moment.at) - store.xOf(hover.seconds)) < 6);
    if (near) return [`Moment: ${near.label}`];
    const section = store.music.sections.find(s => s.start <= hover.seconds && hover.seconds < s.end);
    return section ? [section.label, ...(section.notes ? [section.notes] : [])] : [];
  }
  return [];
}

/** Text broken into lines no wider than maxWidth (canvas has no wrapping of its own). */
function wrapText(context, text, maxWidth) {
  const lines = [];
  let line = "";
  for (const word of text.split(/\s+/)) {
    const tried = line ? `${line} ${word}` : word;
    if (line && context.measureText(tried).width > maxWidth) {
      lines.push(line);
      line = word;
    } else {
      line = tried;
    }
  }
  if (line) lines.push(line);
  return lines;
}
