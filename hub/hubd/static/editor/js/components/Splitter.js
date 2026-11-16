// The bar between the video and the timeline: drag up and down to resize the video,
// double-click to go back to the default. The height is remembered in this browser.
import { html } from "./html.js";

const STORAGE_KEY = "editor-video-height";

export function savedVideoHeight() {
  try {
    const value = Number(localStorage.getItem(STORAGE_KEY));
    return value > 100 ? value : null;
  } catch (error) {
    return null;
  }
}

export function Splitter({ onResize }) {
  function startDrag(event) {
    const bar = event.currentTarget;
    bar.setPointerCapture(event.pointerId);
    bar.classList.add("dragging");
    const top = bar.previousElementSibling.getBoundingClientRect().top;
    let height = null;
    const move = moveEvent => {
      height = Math.round(Math.max(140, Math.min(window.innerHeight - 200, moveEvent.clientY - top)));
      onResize(height);
    };
    const stop = () => {
      bar.classList.remove("dragging");
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", stop);
      try { if (height) localStorage.setItem(STORAGE_KEY, String(height)); } catch (error) { /* private mode */ }
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", stop);
  }

  function reset() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (error) { /* private mode */ }
    onResize(null);
  }

  return html`<div class="splitter" role="separator" aria-orientation="horizontal" aria-label="Drag to resize the video"
                   title="Drag to make the video bigger or smaller (double-click to reset)"
                   onPointerDown=${startDrag} onDblClick=${reset}></div>`;
}
