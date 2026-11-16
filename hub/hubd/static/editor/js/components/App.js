// The editor page. For now (first step of M-E3): load the project, show the song and its
// cameras, and play the bounce. The timeline, the video and the look follow.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { loadProject } from "../api.js";
import { formatSeconds, NS_PER_SECOND } from "../time-format.js";

const html = htm.bind(h);

/** Camera colours in camera order (styles/tokens.css). */
export function cameraColour(index) {
  return `var(--color-camera-${(index % 6) + 1})`;
}

export function App({ takeId, songId, version }) {
  const [project, setProject] = useState(null);
  const [problem, setProblem] = useState(null);

  useEffect(() => {
    loadProject(takeId, songId, version).then(setProject, error => setProblem(error.message));
  }, [takeId, songId, version]);

  if (problem) return html`<p class="message problem">Can't open this song: ${problem}</p>`;
  if (!project) return html`<p class="message">Opening the song…</p>`;

  const [rangeStart, rangeEnd] = project.range_ns;
  const songSeconds = (rangeEnd - rangeStart) / NS_PER_SECOND;
  return html`
    <div class="editor">
      <header class="editor-header">
        <div class="song-title">
          <strong>${project.song.name}</strong>
          <span class="details">v${project.version} · take ${project.take_id} · ${formatSeconds(songSeconds)}</span>
        </div>
        <span></span>
        <span></span>
      </header>
      <main>
        <p class="message">Edit: ${project.edit.cuts.length} shots (${project.edit_source}). Sync: ${project.bounce.sync.anchor || "by hand"}.</p>
        <div class="camera-list">
          ${project.cameras.map((camera, index) => html`
            <div class="camera-row" key=${camera.id}>
              <span class="camera-key" style=${{ background: cameraColour(index) }}>${index + 1}</span>
              <strong>${camera.label}</strong>
              <span class="details">${camera.kind === "phone" ? "phone" : "Pi camera"}${camera.is_sync_server ? " · sync server" : ""}</span>
            </div>`)}
        </div>
        <audio controls src=${project.bounce.url}></audio>
      </main>
    </div>`;
}
