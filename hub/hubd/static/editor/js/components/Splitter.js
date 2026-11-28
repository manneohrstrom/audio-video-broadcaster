// The bar between the video and the timeline: drag to resize the video, double-click to go
// back to the default. Horizontal (sync mode: the timeline below, drag up and down) sets the
// video's height; vertical (the cut: the timeline on the right, drag sideways) sets the video
// column's width. Each is remembered in this browser.
import { html } from "./html.js";

const SIZES = {
  horizontal: { key: "editor-video-height", smallest: 140, room: () => window.innerHeight - 200 },
  // The timeline keeps at least 360 px: its labels and a usable stretch of lanes.
  vertical: { key: "editor-video-width", smallest: 320, room: () => window.innerWidth - 360 },
};

function savedSize(orientation) {
  try {
    const value = Number(localStorage.getItem(SIZES[orientation].key));
    return value > 100 ? value : null;
  } catch (error) {
    return null;
  }
}

/** The video's height the user dragged to in sync mode, or null for the default. */
export function savedVideoHeight() {
  return savedSize("horizontal");
}

/** The video column's width the user dragged to in the cut, or null for the default. */
export function savedVideoWidth() {
  return savedSize("vertical");
}

export function Splitter({ onResize, orientation = "horizontal" }) {
  const size = SIZES[orientation];
  const vertical = orientation === "vertical";

  function startDrag(event) {
    const bar = event.currentTarget;
    bar.setPointerCapture(event.pointerId);
    bar.classList.add("dragging");
    const start = bar.previousElementSibling.getBoundingClientRect();
    let length = null;
    const move = moveEvent => {
      const reached = vertical ? moveEvent.clientX - start.left : moveEvent.clientY - start.top;
      length = Math.round(Math.max(size.smallest, Math.min(size.room(), reached)));
      onResize(length);
    };
    const stop = () => {
      bar.classList.remove("dragging");
      bar.removeEventListener("pointermove", move);
      bar.removeEventListener("pointerup", stop);
      try { if (length) localStorage.setItem(size.key, String(length)); } catch (error) { /* private mode */ }
    };
    bar.addEventListener("pointermove", move);
    bar.addEventListener("pointerup", stop);
  }

  function reset() {
    try { localStorage.removeItem(size.key); } catch (error) { /* private mode */ }
    onResize(null);
  }

  return html`<div class=${"splitter" + (vertical ? " vertical" : "")} role="separator"
                   aria-orientation=${vertical ? "vertical" : "horizontal"} aria-label="Drag to resize the video"
                   title="Drag to make the video bigger or smaller (double-click to reset)"
                   onPointerDown=${startDrag} onDblClick=${reset}></div>`;
}
