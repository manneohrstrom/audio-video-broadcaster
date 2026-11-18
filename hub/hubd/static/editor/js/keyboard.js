// Keyboard shortcuts (shown in the footer):
//   space: play / pause            1-9: that camera from the playhead on
//   left / right: one frame        shift + left / right: one second
//   delete / backspace: remove the selected cut
//   cmd/ctrl + Z: undo, + shift: redo       + / -: zoom in / out
// Sync mode (sync by hand):
//   , / . : the bounce 10 ms earlier / later    shift + , / . : one frame earlier / later
//   1-9: show that camera
import { FRAME_SECONDS } from "./editor-store.js";

const NUDGE_SECONDS = 0.01;

export function attachKeyboard(store, transport) {
  function onKey(event) {
    // Typing in a text field (the title's boxes): its keys are text, not shortcuts.
    const target = event.target;
    const typing = (target.tagName === "INPUT" && !["range", "checkbox", "color", "button"].includes(target.type))
      || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable;
    if (typing) return;
    if (store.syncMode && (event.code === "Comma" || event.code === "Period") && !event.metaKey && !event.ctrlKey) {
      event.preventDefault();
      const direction = event.code === "Comma" ? -1 : 1;
      store.moveBounceBy(direction * (event.shiftKey ? FRAME_SECONDS : NUDGE_SECONDS));
    } else if (event.code === "Space") {
      event.preventDefault();
      transport.toggle();
    } else if (/^Digit[1-9]$/.test(event.code) && !event.metaKey && !event.ctrlKey) {
      const camera = store.cameras[Number(event.code.slice(5)) - 1];
      if (camera) store.cutToCameraAtPlayhead(camera);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const direction = event.key === "ArrowLeft" ? -1 : 1;
      if (event.shiftKey) transport.seek(store.playheadSeconds + direction); else transport.stepFrames(direction);
    } else if ((event.key === "Backspace" || event.key === "Delete") && store.selectedCutIndex > 0) {
      event.preventDefault();
      store.removeSelectedCut();
    } else if (event.code === "KeyZ" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      if (event.shiftKey) store.redo(); else store.undo();
    } else if (event.key === "+" || event.key === "=") {
      store.zoomTo(store.pixelsPerSecond * 1.5);
    } else if (event.key === "-") {
      store.zoomTo(store.pixelsPerSecond / 1.5);
    }
  }
  document.addEventListener("keydown", onKey);
  return () => document.removeEventListener("keydown", onKey);
}
