// Step 3, the title (docs/editor-plan.md section 5b): first where the video starts and ends
// (user, 2026-11-21: the clap and the count-in stay in the edit but not in the video), then
// the title sequence that opens the video (user, 2026-11-21): "Brockley Live" from black,
// the song's title in its colour, then the video with the names on a strip along the bottom
// for "Names stay for" seconds. Drawn in the preview by title/title-layout.js and in the
// video by hub/ingest/title_render.py.
import { html } from "./html.js";
import { formatSeconds } from "../time-format.js";
import { MAX_CHARACTERS, MAX_LINES, CREAM, DETAILS_IN, VIDEO_IN, BRAND_AFTER_SEQUENCE_SECONDS, LOGO_WITHOUT_TITLE_SECONDS } from "../title/title-layout.js";

const COLOURS = [["#ffffff", "White"], [CREAM, "Cream"], ["#e8b15a", "Amber"], ["#c0182a", "Red"]];
const MIN_SECONDS = 3;
const MAX_SECONDS = 20;            // the slider; the hub accepts 1-60

export function TitlePanel({ store, transport }) {
  const title = store.title;
  const set = changes => store.setTitle(changes);
  // Watch it from the top: the video's start.
  const preview = () => { transport.seek(store.inSeconds); if (!store.isPlaying) transport.play(); };
  const extraColour = store.branding && !COLOURS.some(([value]) => value === store.brandingColour)
    ? [[store.brandingColour, "The border's colour"]] : [];

  return html`
    <section class="look-panel title-panel" aria-label="Title">
      <${StartAndEnd} store=${store} />
      <div class="look-section">
        <label class="title-switch">
          <input type="checkbox" checked=${title.on} onChange=${event => set({ on: event.target.checked })} />
          <span class="row-title">Open with the title sequence</span>
        </label>
        <p class="look-hint">From black: Brockley Live, then the title, then the video fades in at ${VIDEO_IN[0]} s
          with the names on a strip along the bottom. The music plays underneath from the video's start.</p>
      </div>
      <fieldset class="look-section title-fields" disabled=${!title.on}>
        <label class="title-field">
          <span>Title <small>shown in capitals</small></span>
          <input type="text" maxlength=${MAX_CHARACTERS} value=${title.text} placeholder="The song's name"
                 onInput=${event => set({ text: event.target.value })} />
        </label>
        <div class="title-field">
          <span>Title colour</span>
          <div class="title-colours">
            ${[...COLOURS, ...extraColour].map(([value, name]) => html`
              <button class=${"swatch" + (title.colour === value ? " selected" : "")} style=${{ background: value }}
                      title=${name} aria-label=${name} aria-pressed=${String(title.colour === value)}
                      onClick=${() => set({ colour: value })}></button>`)}
            <input type="color" value=${title.colour} aria-label="Any colour" title="Any colour"
                   onInput=${event => set({ colour: event.target.value })} />
          </div>
        </div>
        <label class="title-field">
          <span>Names on the strip <small>one per line, up to ${MAX_LINES}</small></span>
          <textarea rows="4" value=${title.lines.join("\n")} placeholder=${"Anna Berg, piano\nBen Okafor, bass"}
                    onInput=${event => set({ lines: event.target.value.split("\n").slice(0, MAX_LINES)
                                                      .map(line => line.slice(0, MAX_CHARACTERS)) })}></textarea>
        </label>
        <div class="strength-row title-duration">
          <span>Names stay for</span>
          <input type="range" min=${MIN_SECONDS} max=${MAX_SECONDS} step="0.5" value=${title.duration_s}
                 onInput=${event => set({ duration_s: Number(event.target.value) })} aria-label="How long the names stay" />
          <output>${title.duration_s.toFixed(1)} s</output>
        </div>
        <p class="look-hint">The strip comes in at ${DETAILS_IN} s into the video.</p>
        <div class="strength-actions">
          <button class="button small" onClick=${preview}>Play the opening</button>
        </div>
      </fieldset>
      <${BorderAndLogo} store=${store} />
    </section>`;
}

/** The border and the Brockley Live logo on top of everything (user, 2026-11-21: here, not in the footer). */
function BorderAndLogo({ store }) {
  const when = store.title.on
    ? `Both fade in ${BRAND_AFTER_SEQUENCE_SECONDS} s after the names strip is gone.`
    : `The border is on from the start; the logo fades in ${LOGO_WITHOUT_TITLE_SECONDS} s into the video.`;
  return html`
    <div class="look-section">
      <label class="title-switch" title="The border and the Brockley Live logo, on top of everything">
        <input type="checkbox" checked=${store.branding} onChange=${event => store.setBranding(event.target.checked)} />
        <span class="row-title">Border & logo</span>
      </label>
      <p class="look-hint">${when}</p>
      <div class="title-field">
        <span>Colour</span>
        <div class="title-colours">
          <input type="color" value=${store.brandingColour} disabled=${!store.branding}
                 aria-label="Colour of the border and logo" title="Colour of the border and logo"
                 onInput=${event => store.setBrandingColour(event.target.value)} />
          ${store.branding && store.brandingColour !== "#ffffff" && html`
            <button class="text-button" onClick=${() => store.setBrandingColour("#ffffff")}>back to white</button>`}
        </div>
      </div>
    </div>`;
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
