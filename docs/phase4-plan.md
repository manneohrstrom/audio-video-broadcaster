# Phase 4 plan: sound, picture and studio polish

Status: **draft, waiting for the user's OK.** Nothing is built until then. **Priority (user, 2026-11-15): the visuals (Track B) come first.** Written 2026-11-15 after Phase 3's GO (`docs/phase3-results.md`). Sources: the Phase 4 list in `docs/design.md`, the user's requirements of 2026-11-14 and 2026-11-15 (`docs/progress.md`).

## Goal

Three things the rig can't do yet:
1. **Sound:** line the DAW bounce up with the video automatically, with no clap marked by hand, including sessions spread over several rooms.
2. **Picture:** set up and match all cameras from one place, for changing light, without touching each camera.
3. **Studio polish:** a big recording light, and the small loose ends from Phases 2–3.

## Track A: audio sync (second; the hub's USB mic arrives 2026-11-16 and spike A1 can run whenever convenient)

**What it does.** During every take the hub records a scratch WAV from its USB mic, stamped against its own clock (the clock the cameras' frames are stamped against). After the session the user uploads the bounce, one per song if they like. The hub finds where each bounce sits in the take, and how fast its clock runs, and stamps it with the take's timecode, so it drops onto the Premiere timeline already in sync.

- **Anchors, per take or per bounce** (multi-room requirement, `docs/design.md`):
  1. **Hub mic match:** the scratch WAV against the bounce (GCC-PHAT at several points through the take, giving offset and drift).
  2. **Claps:** a clap in the bounce, through the instrument mics, that's visible on any Pi camera, found as in `hub/tools/clap_check.py`. This works when the hub mic hears nothing, for example when the players are in other rooms.
  3. Otherwise **"needs sync"**: never a silent guess.

  The hub says which anchor it used and how confident it is. When both work, they must agree within a frame, or the take is flagged.
- **Pi cameras in any room** already share the timecode. Phones and handhelds need their own anchor; that's the "add media" item, Track A3.
- **Drift:** the DAW interface's clock may be 30–60 ppm off (Phase 3). With matches at several points, the hub measures it and resamples or time-stretches the bounce to the take's clock, so a 1-hour song stays in sync.
- **Where files go:** `STUDIO/takes/<take>/audio/` holds `scratch.wav`, the uploaded bounce, and `bounce_synced.wav` with timecode (BWF). Written via `.part` and renamed, like the video. The hub still never deletes from the drive.
- **Upload:** an "Add audio" button on each take in the hub page. Resumable chunked upload, streamed to disk (1 GB of RAM). It pauses during takes and live sessions, like ingest.

### Spikes (before building)

| | Spike | Done when |
|---|---|---|
| **A1** | **Offline match.** The user records one real take with the hub mic recording (a plain `arecord` on the hub) and claps every ~5 min, then bounces as usual. Claude matches scratch against bounce offline. | The match agrees with every clap within 1 frame, or we know why not. Gives the confidence measure and the drift. |
| **A2** | **Mic clock against hub clock.** The USB mic's own sample clock drifts against the hub's clock. Measure it over 30 min (ALSA timestamps against `CLOCK_REALTIME`), and decide whether the scratch WAV needs a per-block timestamp track. | Drift known; a timestamping method chosen. |
| **A3** | **Clap-only anchor**, from Phase 3's data: find the clap in `claptest.mp3` and on one camera, with no hub mic. | Same result as `clap_check.py`, automatically. |

### Exit criteria

1. A ≥30-min take with the hub mic, bounced as 2–3 partial songs: each song is placed and stamped automatically, within 1 frame of the periodic claps at start, middle and end.
2. A take where the hub mic hears nothing usable (players in another room): synced from claps alone, or marked "needs sync". Never wrong.
3. A deliberately edited bounce (a section moved) is flagged, not synced.

## Track B: picture (first)

**B1. Snapshot overview page.** A still from every camera side by side, for placing, framing, focusing and matching, with each camera's status. Idle cameras: a short `rpicam-still` with the locked settings. Live: a frame from the stream. Never during a take, and never stored.

**B2. One set of image controls for all cameras.**
- Global brightness (gain), white balance as a colour temperature plus named presets, optional contrast and saturation, and saved "looks".
- Per-camera offsets from a **grey-card calibration** (one session, stored per camera), so the angles stay matched.
- Settings apply at the next REC: offline takes restart `rpicam-vid` per take, so this is free. In live mode a change restarts the stream (spike B-S1 measures the blip).
- The shutter stays at 1/50 s (flicker-free under 50 Hz light).

**B3. "Auto, then lock" at REC (option).** On REC, every camera meters for about 1 s. The hub picks one exposure and one white balance from all cameras (median, plus each camera's grey-card offset) and locks them for the take. Each take suits the current light, with no drift or mismatch between angles mid-take.

**B4. Focus help.** On the snapshot page: a zoomed crop per camera and a sharpness number (higher is sharper), for setting manual focus.

**B5. Autofocus (needs the user's OK; see decision 1).** Continuous PDAF on the Camera Module 3, per camera, switchable per take, limited in range and speed. Test first on one camera with someone walking up. Note: the wide lens is sharp from about 1 m at 1080p, so AF may rarely be needed, and it can hunt or breathe mid-take. It doesn't affect frame sync.

### Spikes

| | Spike | Done when |
|---|---|---|
| **B-S1** | Live mode: how long the stream blips when a setting changes (MediaMTX `rpiCamera*` parameters). | Measured. |
| **B-S2** | Auto-then-lock: metering time, and how far apart the cameras' auto values land in the same light. | The hub's pick looks matched on the snapshot page. |
| **B-S3** | `rpicam-still` on a Zero 2 W while idle: time and memory per snapshot. | Under about 3 s, no effect on camnode. |
| **B-S4** | Autofocus (only if approved): continuous AF with someone approaching; hunting and breathing. | User verdict on a short test take. |

### Exit criteria

1. From the snapshot page alone, the user sets up and matches all four cameras for a new light, with no SSH.
2. A grey card filmed by all four cameras after calibration: white balance and brightness match within a set tolerance (to be fixed in B-S2).
3. Auto-then-lock takes keep the same exposure and white balance for their whole length, on every camera.

## Track C: studio polish

- **C1. Recording light (smart bulb).** hubd sets the bulb from the rig state on every 2 s poll. Draft colours: dim green ready, blue live, amber arming, red recording, cyan ingest running, purple ingest done (safe to eject), flashing red error. It never blocks REC or STOP. Config holds the type and address. When it arrives: check its firmware (Tasmota or ESPHome) and set a DHCP reservation. Keep the lamp out of shot or dim, because white balance is fixed.
- **C2. Small items from Phases 2–3:**
  - Wi-Fi shown red below −65 dBm, with the link rate, on the camera cards.
  - Reword the deploy guard's "state idle" message when cameras are live.
  - Bump camnode's version number (the LED and lint changes still say 0.1.0).
  - Runbook (`docs/runbook.md`): mounting checklist, Wi-Fi −60 dBm per spot, OBS "Close file when inactive", phone local-network permission, stick repair (First Aid, eject).
  - Pin versions in `docs/versions.md`.
- **C3. From the original Phase 4 list, keep or drop (decision 4):** tally LEDs on the cameras (the board LED already does this), a physical REC button on the hub's GPIO, OBS scene-collection export, LTC timecode (only worth it if Track A disappoints).

## Not in Phase 4 (future ideas, unless the user moves them in)

- Automatic edit on the hub (needs Track A), per `docs/design.md`.
- Pixel 3 phones as offline camera units.
- Network share and Mac auto-pull (`docs/phase3-plan.md`, decision 6).
- "Discard take" button (parked 2026-11-15).
- Copying gapless takes at the native 30.0107 fps without re-encoding (once an editor is chosen; `docs/phase3-results.md`).
- Live latency tuning (deferred 2026-11-14).

## Milestones

| | Milestone |
|---|---|
| B-S1–S3 | Picture spikes (no user time needed, apart from looking at results). |
| **M-B** | Snapshot page and focus help; global controls and looks; grey-card calibration; auto-then-lock. Autofocus only if approved. |
| A1–A3 | Audio spikes. A1 needs one real session from the user; it can run any time, in parallel with M-B. |
| **M-A** | Scratch recording during takes; bounce upload; matching and anchors; synced BWF on the drive; UI per take ("synced, confidence high, anchor: hub mic"). Tests. |
| **M-C** | Recording light (when the bulb is set up); small items; runbook. |
| **M-X** | Exit checks per track; `docs/phase4-results.md`. |

Order: **B (picture) first** (user priority), then A, then C. C1 whenever the bulb is ready.

## Decisions for the user

1. **Autofocus** conflicts with the settled rule "manual focus per camera; no auto focus during takes". Allow continuous AF as a per-camera, per-take option (off by default), after a test (B-S4)? Or keep manual focus and rely on focus help (B4)? *Recommended:* run the test first, then decide.
2. **Auto-then-lock** changes the settled "fixed gain, fixed AWB gains" to "fixed for each take, chosen at REC". Exposure and white balance still never change during a take. OK? *Recommended:* yes, as an option next to manual.
3. **Order: answered.** Visuals first (user, 2026-11-15): B, then A, then C.
4. **C3:** keep or drop tally LEDs, the GPIO REC button, OBS export and LTC? *Recommended:* drop the tally LEDs (the board LED and the bulb cover it) and LTC (unless Track A disappoints); the GPIO button and OBS export are each small, so keep them only if you'd use them.
5. **Bounce upload from the page** writes to the drive (`audio/` in the take folder): the drive is mounted read-write for the upload, as it is for ingest. OK?
