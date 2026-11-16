// The timeline: a label column (one per lane) and the canvas. The canvas is redrawn every
// frame from the store; the labels are ordinary components.
import { useEffect, useRef } from "preact/hooks";
import { html } from "./html.js";
import { layoutLanes, TimelineDrawing, readColours } from "../timeline/timeline-drawing.js";
import { attachTimelinePointer } from "../timeline/timeline-pointer.js";
import { everyFrame } from "../frame-loop.js";

export function Timeline({ store, transport, thumbnailsByCamera, waveform, compact = false }) {
  const canvasRef = useRef(null);
  const wrapperRef = useRef(null);
  const { lanes, height } = layoutLanes(store.cameras, { compact });

  useEffect(() => {
    const canvas = canvasRef.current;
    const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
    const drawing = new TimelineDrawing(store, thumbnailsByCamera, waveform, readColours(canvas));
    let width = 0;
    const resize = new ResizeObserver(() => {
      width = wrapperRef.current.clientWidth;
      canvas.width = Math.round(width * pixelRatio);
      canvas.height = Math.round(height * pixelRatio);
      canvas.style.height = `${height}px`;
      store.setTimelineWidth(width);
      store.notify();
    });
    resize.observe(wrapperRef.current);
    const detachPointer = attachTimelinePointer(canvas, store, () => lanes, transport);
    const stopDrawing = everyFrame(() => {
      if (!width) return;
      const context = canvas.getContext("2d");
      context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      drawing.waveform = waveform;
      drawing.draw(context, width, lanes, height);
    });
    return () => { resize.disconnect(); detachPointer(); stopDrawing(); };
  }, [waveform]);

  const onAir = store.cameraAt(store.playheadSeconds);
  return html`
    <section class="timeline" aria-label="Timeline">
      <div class="lane-labels" style=${{ height: `${height}px` }}>
        ${lanes.map(lane => {
          const style = { top: `${lane.top}px`, height: `${lane.height}px` };
          if (lane.type === "program") return html`<div class="lane-label" style=${style}><div><strong>Video</strong><small>what's showing</small></div></div>`;
          if (lane.type === "waveform") return html`<div class="lane-label" style=${style}><div><strong>Bounce</strong><small>${store.project.bounce.file_name}</small></div></div>`;
          if (lane.type !== "camera") return null;
          const camera = lane.camera;
          return html`
            <button class=${"lane-label camera" + (onAir === camera ? " on-air" : "")}
                    style=${{ ...style, "--camera-colour": `var(--color-camera-${(camera.index % 6) + 1})` }}
                    title=${`Show ${camera.label} from the playhead (key ${camera.number})`}
                    onClick=${() => store.cutToCameraAtPlayhead(camera)}>
              <span class="camera-key">${camera.number}</span>
              <span class="camera-name"><strong>${camera.label}</strong>
                <small>${camera.kind === "phone" ? "phone" : "Pi camera"}${camera.is_sync_server ? " · sync" : ""}${camera.thumbnails_ready ? "" : " · pictures coming"}</small></span>
            </button>`;
        })}
      </div>
      <div class="timeline-canvas" ref=${wrapperRef}>
        <canvas ref=${canvasRef}
                aria-label="Cameras over the song. Hover to look; drag on a camera to use it for that stretch; drag a shot to slide it."></canvas>
      </div>
    </section>`;
}
