// Drawing the timeline: one canvas with a ruler, the program strip (the cut as it is),
// one strip per camera (filmstrip thumbnails over the whole song), and the bounce's
// waveform. Redrawn every animation frame from the store; nothing here changes state.
// Sync mode (sync by hand): no program strip, the cameras as they are, and a taller
// waveform that moves with the bounce, over a faint copy of where it was when Sync opened.

export const RULER_HEIGHT = 18;
export const PROGRAM_HEIGHT = 58;
export const CAMERA_HEIGHT = 44;
export const CAMERA_GAP = 6;
export const WAVEFORM_HEIGHT = 50;
export const SYNC_WAVEFORM_HEIGHT = 84;
const GROUP_GAP = 12;
const HANDLE_GRAB_PIXELS = 7;

/**
 * Lanes top to bottom: [{ type, top, height, camera? }], plus the total height.
 * compact (step 2, the look): only the ruler, the program strip and the waveform.
 * sync (Sync mode): the ruler, the cameras and a tall waveform.
 */
export function layoutLanes(cameras, { compact = false, sync = false } = {}) {
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
  add({ type: "program" }, PROGRAM_HEIGHT, GROUP_GAP);
  cameras.forEach((camera, i) => add({ type: "camera", camera }, CAMERA_HEIGHT, i < cameras.length - 1 ? CAMERA_GAP : GROUP_GAP));
  add({ type: "waveform" }, WAVEFORM_HEIGHT, 0);
  return { lanes, height: top };
}

export function laneAt(lanes, y) {
  return lanes.find(lane => y >= lane.top - 3 && y < lane.top + lane.height + 3) || null;
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
 * ({ kind: "edge", cutIndex }), its middle ({ kind: "body", shotIndex }), or null.
 */
export function shotPartAt(store, camera, x) {
  const seconds = store.secondsAtX(x);
  for (let i = 0; i < store.cuts.length; i++) {
    if (store.cuts[i].cameraId !== camera.id) continue;
    const start = store.cuts[i].atSeconds;
    const end = i + 1 < store.cuts.length ? store.cuts[i + 1].atSeconds : store.songSeconds;
    if (i > 0 && Math.abs(x - store.xOf(start)) < HANDLE_GRAB_PIXELS) return { kind: "edge", cutIndex: i };
    if (i + 1 < store.cuts.length && Math.abs(x - store.xOf(end)) < HANDLE_GRAB_PIXELS) return { kind: "edge", cutIndex: i + 1 };
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
  }

  draw(context, width, lanes, height) {
    context.clearRect(0, 0, width, height);
    for (const lane of lanes) {
      if (lane.type === "ruler") this.drawRuler(context, lane, width);
      else if (lane.type === "program") this.drawProgram(context, lane, width);
      else if (lane.type === "camera") this.drawCamera(context, lane, width);
      else this.drawWaveform(context, lane, width);
    }
    this.drawHoverLine(context, lanes, width, height);
    this.drawPlayhead(context, height);
  }

  // -- pieces
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
      context.rect(x0, lane.top, x1 - x0, lane.height);
      context.clip();
      this.drawFilmstrip(context, shot.camera, lane.top + 5, lane.height - 5, x0, x1);
      for (const [roughStart, roughEnd] of store.roughSections(shot.camera)) {
        if (roughStart < shot.end && shot.start < roughEnd) {
          const a = store.xOf(Math.max(roughStart, shot.start));
          this.hatch(context, a, lane.top, store.xOf(Math.min(roughEnd, shot.end)) - a, lane.height, this.colours.rough, 5);
        }
      }
      context.fillStyle = colour;
      context.fillRect(x0, lane.top, x1 - x0, 5);
      this.drawShotLabel(context, `${shot.camera.number} ${shot.camera.label}`, colour, x0, x1, lane.top);
      context.restore();
    }
    this.drawSelectionPreview(context, lane);
    store.cuts.forEach((cut, index) => {
      if (index === 0) return;
      const x = Math.round(store.xOf(cut.atSeconds));
      if (x < -8 || x > width + 8) return;
      const selected = index === store.selectedCutIndex;
      const hot = store.hover?.cutHandle === index;
      context.fillStyle = selected ? this.colours.accent : cut.by === "user" ? this.colours.userCut : this.colours.autoCut;
      context.fillRect(x - 1, lane.top, 2, lane.height);
      const knobWidth = selected || hot ? 9 : 7;
      context.beginPath();
      context.roundRect(x - knobWidth / 2, lane.top + lane.height / 2 - 9, knobWidth, 18, 3);
      context.fill();
      if (selected) { context.strokeStyle = this.colours.background; context.lineWidth = 1.5; context.stroke(); }
    });
  }

  drawShotLabel(context, text, colour, x0, x1, top) {
    if (x1 - x0 < 40) return;
    context.font = `600 11px ${this.colours.uiFont}`;
    const textWidth = context.measureText(text).width;
    if (x1 - x0 < textWidth + 16) return;
    context.fillStyle = "rgba(17,16,15,0.8)";
    context.fillRect(x0 + 5, top + 10, textWidth + 10, 17);
    context.fillStyle = colour;
    context.fillText(text, x0 + 10, top + 22);
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
    context.globalAlpha = 0.55;
    context.fillStyle = colour;
    context.fillRect(x0, lane.top, x1 - x0, lane.height);
    context.globalAlpha = 1;
    context.fillRect(x0, lane.top, x1 - x0, 5);
    this.drawShotLabel(context, `${gesture.camera.number} ${gesture.camera.label} · ${(end - start).toFixed(1)} s`,
                       this.colours.accent, x0, Math.max(x1, x0 + 200), lane.top);
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
    if (store.syncMode) {                     // Sync mode: outline the camera that's showing
      this.drawRecordingMarks(context, lane, camera);
      if (store.cameraAt(0) === camera) {
        context.strokeStyle = colour;
        context.lineWidth = 2;
        context.strokeRect(1, lane.top + 1, width - 2, lane.height - 2);
      }
      context.restore();
      return;
    }
    const shots = store.shots();
    // Dim what isn't in the cut, so the shots that are stand out.
    for (const shot of shots) {
      if (shot.cameraId === camera.id) continue;
      context.fillStyle = this.colours.dimmed;
      context.fillRect(store.xOf(shot.start), lane.top, store.xOf(shot.end) - store.xOf(shot.start), lane.height);
    }
    this.drawRecordingMarks(context, lane, camera);
    // This camera's shots in the cut: outlined in its colour, with a bar along the bottom.
    for (const shot of shots) {
      if (shot.cameraId !== camera.id) continue;
      const x0 = store.xOf(shot.start);
      const x1 = store.xOf(shot.end);
      const sliding = store.gesture?.kind === "slide" && store.gesture.shotIndex === shot.index;
      context.strokeStyle = sliding ? this.colours.accent : colour;
      context.lineWidth = sliding ? 3 : 2;
      context.strokeRect(x0 + 1, lane.top + 1, x1 - x0 - 2, lane.height - 2);
      context.fillStyle = colour;
      context.fillRect(x0, lane.top + lane.height - 4, x1 - x0, 4);
    }
    // A stretch being dragged out on this strip.
    const gesture = store.gesture;
    if (gesture?.kind === "select" && gesture.moved && gesture.camera === camera) {
      const x0 = store.xOf(Math.min(gesture.startSeconds, gesture.currentSeconds));
      const x1 = store.xOf(Math.max(gesture.startSeconds, gesture.currentSeconds));
      context.globalAlpha = 0.4;
      context.fillStyle = colour;
      context.fillRect(x0, lane.top, x1 - x0, lane.height);
      context.globalAlpha = 1;
      context.strokeStyle = this.colours.accent;
      context.lineWidth = 2;
      context.strokeRect(x0 + 1, lane.top + 1, x1 - x0 - 2, lane.height - 2);
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
    dimmed: "rgba(17,16,15,0.66)",
    notRecording: "rgba(236,229,214,0.07)",
    rough: "rgba(236,229,214,0.4)",
    autoCut: "rgba(228,220,203,0.6)",
    hoverLine: "rgba(228,220,203,0.5)",
    waveform: "#6b6458",
    waveformPlayed: "#d9cfbb",
    waveformGhost: "rgba(217,207,187,0.3)",
    uiFont: token("--font-ui"),
    numberFont: token("--font-numbers"),
  };
}
