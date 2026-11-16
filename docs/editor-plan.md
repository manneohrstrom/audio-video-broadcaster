# Editor plan: from takes to finished videos

Status: **accepted by the user (2026-11-16), with the recommended answers to all four decisions below. Sections 4 and 5 (the UI) revised from the user's requirements the same day; a clickable mock-up first, then building M-E3 against the S1 take (user OK to start now).** Spikes E1–E3 run alongside Phase 4 Track B; the main work (M-E2 on) follows Track A. Sources: the user's direction of 2026-11-15 (our own editor; `docs/design.md`, "Editing"), the conversation of 2026-11-16 (web editor on the hub, bounce upload, automatic cut, changes by hand, render for YouTube), Phase 3 results (`docs/phase3-results.md`) and the ingest-time question (`docs/phone-plan.md`).

## Goal

Get from a take on `STUDIO` to a finished, synced video with no outside editing software:

1. Upload the Logic bounce, one or more per take (one per song is normal). It's synced to the take automatically (Phase 4 Track A).
2. The hub makes an **automatic cut** for each song: which camera is on, from when to when.
3. In the browser, the user **changes the cut** if they want: chooses which camera is on at each moment (step 1), then picks a look for the whole video (step 2).
4. The hub **renders** the result: a YouTube master with the bounce as its soundtrack, and optionally (a maybe, see below) a vertical story version.

The first automatic cut uses simple rules. An agent that listens to the music, watches the video and cuts in a style the user asks for comes later. This plan only makes sure the data is ready for it.

## Where it runs

- **Hub (Pi 5):** stores and serves the media, runs the automatic cut, does the render. Like ingest, the render runs only when no take or live session is active, and pauses (SIGSTOP) on REC or LIVE.
- **Browser (on the Mac, or any computer on the LAN):** the editor UI. Playback and scrubbing are decoded in the browser, so the Pi 5's lack of a hardware encoder doesn't matter while editing.
- Served by the hub, no build step. The editor uses Preact with `htm` (user decision 2026-11-16), copied into the repo and pinned. It has its own design system (CSS tokens plus our own components: buttons, sliders, filter tiles, strips, tabs) and type checking with JSDoc and `tsc --checkJs` on the Mac. The hub's other pages stay plain JS.

## 1. Ingest: copy at real timing instead of re-encoding (changes a settled decision; needs the user's OK)

**Today.** Ingest re-encodes every camera onto an exact 30.000 fps grid (libx264, about real time per camera-hour on the Pi 5). That gave editing software a clean constant-rate file. A big session (4 Pis, 5 phones, 2 hours) is about 18 hours of ingest.

**Proposed.** Ingest rewraps with `-c copy`, and every frame keeps its real capture time (from `--save-pts` and `first_frame_wallclock_ns`; for phones, the page's hub-clock frame times). A dropped frame becomes one long frame. Timecode, verify against the manifest (gaps included), `take.json` and rough sections stay as they are. Ingest drops from hours to minutes per camera-hour, mostly the pull.

**Why sync still holds.** Sync comes from timestamps, not from the file's frame rate. The render (section 6) builds the exact 30.000 fps output timeline locked to the bounce and, for each output frame, takes the source frame nearest in real time. That's the same logic as today's conform. It runs once on the final cut instead of on every camera-hour, and footage gets one encode instead of two. The rule "never stamp frames as exactly 30.000 fps without fixing the timing" is still met.

**Already-conformed takes** stay as they are. A constant 30 fps file is just a special case of "every frame has a time", so the editor reads both.

**Resolves** the ingest-time question in `docs/phone-plan.md`. On acceptance, the "Ingest" item in `CLAUDE.md` and `docs/design.md` gets updated.

## 2. The edit as data

One JSON file per song, the edit. It's what the automatic cut writes, what the timeline UI changes, what the render reads, and later what the agent proposes. Times are take time in nanoseconds on the hub clock, the same clock as `first_frame_wallclock_ns`, so nothing depends on frame rates.

```json
{
  "version": 1,
  "take": "20261115-231111",
  "song": "Track 2",
  "bounce": "audio/track-2/v3/synced.wav",
  "range_ns": [start, end],
  "cuts": [
    {"at_ns": 0, "camera": "cam1"},
    {"at_ns": 5400000000, "camera": "cam3"}
  ],
  "look": {"colour": "warm-film", "colour_strength": 0.6, "overlay": "grain", "overlay_strength": 0.25},
  "outputs": ["youtube"],
  "made_by": "auto-v1",
  "notes": []
}
```

- A cut holds until the next one. The first cut is at the song's start.
- `made_by` records whether a cut came from the rules, the user, or later the agent, so the agent can learn what the user changed.
- **Where it lives:** the working copy is on the hub's SD card and is saved as you edit (so the drive doesn't need to be mounted read-write while you work). It's written to the take's `edits/` folder on `STUDIO` on save and on every render, via `.part` and rename. The hub never deletes from the drive; older versions are kept as `edit.v<N>.json`.

## 3. Automatic cut, version 1 (rules)

Per synced song segment (from Track A), using the rules already in `docs/design.md` as the starting point:

- Tempo and beats come from the bounce (onset detection, in Python on the hub). Cuts go on beats, preferably on bar lines.
- Hold each shot 4–10 s. Come back to a wide camera regularly (which one is wide is set per take on the hub page).
- Prefer cameras with movement (frame-difference energy per camera, computed at low resolution when the cut is made).
- **Never cut to a camera inside a rough section** (from `take.json`), or outside the time that camera recorded.
- Deterministic: the same inputs give the same cut, so a re-run doesn't surprise the user.

It runs right after a bounce is synced, so the cut is ready the first time the user opens the song.

## 4. Editor UI: step 1, the cut

*Revised with the user, 2026-11-16 (requirements below are theirs; replaces the first draft of this section). Needs the user's OK before building.*

**Who and where.** The main editor is not technical. The interface has to be simple and quick, in the spirit of the Instagram video editor or early iMovie (visual filmstrip timelines). It runs on a Mac mini with two monitors, so there's plenty of room.

**The model: choose, never arrange.** The take is fixed. The only editing decision is *which camera is showing from this moment on*. Nothing is moved, trimmed, duplicated or reordered, and there's no non-linear editing. A cut holds until the next one (the edit format in section 2 already works this way).

**Layout (one screen):**
- **Video window:** the program output (the chosen camera) at the playhead. With the look from step 2 applied, so it looks like the result.
- **Program strip** across the top: the cut as made so far, as coloured blocks per camera, with filmstrip thumbnails.
- **One strip per camera** below it, always all of them, laid out horizontally over the song's whole length: filmstrip thumbnails, so you see at a glance what every camera is doing over time. Rough sections and times a camera didn't record are greyed out.
- **Audio waveform** of the bounce under the strips, to see where the parts of the song are.
- **Hover:** with the mouse over any strip, that camera's frame at that exact time shows larger (a tooltip-sized preview), and a hover line runs across every strip so you can compare all cameras at that moment.

**Interactions:**
- **Scrub:** click or drag anywhere on the timeline to move the playhead; very fast and responsive. Space plays and pauses with the bounce as audio.
- **Choose a camera:** click a camera's strip at a time to show that camera from there on (until the next cut). Keys 1–6 do the same at the playhead.
- Drag a cut's edge in the program strip to move it; delete a cut (the previous camera continues). Undo and redo.
- **No snapping** to beats or bars (user: it would be annoying; can be added later if missed). Cut points land where you click.
- Zoom in and out of the timeline (whole song down to a few seconds).
- The automatic cut (section 3) fills the program strip the first time a song is opened, as a starting point; the user overrides it freely.
- Saved automatically as you go.

**No transitions to choose from.** Every camera change is a short, subtle dissolve (user, 2026-11-16; length to settle in the mock-up, starting at about 0.3 s). The render needs two decoders running during each dissolve: check memory on the hub (E3 measured ~88 MB per decoder at 2 threads, 180–290 MB free).

**Screens:** must work on one screen, including a laptop (the editor may work on one). **Stretch goal:** "Pop out video" opens the video in its own window for the second monitor, with the strips on the first (user, 2026-11-16).

**Under the hood:**
- The camera strips need small thumbnails along the whole take. The hub makes them once per take (for example one 160-pixel frame per second per camera, packed into sprite sheets) as a step after ingest, and keeps them on its SD card. Hover previews and strips use them; playback and the video window use WebCodecs on the real files (spike E2), for exact frames in sync with the bounce.
- Media is served from `STUDIO`, mounted read-only for playback and unmounted 5 minutes after the last playback (the existing playback rule).

**Mock-up feedback (user, 2026-11-16), changes the above:**
- **Choosing a camera is press, drag, release** on a camera's strip: where you press is the start, you drag left or right, and where you release is the end. That camera is shown for exactly that stretch; before and after it, the cut stays as it was (the camera that was on before comes back at the end). This replaces "click = this camera from here until the next cut". The user found the click model hard to judge; the mock-up gets drag-select next, before more usability judgement.
- **A splitter between the video (top) and the timeline (bottom)**, dragged up and down, in both steps; the video window is sizeable in both.
- **Zoom is a horizontal slider**, not − / + buttons.
- The rest of the layout: "really great".

## 5. Editor UI: step 2, the look

*Revised with the user, 2026-11-16: replaces "colour per camera". The look is global, for the whole video, not per camera.*

A second step (a tab, after the cut is done): **what's the vibe of this video?** It works like Instagram, with Instagram's words: **Filters** (tap through, see the result at once; tap the chosen one again for its strength) and **Effects**. A first set is proposed in the mock-up for the user to trim. **No tabs between Filters and Effects** (user, 2026-11-16, mock-up feedback): one horizontally scrolling row of filters, and below it one horizontally scrolling row of effects, both visible at once.

- **Two separate choices:**
  - **Colour look:** a preset grade (a LUT), e.g. warm film, faded, cool night, punchy, black-and-white.
  - **Overlay:** a texture or effect on top, e.g. analogue film grain, light leaks, vignette, VHS, soft glow.
- **Strength slider for each** (0–100 %, e.g. 25 % grain), blending the effect with the image.
- **One look per song** (user, 2026-11-16): one video per bounce, one bounce per song, so one look per bounce, not per session. Effects can be stacked (e.g. grain and vignette), each with its own strength.
- **Camera matching stays automatic and invisible:** each device's correction from its calibration card readings (Phase 4 Track B, B4: the phones' tint and colour rendering against the Pis') is applied before the look. The user never sees per-camera controls.
- **The preview must match the render:** the look and overlays run in WebGL in the browser and the same maths in the hub's render (ffmpeg `lut3d` for colour; overlays generated the same way on both sides, e.g. grain from a fixed seed). Per camera, the match correction and the look combine into one LUT, so colour costs what spike E3 measured (0.53× real time). Overlays add render cost; to measure.
- **Not in v1:** per-camera colour controls, keyframed or changing looks, masks, titles, split screen, picture-in-picture.

## 6. Render

- **Output timeline:** exact 30.000 fps, starting at the song's start, with the synced bounce as the only audio. The bounce is already aligned and drift-corrected to the take's clock by Track A.
- **Picking frames:** for each output frame at time *t*, take the active camera's frame with the nearest real time. A missing frame repeats the previous one; the automatic cut avoids rough sections, so this is rare in practice. This is today's conform logic (ffmpeg `fps=30` on the time-of-day grid, `docs/progress.md` 2026-11-15) applied per segment.
- **Pipeline:** one ffmpeg process per output. Each cut segment is trimmed from its camera by timestamp, put through that camera's LUT, joined (concat filter, hard cuts), and encoded once. Only the active camera is decoded at any moment.
- **YouTube master:** 1920×1080, 30 fps, H.264 (libx264, preset and CRF tuned in spike E3, starting at the ingest's veryfast CRF 16), audio AAC 320 kbps from the bounce WAV, `.mp4` with faststart. Written to `edits/<song>.mp4` on `STUDIO` via `.part` and rename.
- **Cost:** about real time per output on the Pi 5, more with colour filters (spike E3 measures it). A 4-minute song is about 5 minutes; a 1-hour session is about an hour per output. Fine overnight, like ingest.
- **Progress** on the hub page, as with ingest: progress bar, ETA, pauses for REC and LIVE.
- **Check after rendering:** duration matches the song; frame count is exactly 30 × duration; one spot-check per segment that the rendered frame matches the expected source frame. Same spirit as ingest's verify.

## 7. Vertical story version (a maybe, not decided)

A 9:16 version for Instagram stories, rendered from the same edit, is **a possible addition, not part of v1.** The user will decide later whether it's worth doing.

What's known so far:
- A vertical crop of a 1080p frame is only about 608×1080. A 1080×1920 story then needs an upscale of almost 1.8×, which looks soft.
- Options if it goes ahead: accept the softness; mount one or two phones vertically for story angles (the phone page would need to record portrait); or reframe per shot (a crop position per cut in the edit, which costs UI work).
- The edit format leaves room for it (`outputs`, and a crop per cut could be added later) so it can be added without redoing anything.

## Spikes (before building)

| | Spike | Done when |
|---|---|---|
| **E1** | **Copy at real timing.** Rewrap one existing Pi take and one phone take with `-c copy`, keeping real frame times (mkvmerge timestamps, as ingest already does, then into `.mov` with timecode). Check frame count and per-frame times against the manifest, gaps included, and the timestamp precision the container keeps. Uses existing takes (no new session). | Every frame's time within 1 ms of the manifest; file plays in Chrome and Safari; ffmpeg reads the times back. |
| **E2** | **Browser playback.** A throwaway page that decodes 4 cameras (then 5, with a phone) with WebCodecs, in sync with a WAV, with seeking, on the user's Mac. | Smooth playback and frame-accurate seeking with all cameras, or a known limit (then: lower-resolution thumbnails, or proxies). |
| **E3** | **Render cost.** Render a 4-minute multicam cut with LUTs from existing takes on the hub, at a few presets. | Seconds per output second known; hub temperature logged; a preset chosen. |
| **E4** | **Auto-cut rules** on an existing take with a bounce (after Track A's A1 session, or with Phase 3's clap-test data). | A cut the user finds watchable as a starting point. |

## Milestones

| | Milestone |
|---|---|
| E1–E3 | Spikes; no user time needed apart from looking at E2 on the Mac. |
| **M-E1** | Ingest switched to `-c copy` with real timing (if the user OKs section 1). Tests; `CLAUDE.md` and `docs/design.md` updated. |
| **M-E2** | Edit format; automatic cut v1; render (YouTube master); render progress on the hub page. A finished video with no UI work yet. |
| **M-E3** | Step 1, the cut (section 4): thumbnails after ingest, video window, camera strips, waveform, hover, scrub, choose a camera, save. Can be built now against the S1 take (synced by hand), ahead of Track A. |
| **M-E4** | Step 2, the look (section 5): colour presets and overlays with strength, preview in WebGL, the same in the render, automatic camera match from the card readings. |
| **M-EX** | Exit checks; `docs/editor-results.md`. |

**Depends on Phase 4 Track A** (synced bounces and song segments) for everything from M-E2 on. M-E1 and the spikes don't, and can run alongside Track B.

## Exit criteria

1. A real session's song goes from bounce upload to a rendered YouTube file with no step outside the hub page.
2. The render stays in sync with the bounce within one frame from the start to the end of the song (checked with claps, as in Track A's acceptance test).
3. The rendered look matches the browser preview (side-by-side stills, no visible difference).
4. Ingest of a real session with `-c copy` keeps every frame's time and passes verify (if section 1 is accepted).
5. REC or LIVE during a render pauses it at once without affecting the take, and the render then resumes.

## Not in this plan

The agent (listening to the music, analysing video, cutting in a style the user asks for); the vertical story version (section 7, a maybe); transitions, titles, split screen, picture-in-picture; keyframed or masked colour; rendering on the Mac; editing from outside the LAN.

## Decisions for the user

1. **Ingest switch to `-c copy` with real timing: answered (2026-11-16).** Yes, once spike E1 passes. Until then the conform stays as built. `CLAUDE.md` updated.
2. **Order: answered (2026-11-16).** Spikes E1–E3 alongside Track B; the editor's main work (M-E2 on) after Track A, which it depends on. Phase 4's B, A, C order stays.
3. **Colour in v1: answered (2026-11-16).** The basic controls in section 5 (plus an optional `.cube` look).
4. **Vertical story version: a maybe (user, 2026-11-16).** Decide later, after the YouTube render works.
