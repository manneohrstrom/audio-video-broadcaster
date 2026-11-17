// Under the video: play, frame steps, the time, which camera is on, and the zoom slider.
// The play key is the instrument's red eye: it glows while the song plays. The time sits on
// an LCD, with the unlit segments ("8:88.8") faintly behind the lit ones.
import { useEffect, useRef } from "preact/hooks";
import { html } from "./html.js";
import { everyFrame } from "../frame-loop.js";
import { formatSeconds } from "../time-format.js";
import { Icon } from "./icons.js";

/** The LCD's unlit segments behind a reading: every digit as an 8. */
const unlitSegments = text => text.replace(/\d/g, "8");

export function TransportBar({ store, transport }) {
  const timeRef = useRef(null);
  const unlitRef = useRef(null);
  const onAirRef = useRef(null);

  useEffect(() => everyFrame(() => {
    transport.tick();
    const time = formatSeconds(store.playheadSeconds);
    if (timeRef.current.textContent !== time) {
      timeRef.current.textContent = time;
      unlitRef.current.textContent = unlitSegments(time);
    }
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
      <button class=${"play-button" + (store.isPlaying ? " playing" : "")} onClick=${() => transport.toggle()}
              aria-label=${store.isPlaying ? "Pause" : "Play"} title="Play / pause (space)">
        <span class="iris"></span><${Icon} name=${store.isPlaying ? "pause" : "play"} size=${16} />
      </button>
      <button class="round-key" onClick=${() => transport.stepFrames(-1)} aria-label="Back one frame" title="Back one frame (←)">
        <${Icon} name="frameBack" size=${16} /></button>
      <button class="round-key" onClick=${() => transport.stepFrames(1)} aria-label="Forward one frame" title="Forward one frame (→)">
        <${Icon} name="frameForward" size=${16} /></button>
      <span class="lcd" role="timer" aria-label="Playhead time">
        <span class="lcd-reading"><span class="lcd-unlit" ref=${unlitRef} aria-hidden="true">8:88.8</span><span class="time" ref=${timeRef}>0:00.0</span></span>
        <span class="duration">${formatSeconds(store.songSeconds)}</span>
      </span>
      <span class="on-air" ref=${onAirRef}></span>
      <span class="spacer"></span>
      <label class="zoom" for="zoom"><${Icon} name="magnifier" size=${16} /><span>Zoom</span>
        <input id="zoom" type="range" min="0" max="1000" value=${Math.round(store.zoomPosition * 1000)}
               aria-label="Zoom" onInput=${event => store.setZoomPosition(event.target.value / 1000)} />
      </label>
    </div>`;
}
