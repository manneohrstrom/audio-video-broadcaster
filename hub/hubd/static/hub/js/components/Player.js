// Playback straight from the STUDIO drive (hub-ingest mounts it read-only on request):
// one camera of a take (switch cameras at the same time), or a rendered video.
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "shared/html.js";
import { Dialog } from "./Dialog.js";
import { nameWithId, takeName } from "../names.js";

const mediaUrl = (take, path) => `/media/${encodeURIComponent(take)}/${path.split("/").map(encodeURIComponent).join("/")}`;

export function Player({ take, camera, cameras, labels, file, names, onSwitch, onClose }) {
  const takeTitle = nameWithId(takeName(names, take), take, take);
  const videoRef = useRef(null);
  const resumeAt = useRef(0);
  const [note, setNote] = useState("Opening the drive…");
  const source = file ? mediaUrl(take, file) : mediaUrl(take, camera + ".mov");

  useEffect(() => {
    const video = videoRef.current;
    video.onloadedmetadata = () => {
      if (resumeAt.current) video.currentTime = Math.min(resumeAt.current, video.duration || resumeAt.current);
      setNote("");
      video.play().catch(() => {});
    };
    video.onerror = () => setNote("Can't play this file: the STUDIO drive may be unplugged or busy.");
  }, [source]);
  useEffect(() => () => { const video = videoRef.current; if (video) { video.pause(); video.removeAttribute("src"); video.load(); } }, []);

  function switchTo(next) {
    resumeAt.current = videoRef.current ? videoRef.current.currentTime : 0;      // same moment on the next camera
    setNote("Opening the drive…");
    onSwitch({ camera: next });
  }
  return html`
    <${Dialog} title=${file ? `${takeTitle} · ${file.split("/").pop()}` : takeTitle} onClose=${onClose} wide>
      ${cameras && html`<div class="segmented" role="group" aria-label="Camera">
        ${cameras.map(c => html`<button aria-pressed=${String(c === camera)} onClick=${() => switchTo(c)}
                                        title=${(labels || {})[c] && labels[c] !== c ? c : ""}>${(labels || {})[c] || c}</button>`)}</div>`}
      <video ref=${videoRef} class="player-video" src=${source} controls playsinline preload="metadata"></video>
      <p class="note">${note}</p>
    <//>`;
}
