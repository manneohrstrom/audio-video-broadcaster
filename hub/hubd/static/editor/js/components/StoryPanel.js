// Step 4, the Instagram story (user, 2026-11-24): a 9:16 slice of the finished video, with
// its look, from the story's own start to its end; no title sequence, no border and logo.
// The slice is placed per camera: drag on the video (VideoWindow) for the camera that's
// showing, or use the sliders here. The preview at the top is the story as it will look
// (VideoWindow draws it every frame into store.storyPreviewCanvas). "Make story" is in the
// header; the stories show on the hub page with the song's videos, ready to download.
// At each cut the two slices dissolve with the slice staying put (the default), or (chosen per
// cut under "Cuts") the slice glides to the next camera's place (story.js).
import { html } from "./html.js";
import { formatSeconds } from "../time-format.js";
import { CENTRE, INSTAGRAM_CLIP_SECONDS, cutsInStory, sliceLeftShare } from "../story.js";

const PREVIEW_WIDTH = 270;          // the canvas's pixels (9:16); shown smaller or larger by CSS
const PREVIEW_HEIGHT = 480;
const PLACES = [[0, "Left"], [CENTRE, "Middle"], [1, "Right"]];

export function StoryPanel({ store, transport }) {
  const playStory = () => { transport.seek(store.storyInSeconds); if (!store.isPlaying) transport.play(); };
  return html`
    <section class="look-panel story-panel" aria-label="Story">
      <div class="look-section story-top">
        <canvas class="story-preview" width=${PREVIEW_WIDTH} height=${PREVIEW_HEIGHT} aria-label="The story as it will look"
                ref=${canvas => { store.storyPreviewCanvas = canvas; }}></canvas>
        <div class="story-intro">
          <span class="row-title">Instagram story</span>
          <p class="look-hint">A 9:16 slice of the video, with its look, without the title sequence and the border.
            Drag on the video to move the slice: each camera keeps its own place. At a cut the two slices
            dissolve, or the slice glides across: choose under Cuts.</p>
          <p class="look-hint">The slice is a third of the frame's width, so the story is that part scaled up.</p>
          <div class="strength-actions">
            <button class="button small" onClick=${playStory}>Play the story</button>
          </div>
        </div>
      </div>
      <${Framing} store=${store} />
      <${StoryCuts} store=${store} transport=${transport} />
      <${StoryStartAndEnd} store=${store} />
    </section>`;
}

/** One row per camera: where the slice sits in its picture. */
function Framing({ store }) {
  const onAir = store.cameraAt(store.playheadSeconds);
  return html`
    <div class="look-section">
      <span class="row-title">Framing</span>
      <div class="story-framing">
        ${store.cameras.map(camera => {
          const position = store.storyFraming[camera.id] ?? CENTRE;
          return html`
            <div class=${"story-camera" + (camera === onAir ? " showing" : "")}
                 style=${{ "--camera-colour": `var(--color-camera-${(camera.index % 6) + 1})` }}>
              <span class="camera-key">${camera.number}</span>
              <span class="story-camera-name" title=${camera.id}>${camera.label}</span>
              <input type="range" min="0" max="1" step="0.005" value=${position}
                     aria-label=${`Where the slice sits in ${camera.label}'s picture`}
                     onInput=${event => store.setFraming(camera.id, Number(event.target.value))} />
              <span class="story-places">
                ${PLACES.map(([value, name]) => html`
                  <button class=${"button small" + (Math.abs(position - value) < 0.01 ? " selected" : "")}
                          aria-pressed=${String(Math.abs(position - value) < 0.01)}
                          onClick=${() => store.setFraming(camera.id, value)}>${name}</button>`)}
              </span>
            </div>`;
        })}
      </div>
    </div>`;
}

/** Where the story starts and ends (store.storyInSeconds, storyOutSeconds; keys I and O here). */
function StoryStartAndEnd({ store }) {
  const tooLong = store.storySeconds > INSTAGRAM_CLIP_SECONDS + 0.01;
  return html`
    <div class="look-section trim-section">
      <span class="row-title">Start and end of the story</span>
      <p class="look-hint">Keys <kbd>I</kbd> and <kbd>O</kbd> set them at the playhead here; drag the brackets on the ruler to move them.
        The video's own start and end stay as they are.</p>
      <div class="title-field">
        <span>Starts at</span>
        <div class="title-when">
          <output class="title-time">${formatSeconds(store.storyInSeconds)}</output>
          <button class="button small" onClick=${() => store.setStoryIn(store.playheadSeconds)}
                  title="The story starts where the playhead is (I)">At the playhead</button>
        </div>
      </div>
      <div class="title-field">
        <span>Ends at</span>
        <div class="title-when">
          <output class="title-time">${formatSeconds(store.storyOutSeconds)}</output>
          <button class="button small" onClick=${() => store.setStoryOut(store.playheadSeconds)}
                  title="The story ends where the playhead is (O)">At the playhead</button>
        </div>
      </div>
      <p class=${"look-hint" + (tooLong ? " problem" : "")}>The story is ${formatSeconds(store.storySeconds)} long.
        ${tooLong ? ` Instagram plays up to ${INSTAGRAM_CLIP_SECONDS} s in one story and splits a longer video into parts.` : ""}</p>
    </div>`;
}

/** The cuts inside the story, each with Glide or Dissolve (user, 2026-11-24). */
function StoryCuts({ store, transport }) {
  const cuts = cutsInStory(store.cuts, store.storyInSeconds, store.storyOutSeconds);
  const place = cameraId => sliceLeftShare(store.storyFraming[cameraId] ?? CENTRE);
  // Watch it: from a second before the cut.
  const watch = cut => { transport.seek(Math.max(0, cut.atSeconds - 1)); if (!store.isPlaying) transport.play(); };
  return html`
    <div class="look-section">
      <div class="section-header">
        <span class="row-title">Cuts</span>
        ${cuts.length > 1 && html`
          <span class="story-places">
            <button class="button small" onClick=${() => store.setStoryDissolveEverywhere(true)}>All dissolve</button>
            <button class="button small" onClick=${() => store.setStoryDissolveEverywhere(false)}>All glide</button>
          </span>`}
      </div>
      <p class="look-hint">Dissolve: the slice stays, and the next camera's slice fades in where it is.
        Glide: the slice moves to the next camera's place during the dissolve.</p>
      ${cuts.length === 0 && html`<p class="look-hint">No cuts between the story's start and end.</p>`}
      <div class="story-cuts">
        ${cuts.map(({ index, cut, from }) => {
          const fromCamera = store.camerasById.get(from.cameraId);
          const toCamera = store.camerasById.get(cut.cameraId);
          const samePlace = Math.abs(place(from.cameraId) - place(cut.cameraId)) < 0.001;
          return html`
            <div class="story-cut">
              <button class="text-button story-cut-time" onClick=${() => watch(cut)} title="Watch this cut">${formatSeconds(cut.atSeconds)}</button>
              <span class="story-cut-cameras" title=${samePlace ? "Both cameras' slices are at the same place: it's a plain dissolve either way" : ""}>
                ${fromCamera.number} → ${toCamera.number} ${toCamera.label}${samePlace ? html` <small>· same place</small>` : ""}</span>
              <span class="story-places" role="group" aria-label=${`At the cut at ${formatSeconds(cut.atSeconds)}`}>
                ${[[true, "Dissolve"], [false, "Glide"]].map(([on, name]) => html`
                  <button class=${"button small" + (!cut.storyGlide === on ? " selected" : "")}
                          aria-pressed=${String(!cut.storyGlide === on)}
                          onClick=${() => { if (!cut.storyGlide !== on) store.setStoryDissolve(index, on); }}>${name}</button>`)}
              </span>
            </div>`;
        })}
      </div>
    </div>`;
}
