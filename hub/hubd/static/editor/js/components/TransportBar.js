// Under the video: play, frame steps, the time, which camera is on, and the zoom slider.
import { useEffect, useRef } from "preact/hooks";
import { html } from "./html.js";
import { everyFrame } from "../frame-loop.js";
import { formatSeconds } from "../time-format.js";

export function TransportBar({ store, transport }) {
  const timeRef = useRef(null);
  const onAirRef = useRef(null);

  useEffect(() => everyFrame(() => {
    transport.tick();
    const time = formatSeconds(store.playheadSeconds);
    if (timeRef.current.textContent !== time) timeRef.current.textContent = time;
    const camera = store.cameraAt(store.playheadSeconds);
    const onAir = `On: ${camera.number} ${camera.label}`;
    if (onAirRef.current.dataset.text !== onAir) {
      onAirRef.current.dataset.text = onAir;
      onAirRef.current.innerHTML = "";
      const dot = document.createElement("i");
      dot.style.background = `var(--color-camera-${(camera.index % 6) + 1})`;
      onAirRef.current.append(dot, onAir);
    }
  }), []);

  return html`
    <div class="transport-bar">
      <button class="play-button" onClick=${() => transport.toggle()} aria-label=${store.isPlaying ? "Pause" : "Play"}
              title="Play / pause (space)">${store.isPlaying ? "❚❚" : "▶"}</button>
      <button class="button small" onClick=${() => transport.stepFrames(-1)} title="Back one frame (←)">◀︎ 1</button>
      <button class="button small" onClick=${() => transport.stepFrames(1)} title="Forward one frame (→)">1 ▶︎</button>
      <span class="time" ref=${timeRef}>0:00.0</span>
      <span class="duration">/ ${formatSeconds(store.songSeconds)}</span>
      <span class="on-air" ref=${onAirRef}></span>
      <span class="spacer"></span>
      <label class="zoom" for="zoom">Zoom
        <input id="zoom" type="range" min="0" max="1000" value=${Math.round(store.zoomPosition * 1000)}
               aria-label="Zoom" onInput=${event => store.setZoomPosition(event.target.value / 1000)} />
      </label>
    </div>`;
}
