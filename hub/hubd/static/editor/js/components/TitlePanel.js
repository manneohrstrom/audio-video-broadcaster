// Step 3, the title (docs/editor-plan.md section 5b): first where the video starts and ends
// (user, 2026-11-21: the clap and the count-in stay in the edit but not in the video), then
// a title line and the lines below it (usually who's playing), in one colour. It dissolves in all at once at "Appears at" and is
// gone "Stays for" seconds later. Drawn in the preview by title/title-layout.js and in the
// video by hub/ingest/title_render.py.
import { html } from "./html.js";
import { formatSeconds } from "../time-format.js";
import { FADE_SECONDS, MAX_CHARACTERS, MAX_LINES } from "../title/title-layout.js";

const COLOURS = [["#ffffff", "White"], ["#f4e7cc", "Cream"], ["#e8b15a", "Amber"], ["#111111", "Black"]];
const MIN_SECONDS = 1;
const MAX_SECONDS = 20;            // the slider; the hub accepts up to 60

/** Seconds rounded to tenths, kept inside the song. */
function clampStart(seconds, songSeconds) {
  return Math.round(Math.min(Math.max(0, seconds), Math.max(0, songSeconds - MIN_SECONDS)) * 10) / 10;
}

export function TitlePanel({ store, transport }) {
  const title = store.title;
  const set = changes => store.setTitle(changes);
  // Watch it come in: from a moment before it appears.
  const preview = () => { transport.seek(Math.max(0, title.start_s - 1)); if (!store.isPlaying) transport.play(); };
  const extraColour = store.branding && !COLOURS.some(([value]) => value === store.brandingColour)
    ? [[store.brandingColour, "The border's colour"]] : [];

  const titleOutsideVideo = title.on && (title.start_s < store.inSeconds - 0.05 || title.start_s > store.outSeconds - 1);

  return html`
    <section class="look-panel title-panel" aria-label="Title">
      <${StartAndEnd} store=${store} />
      <div class="look-section">
        <label class="title-switch">
          <input type="checkbox" checked=${title.on} onChange=${event => set({ on: event.target.checked })} />
          <span class="row-title">Show a title</span>
        </label>
        <p class="look-hint">It dissolves in all at once, stays, and dissolves out (${FADE_SECONDS} s each way).</p>
      </div>
      <fieldset class="look-section title-fields" disabled=${!title.on}>
        <label class="title-field">
          <span>Title</span>
          <input type="text" maxlength=${MAX_CHARACTERS} value=${title.text} placeholder="The song's name"
                 onInput=${event => set({ text: event.target.value })} />
        </label>
        <label class="title-field">
          <span>Lines below <small>one per line, up to ${MAX_LINES}</small></span>
          <textarea rows="5" value=${title.lines.join("\n")} placeholder=${"Anna Berg, piano\nBen Okafor, bass"}
                    onInput=${event => set({ lines: event.target.value.split("\n").slice(0, MAX_LINES)
                                                      .map(line => line.slice(0, MAX_CHARACTERS)) })}></textarea>
        </label>
        <div class="title-field">
          <span>Colour</span>
          <div class="title-colours">
            ${[...COLOURS, ...extraColour].map(([value, name]) => html`
              <button class=${"swatch" + (title.colour === value ? " selected" : "")} style=${{ background: value }}
                      title=${name} aria-label=${name} aria-pressed=${String(title.colour === value)}
                      onClick=${() => set({ colour: value })}></button>`)}
            <input type="color" value=${title.colour} aria-label="Any colour" title="Any colour"
                   onInput=${event => set({ colour: event.target.value })} />
          </div>
        </div>
        <div class="title-field">
          <span>Appears at</span>
          <div class="title-when">
            <output class="title-time">${formatSeconds(title.start_s)}</output>
            <button class="button small" onClick=${() => set({ start_s: clampStart(store.playheadSeconds, store.songSeconds) })}
                    title="Make it appear where the playhead is">At the playhead</button>
            <button class="button small" onClick=${() => set({ start_s: clampStart(title.start_s - 0.5, store.songSeconds) })}
                    aria-label="Half a second earlier">−0.5 s</button>
            <button class="button small" onClick=${() => set({ start_s: clampStart(title.start_s + 0.5, store.songSeconds) })}
                    aria-label="Half a second later">+0.5 s</button>
          </div>
        </div>
        ${titleOutsideVideo && html`<p class="look-hint problem">The title appears outside the video's start and end: move it with “At the playhead”.</p>`}
        <div class="strength-row title-duration">
          <span>Stays for</span>
          <input type="range" min=${MIN_SECONDS} max=${MAX_SECONDS} step="0.5" value=${title.duration_s}
                 onInput=${event => set({ duration_s: Number(event.target.value) })} aria-label="How long the title stays" />
          <output>${title.duration_s.toFixed(1)} s</output>
        </div>
        <div class="strength-actions">
          <button class="button small" onClick=${preview}>Play it from just before</button>
        </div>
      </fieldset>
    </section>`;
}

/** Where the rendered video starts and ends (store.inSeconds, store.outSeconds; keys I and O). */
function StartAndEnd({ store }) {
  return html`
    <div class="look-section trim-section">
      <span class="row-title">Start and end of the video</span>
      <p class="look-hint">Leave the clap or the count-in out of the video: it stays in the edit, darkened on the timeline.
        Keys <kbd>I</kbd> and <kbd>O</kbd> set them at the playhead; drag the brackets on the ruler to move them.</p>
      <div class="title-field">
        <span>Starts at</span>
        <div class="title-when">
          <output class="title-time">${formatSeconds(store.inSeconds)}</output>
          <button class="button small" onClick=${() => store.setInPoint(store.playheadSeconds)}
                  title="The video starts where the playhead is (I)">At the playhead</button>
          <button class="button small" disabled=${store.inSeconds <= 0.001} onClick=${() => store.setInPoint(0)}
                  title="The video starts where the song does">From the start</button>
        </div>
      </div>
      <div class="title-field">
        <span>Ends at</span>
        <div class="title-when">
          <output class="title-time">${formatSeconds(store.outSeconds)}</output>
          <button class="button small" onClick=${() => store.setOutPoint(store.playheadSeconds)}
                  title="The video ends where the playhead is (O)">At the playhead</button>
          <button class="button small" disabled=${store.outSeconds >= store.songSeconds - 0.001} onClick=${() => store.setOutPoint(store.songSeconds)}
                  title="The video runs to the end of the song">To the end</button>
        </div>
      </div>
      <p class="look-hint">The video is ${formatSeconds(store.videoSeconds)} long${store.isTrimmed ? ` (the song: ${formatSeconds(store.songSeconds)})` : ""}.</p>
    </div>`;
}
