// Mouse (pointer) gestures on the timeline canvas. The gesture in progress lives in
// store.gesture so the drawing can show it:
//
//   on a camera strip, empty part:   press-drag-release  -> { kind: "select" }  that camera for the stretch
//   on a camera strip, a shot's body: drag               -> { kind: "slide" }   the shot slides, same length
//   on a shot's edge, or a cut handle on the program strip -> { kind: "moveCut" }
//   (a slid shot or a moved edge runs over the shots in its way: edit-model.js moveEdge, slideShot)
//   on the ruler, the video's start or end marker         -> { kind: "trim" }    moves it (Story step: the story's)
//   anywhere else (ruler, program, waveform)             -> { kind: "scrub" }   moves the playhead
//   a plain click on a camera strip just moves the playhead.
//   wheel: scroll sideways; pinch or Ctrl/Cmd + wheel: zoom around the mouse.
//
// Sync mode (sync by hand) has its own, simpler set:
//   on the waveform: drag                -> { kind: "moveBounce" }  the bounce moves with the mouse
//   on a camera strip: press             -> that camera shows, then a scrub
//   anywhere else                        -> { kind: "scrub" }
import * as EditModel from "../edit-model.js";
import { laneAt, cutHandleAt, shotPartAt, trimMarkerAt } from "./timeline-drawing.js";
import { NS_PER_SECOND } from "../time-format.js";

const DRAG_THRESHOLD_PIXELS = 4;

export function attachTimelinePointer(canvas, store, getLanes, transport) {
  const localPoint = event => {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  function onMove(event) {
    const { x, y } = localPoint(event);
    const seconds = store.secondsAtX(x);
    const lane = laneAt(getLanes(), y);
    const gesture = store.gesture;
    if (gesture) {
      if (Math.abs(x - gesture.startX) > DRAG_THRESHOLD_PIXELS) gesture.moved = true;
      if (gesture.kind === "select") {
        gesture.currentSeconds = seconds;
        store.hover = { seconds, x, lane: gesture.lane, skimCamera: gesture.camera };
      } else if (gesture.kind === "slide") {
        gesture.result = EditModel.slideShot(gesture.originalCuts, gesture.shotIndex, seconds - gesture.startSeconds, store.songSeconds);
        store.cuts = gesture.result.cuts;
        if (!store.isPlaying) store.playheadSeconds = gesture.result.start + 0.001;
      } else if (gesture.kind === "moveCut") {
        gesture.result = EditModel.moveEdge(gesture.originalCuts, gesture.cutIndex, seconds, store.songSeconds, gesture.held);
        gesture.edgeSeconds = gesture.result.edgeSeconds;
        store.cuts = gesture.result.cuts;
        if (!store.isPlaying) store.playheadSeconds = gesture.edgeSeconds + 0.001;
      } else if (gesture.kind === "scrub") {
        store.playheadSeconds = seconds;
      } else if (gesture.kind === "trim") {
        if (gesture.marker === "in") store.setMarkIn(seconds); else store.setMarkOut(seconds);
        if (!store.isPlaying) store.playheadSeconds = gesture.marker === "in" ? store.markIn : store.markOut;
      } else if (gesture.kind === "moveBounce") {
        store.setBounceStartNs(gesture.originalStartNs + (x - gesture.startX) / store.pixelsPerSecond * NS_PER_SECOND);
      }
      return;
    }
    if (store.syncMode) {
      store.hover = { seconds, x, lane, skimCamera: lane?.type === "camera" ? lane.camera : null };
      canvas.style.cursor = lane?.type === "waveform" ? "grab" : lane?.type === "camera" ? "pointer" : "default";
      return;
    }
    const cutHandle = lane?.type === "program" ? cutHandleAt(store, x) : -1;
    const shotPart = lane?.type === "camera" ? shotPartAt(store, lane.camera, x) : null;
    const trimMarker = lane?.type === "ruler" ? trimMarkerAt(store, x) : null;
    store.hover = { seconds, x, lane, cutHandle, trimMarker, skimCamera: lane?.type === "camera" ? lane.camera : null };
    canvas.style.cursor = cutHandle > 0 || shotPart?.kind === "edge" || trimMarker ? "ew-resize"
      : shotPart ? "grab" : lane?.type === "camera" ? "crosshair" : "default";
  }

  function onDown(event) {
    if (event.button !== 0) return;
    canvas.setPointerCapture(event.pointerId);
    const { x, y } = localPoint(event);
    const seconds = store.secondsAtX(x);
    const lane = laneAt(getLanes(), y);
    const base = { startX: x, startSeconds: seconds, moved: false, lane };
    store.selectedCutIndex = -1;
    if (store.syncMode && startSyncGesture(base, lane)) return;

    const trimMarker = lane?.type === "ruler" ? trimMarkerAt(store, x) : null;
    if (trimMarker) {
      store.gesture = { ...base, kind: "trim", marker: trimMarker };
      store.notify();
      return;
    }
    const cutHandle = lane?.type === "program" ? cutHandleAt(store, x) : -1;
    if (cutHandle > 0) {
      store.gesture = { ...base, kind: "moveCut", cutIndex: cutHandle, held: null,
                        edgeSeconds: store.cuts[cutHandle].atSeconds, originalCuts: store.cuts };
      store.selectedCutIndex = cutHandle;
      store.notify();
      return;
    }
    if (lane?.type === "camera" && !store.syncMode) {
      const part = shotPartAt(store, lane.camera, x);
      if (part?.kind === "edge") {
        store.gesture = { ...base, kind: "moveCut", cutIndex: part.cutIndex, held: part.held,
                          edgeSeconds: store.cuts[part.cutIndex].atSeconds, originalCuts: store.cuts };
        store.selectedCutIndex = part.cutIndex;
      } else if (part) {
        store.gesture = { ...base, kind: "slide", shotIndex: part.shotIndex, originalCuts: store.cuts };
        canvas.style.cursor = "grabbing";
      } else {
        store.gesture = { ...base, kind: "select", camera: lane.camera, currentSeconds: seconds };
      }
      store.notify();
      return;
    }
    store.gesture = { ...base, kind: "scrub", wasPlaying: store.isPlaying };
    if (store.isPlaying) transport.pause();
    store.playheadSeconds = seconds;
    store.notify();
  }

  /** Sync mode: true when the press started one of its own gestures. */
  function startSyncGesture(base, lane) {
    if (lane?.type === "waveform") {
      store.gesture = { ...base, kind: "moveBounce", originalStartNs: store.bounceStartNs };
      canvas.style.cursor = "grabbing";
      store.notify();
      return true;
    }
    if (lane?.type === "camera") store.showCamera(lane.camera);
    return false;                                       // then a scrub, as anywhere else
  }

  function onUp() {
    const gesture = store.gesture;
    if (!gesture) return;
    store.gesture = null;
    if (gesture.kind === "select") {
      if (gesture.moved) {
        const start = Math.min(gesture.startSeconds, gesture.currentSeconds);
        const end = Math.max(gesture.startSeconds, gesture.currentSeconds);
        store.chooseCameraForStretch(gesture.camera, start, end);
        transport.seek(start);
      } else {
        transport.seek(gesture.startSeconds);            // a plain click: just move the playhead
      }
    } else if (gesture.kind === "slide" || gesture.kind === "moveCut") {
      const result = gesture.result;
      store.cuts = gesture.originalCuts;
      if (gesture.moved && result && result.cuts !== gesture.originalCuts) {
        store.beginChange();
        const changed = EditModel.normalize(result.cuts);
        // The edge stays selected where it ended up (it may have merged away).
        if (gesture.kind === "moveCut") store.selectedCutIndex = changed.findIndex((cut, i) => i > 0 && Math.abs(cut.atSeconds - result.edgeSeconds) < 1e-6);
        store.setCuts(changed, {
          warnAbout: { camera: store.camerasById.get(result.cameraId), start: result.start, end: result.end } });
      } else if (gesture.kind === "slide") {
        transport.seek(gesture.startSeconds);
      }
    } else if (gesture.kind === "scrub" && gesture.wasPlaying) {
      transport.play();
    }
    store.notify();
  }

  function onLeave() {
    if (!store.gesture) store.hover = null;
  }

  function onWheel(event) {
    event.preventDefault();
    const { x } = localPoint(event);
    if (event.ctrlKey || event.metaKey) {
      store.zoomTo(store.pixelsPerSecond * Math.exp(-event.deltaY * 0.01), store.secondsAtX(x));
      return;
    }
    const sideways = event.deltaX !== 0 ? event.deltaX : event.deltaY;
    store.scrollBy(sideways / store.pixelsPerSecond);
    if (store.hover) store.hover.seconds = store.secondsAtX(store.hover.x);
  }

  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", onUp);
  canvas.addEventListener("pointerleave", onLeave);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  return () => {
    canvas.removeEventListener("pointermove", onMove);
    canvas.removeEventListener("pointerdown", onDown);
    canvas.removeEventListener("pointerup", onUp);
    canvas.removeEventListener("pointercancel", onUp);
    canvas.removeEventListener("pointerleave", onLeave);
    canvas.removeEventListener("wheel", onWheel);
  };
}
