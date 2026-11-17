// Sync mode's header (sync by hand, docs/editor-plan.md section 4b): where the bounce sits,
// how far it has moved, nudge buttons, and Save / Cancel.
//
// Moving the bounce changes the music under the pictures. The cut stays on the same frames:
// its times are on the hub clock, so it fits the new placement when the editor reopens.
import { html } from "./html.js";
import { FRAME_SECONDS } from "../editor-store.js";
import { NS_PER_SECOND } from "../time-format.js";
import { Icon } from "shared/icons.js";

const NUDGES = [
  { seconds: -FRAME_SECONDS, label: "1 frame", key: "⇧,", icon: "nudgeEarlier" },
  { seconds: -0.01, label: "10 ms", key: ",", icon: "nudgeEarlier" },
  { seconds: 0.01, label: "10 ms", key: ".", icon: "nudgeLater" },
  { seconds: FRAME_SECONDS, label: "1 frame", key: "⇧.", icon: "nudgeLater" },
];

/** What Sync mode started from, in words (project.sync_start.source). */
function startingPointText(start) {
  if (start.source === "automatic") return `from the automatic sync${start.confidence ? `, confidence ${start.confidence}` : ""}`;
  if (start.source === "by hand") return "from your last sync by hand";
  if (start.source === "best guess") return "from the automatic sync's best guess (it wasn't sure)";
  return "at the start of the take (no automatic sync to start from)";
}

/** "+0.120", "-1.033" (seconds; the LCD face has a plain hyphen, not a minus sign) */
function signedSeconds(seconds) {
  const rounded = Math.round(seconds * 1000) / 1000;
  return `${rounded < 0 ? "-" : "+"}${Math.abs(rounded).toFixed(3)}`;
}

/** Where the music starts against the video, e.g. "music starts 5.012 s before the video". */
function placementText(store) {
  const seconds = (store.bounceStartNs - store.rangeStartNs) / NS_PER_SECOND;
  const amount = `${Math.abs(seconds).toFixed(3)} s`;
  return seconds < 0 ? `music starts ${amount} before the video` : `music starts ${amount} into the video`;
}

/** Leave Sync mode: back to the editor (if the bounce is synced) or to the hub page. */
export function leaveSyncMode({ synced }) {
  if (!synced) {
    location.href = "/";
    return;
  }
  const query = new URLSearchParams(location.search);
  query.delete("mode");
  location.search = query.toString();
}

export function SyncBar({ store }) {
  const project = store.project;
  const start = project.sync_start;
  const moved = store.bounceMovedSeconds;
  const automaticNs = start.automatic_start_ns;
  const isSynced = start.source === "automatic" || start.source === "by hand";
  const saving = store.syncSaveState === "saving";

  async function save() {
    if (await store.saveBouncePlacement()) leaveSyncMode({ synced: true });
  }

  return html`
    <header class="editor-header sync-header">
      <div class="song-title">
        <strong>${project.song.name}</strong>
        <span class="details">Sync by hand · bounce v${project.version} · ${project.bounce.file_name}</span>
      </div>
      <div class="sync-nudges" role="group" aria-label="Move the bounce">
        ${NUDGES.slice(0, 2).map(nudge => html`
          <button class="button small" onClick=${() => store.moveBounceBy(nudge.seconds)}
                  title=${`Bounce earlier (${nudge.key})`}><${Icon} name=${nudge.icon} size=${16} />${nudge.label}</button>`)}
        <div class="sync-readout" aria-live="polite">
          ${moved
            ? html`<strong class="lcd-text moved">${signedSeconds(moved)}<span class="unit">s</span></strong>`
            : html`<strong>not moved</strong>`}
          <small>${placementText(store)}</small>
        </div>
        ${NUDGES.slice(2).map(nudge => html`
          <button class="button small" onClick=${() => store.moveBounceBy(nudge.seconds)}
                  title=${`Bounce later (${nudge.key})`}>${nudge.label}<${Icon} name=${nudge.icon} size=${16} /></button>`)}
      </div>
      <div class="header-actions">
        <span class="sync-start" title=${start.reason || ""}>${startingPointText(start)}</span>
        ${automaticNs != null && automaticNs !== store.bounceStartNs && start.source === "by hand" && html`
          <button class="button" onClick=${() => store.setBounceStartNs(automaticNs)}
                  title="Put the bounce where the automatic sync had it">Automatic</button>`}
        <button class="button" disabled=${!moved} onClick=${() => store.setBounceStartNs(store.syncStartNs)}
                title="Undo the moves: back to where Sync opened"><${Icon} name="reset" size=${16} />Reset</button>
        <button class="button" onClick=${() => leaveSyncMode({ synced: isSynced })}
                title="Leave without saving">Cancel</button>
        <button class="button signal" disabled=${saving} onClick=${save}
                title="Save this as the bounce's sync and go back to the editor"><${Icon} name="check" size=${16} />${saving ? "Saving…" : "Save sync"}</button>
      </div>
    </header>`;
}
