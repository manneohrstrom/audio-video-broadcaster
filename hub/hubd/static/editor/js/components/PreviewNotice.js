// A slim notice under the header while the take's preview copies (editing proxies, made by
// hub-ingest) aren't all there yet. Without them every camera switch seeks in the full-size
// original on the drive, and the strip thumbnail stands in meanwhile (user, 2026-11-28).
// It asks the hub again every 30 s; when all copies are ready it offers a reload, which saves
// the edit first, since the player picks its files when the song opens.
import { useEffect, useState } from "preact/hooks";
import { html } from "./html.js";
import { loadProject } from "../api.js";

const CHECK_EVERY_MS = 30_000;

/** How many of the cameras have a preview copy. */
function countReady(cameras) {
  return cameras.filter(camera => camera.proxy_url).length;
}

async function reloadSaved(store) {
  if (store.saveState !== "saved") await store.saveNow();
  location.reload();
}

export function PreviewNotice({ store, project }) {
  const total = project.cameras.length;
  const [ready, setReady] = useState(countReady(project.cameras));
  const allAtOpen = countReady(project.cameras) === total;

  useEffect(() => {
    if (allAtOpen || ready === total) return undefined;
    const timer = setInterval(() => {
      loadProject(project.take_id, project.song.id, project.version)
        .then(latest => setReady(countReady(latest.cameras)))
        .catch(() => {});                          // the hub busy or restarting: ask again next time
    }, CHECK_EVERY_MS);
    return () => clearInterval(timer);
  }, [project, ready, total, allAtOpen]);

  if (allAtOpen) return null;
  if (ready === total) {
    return html`
      <div class="preview-notice done" role="status">
        All ${total} preview copies are ready.
        <button class="button" onClick=${() => reloadSaved(store)}>Reload for smooth playback</button>
      </div>`;
  }
  return html`
    <div class="preview-notice" role="status">
      Preview copies: ${ready} of ${total} ready. Until they all are, switching camera is slow and
      a thumbnail stands in. The hub makes them when no take is running.
    </div>`;
}
