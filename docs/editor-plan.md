# Editor plan: from takes to finished videos

Status: **accepted by the user (2026-11-16), with the recommended answers to all four decisions below.** Spikes E1–E3 run alongside Phase 4 Track B; the main work (M-E2 on) follows Track A. Sources: the user's direction of 2026-11-15 (no Premiere, our own editor; `docs/design.md`, "Editing"), the conversation of 2026-11-16 (web editor on the hub, bounce upload, automatic cut, changes by hand, render for YouTube), Phase 3 results (`docs/phase3-results.md`) and the ingest-time question (`docs/phone-plan.md`).

## Goal

Get from a take on `STUDIO` to a finished, synced video with no outside editing software:

1. Upload the Logic bounce, one or more per take (one per song is normal). It's synced to the take automatically (Phase 4 Track A).
2. The hub makes an **automatic cut** for each song: which camera is on, from when to when.
3. In the browser, the user **changes the cut** if they want: moves cut points, swaps cameras, sets colour per camera.
4. The hub **renders** the result: a YouTube master with the bounce as its soundtrack, and optionally (a maybe, see below) a vertical story version.

The first automatic cut uses simple rules. An agent that listens to the music, watches the video and cuts in a style the user asks for comes later. This plan only makes sure the data is ready for it.

## Where it runs

- **Hub (Pi 5):** stores and serves the media, runs the automatic cut, does the render. Like ingest, the render runs only when no take or live session is active, and pauses (SIGSTOP) on REC or LIVE.
- **Browser (on the Mac, or any computer on the LAN):** the editor UI. Playback and scrubbing are decoded in the browser, so the Pi 5's lack of a hardware encoder doesn't matter while editing.
- Plain HTML and JS served by the hub, no build step (as for the hub page).

## 1. Ingest: copy at real timing instead of re-encoding (changes a settled decision; needs the user's OK)

**Today.** Ingest re-encodes every camera onto an exact 30.000 fps grid (libx264, about real time per camera-hour on the Pi 5). That gave Premiere a clean constant-rate file. A big session (4 Pis, 5 phones, 2 hours) is about 18 hours of ingest.

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
  "colour": {"cam1": {"exposure": 0.0, "temperature": 0, "tint": 0, "contrast": 1.0, "saturation": 1.0}},
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

## 4. Timeline UI

A page per song, reached from the take in the hub's take list:

- **Viewer** showing the program output (the active camera, with colour), plus a strip of small live thumbnails, one per camera, at the playhead.
- **Timeline:** the bounce waveform with beat and bar marks, one lane per camera with rough sections greyed out and gaps in recording shown, and a program lane showing the cut.
- **Editing:** click a camera at a point in time to cut to it there; drag cut points (they snap to beats, with a modifier key for free movement); delete a cut; undo and redo. "Re-run automatic cut" for the whole song or a selected range.
- **Play and scrub** with the bounce as audio. Playback must be frame-accurate against the bounce, so it uses WebCodecs (decoding frames by timestamp) rather than plain `<video>` seeking, which is imprecise, especially on files with variable frame timing. Spike E2 checks this works with four or more cameras at once.
- Media is served from `STUDIO`, mounted read-only for playback and unmounted 5 minutes after the last playback (the existing playback rule).

## 5. Colour

- **Per camera, per song or per take** (copied across songs by default): exposure, white balance (temperature and tint), contrast, saturation. Optionally a `.cube` LUT for a look.
- **The preview must match the render.** The parameters are turned into one 3D LUT per camera. The browser applies it in WebGL, and the render applies the same LUT with ffmpeg `lut3d`. One source of truth, so what you see is what you get.
- **Phase 4 Track B** does the camera-side matching (gain, AWB, grey card) at recording time. This is the correction afterwards, for what Track B can't fix.
- **Not in v1:** masks, keyframed colour, transitions other than hard cuts, titles, split screen and picture-in-picture (future ideas in `docs/design.md`).

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
| **M-E3** | Timeline UI: viewer, lanes, cutting, play and scrub, save. |
| **M-E4** | Colour: parameters, LUTs, preview in WebGL, applied in the render. |
| **M-EX** | Exit checks; `docs/editor-results.md`. |

**Depends on Phase 4 Track A** (synced bounces and song segments) for everything from M-E2 on. M-E1 and the spikes don't, and can run alongside Track B.

## Exit criteria

1. A real session's song goes from bounce upload to a rendered YouTube file with no step outside the hub page.
2. The render stays in sync with the bounce within one frame from the start to the end of the song (checked with claps, as in Track A's acceptance test).
3. The rendered colour matches the browser preview (side-by-side stills, no visible difference).
4. Ingest of a real session with `-c copy` keeps every frame's time and passes verify (if section 1 is accepted).
5. REC or LIVE during a render pauses it at once without affecting the take, and the render then resumes.

## Not in this plan

The agent (listening to the music, analysing video, cutting in a style the user asks for); the vertical story version (section 7, a maybe); transitions, titles, split screen, picture-in-picture; keyframed or masked colour; rendering on the Mac; editing from outside the LAN.

## Decisions for the user

1. **Ingest switch to `-c copy` with real timing: answered (2026-11-16).** Yes, once spike E1 passes. Until then the conform stays as built. `CLAUDE.md` updated.
2. **Order: answered (2026-11-16).** Spikes E1–E3 alongside Track B; the editor's main work (M-E2 on) after Track A, which it depends on. Phase 4's B, A, C order stays.
3. **Colour in v1: answered (2026-11-16).** The basic controls in section 5 (plus an optional `.cube` look).
4. **Vertical story version: a maybe (user, 2026-11-16).** Decide later, after the YouTube render works.
