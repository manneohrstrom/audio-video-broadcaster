# Phase 4 plan: sound, picture and studio polish

Status: **accepted by the user (2026-11-15). Work starts 2026-11-16 with Track B (visuals).** Decisions below are all answered. Written 2026-11-15 after Phase 3's GO (`docs/phase3-results.md`). Sources: the Phase 4 list in `docs/design.md`, the user's requirements of 2026-11-14 and 2026-11-15 (`docs/progress.md`).

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
- **Songs and versions (user requirement, 2026-11-15).** A take can have several songs (track 1, 2, 3, …), and each song several bounce versions (different mixes, levels, tweaks).
  - Each upload goes to a song of that take, either a new song (named by the user, default "Track N") or an existing one. Uploading to an existing song adds the next version (v1, v2, …). Every version is kept; the newest is the default, and the user can pick another.
  - Each version is synced on its own. Versions of the same song should land in the same place in the take: if a new version lands elsewhere, it was probably edited in time, and the hub flags it rather than trusting it.
  - The later auto-edit uses the chosen version of each song.
- **Where files go:** `STUDIO/takes/<take>/audio/scratch.wav`, and per song `audio/<song>/v<N>/` with the uploaded file as sent plus `synced.wav` with timecode (BWF) and a small `sync.json` (offset, drift, anchor, confidence). Written via `.part` and renamed, like the video. The hub never deletes from the drive.
- **Upload:** "Add audio" on each take in the hub page: pick or create the song, then the file. Resumable chunked upload (8 MB pieces). It pauses during takes and live sessions, like ingest. *Built 2026-11-16 (hubd 0.9.0, ingest 0.5.0) with one change: uploads are staged on the hub's SD card and hub-ingest copies them to the drive like video (`.part`, fsync, checksum, rename), instead of streaming straight to the drive. So uploading works without the drive plugged in, and hub-ingest stays the only thing that mounts it.*
- **Scratch recording (built 2026-11-16, before the mic arrived):** `arecord` from the first USB mic for every take, from REC (before the cameras arm) to after STOP; each 100 ms block stamped with the hub's clock (`scratch.times.tsv`), so the mic's drift (A2) can be fitted out whatever it is. No mic or a failing mic never affects REC.

### A4. Quick mix: one button, finished video (user request, accepted 2026-11-16)

**What it does.** A "Make video" button on each synced song version. The hub cuts between the cameras with a fixed simple rule and renders one YouTube-ready file with the bounce as the soundtrack. No editor, no timeline, nothing to set.

- **Rule:** switch camera every 10 s, going round the Pi cameras in order (cam1, cam2, cam3, cam4, cam1, …). The first and last shots use the sync server camera (cam1). Skip a camera that didn't record that stretch or has a rough section (dropped frames, from the take's `gaps`) in it, and go on to the next. Same inputs give the same video.
- **Cameras:** Pi cameras only by default. Phones join once they have their own sync anchor (A3 "add media"); until then a phone's offset is only as good as its capture-delay estimate.
- **Output:** 1920×1080, exactly 30 fps, H.264, AAC 320 kbps from the synced bounce, `.mp4` with faststart, to `STUDIO/takes/<take>/edits/<song>/v<N>-quick.mp4` via `.part` and rename. Each output frame takes the active camera's frame nearest in real time (the editor plan's render, section 6). Hard cuts, no colour work beyond calibration.
- **Runs like ingest:** only when no take or live session is active, pauses on REC/LIVE, progress and ETA on the hub page. About real time on the Pi 5 (spike E3): a 4-minute song in about 5 minutes.
- **Checked after rendering:** length matches the song, frame count is exactly 30 × length, one spot-check frame per shot.
- **Not throwaway:** the cut is written in the editor's edit format (`docs/editor-plan.md` section 2) and the render is the editor's render. The editor's automatic cut (beats, movement, wide shots) later replaces the 10 s rule, and the timeline UI opens the same file.
- **Needs:** a synced bounce. The clap-only path (A3, then upload, songs and versions) is enough, so it doesn't wait for the USB mic.

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
4. Two versions of one song, uploaded separately, are both synced to the same place and both kept; a third version shifted in time is flagged.

## Track B: picture (first)

*Revised 2026-11-16 with the user (calibration per setup, done by showing a card; tripod/handheld phones; no autofocus on the Pis). Replaces the first version's one-off grey-card offsets and global looks.*

**How a setup works (the user's workflow).** Cameras get placed (Pis are fixed on the wall; phones on tripods or handheld), the lights are set, then every device in the take is calibrated by showing it the card. Calibration holds until the user shows the card again, so a new room or new light is just another walk round with the card. Settings never change during a take.

**B1. Snapshot overview page.** A still from every device (Pis and phones) side by side, for placing, framing and checking the match, with each device's status and its calibration state. Idle Pis: a short `rpicam-still` (0.6 s, B-S3). Live: a frame from the stream. Phones: a frame from the page. Never during a take, and never stored.

**B2. The calibration card.** One A4 sheet, printed on the laser printer (matte paper, glued to card): four ArUco markers in the corners, a large mid-grey patch in the middle, and a strip of about 12 colour patches (primaries, secondaries, skin tones, mid-tones). The markers let the hub find the card and the exact patch positions; their black and white are the black and white references. Printed colours don't need to be accurate: every device sees the same card, and the job is to match devices to each other.

**B3. Automatic calibration, no buttons.**
- While the rig is idle, each device that is waiting sends the hub a low-resolution frame about once a second (Pis: camnode, camera at low resolution; phones: the phone page). The hub (Pi 5) looks for the card; the Zeros only take the frames.
- Each device shows **"Waiting for card"** on the hub page (and on the phone page). When the card is found, steady for about a second, large enough and free of glare, the hub computes that device's settings from the grey patch, checks them with one more frame, and locks them: the device turns **"Calibrated ✓"** and the hub page plays a chime (for whoever holds the card).
- Settings: shutter stays 1/50 s; gain so the grey reads the target mid-grey; white balance so the grey is neutral. Pis set red and blue gains (covers colour temperature and green–magenta tint). Phones set ISO and a colour temperature (Chrome offers no tint); the leftover tint is handled by the editor (below). If a device needs maximum gain, it warns: not enough light for that device.
- Showing the card to a calibrated device while idle recalibrates it. During a take (and while arming) the hub ignores the card and nothing is sent.
- Option, if cheap: a device whose picture gets much brighter or darker than at calibration goes back to "Waiting for card" (someone switched a lamp on).
- Watching for the card must not delay REC or LIVE (the camera is released at once) nor heat the Zeros (B-S7).
- Hold the card where the performers will be: calibration is right for the light at the card. Mixed light (window plus lamps) can't be fixed by any card.

**B4. Card readings saved for the editor.** Each calibration stores, per device, how the grey, white, black and every colour patch read (and the settings it locked), and every take records the calibration it was shot with (in the manifest, then `take.json`). The editor's colour step uses this to match devices automatically: the phones' leftover tint, and the phones' colour rendering against the Pis'. The four Pis are the same camera model, so for them the grey alone is enough.

**B5. Focus.**
- Pis (fixed on the wall): one fixed focus per camera, set once with the focus help (a zoomed crop and a sharpness number on the snapshot page) and kept. The wide lens is sharp from about 1 m to infinity at 1080p and nobody comes closer than about 75 cm, so **no autofocus on the Pis** (the first version's B5 and spike B-S4 are dropped).
- Phones: the user marks each phone **tripod** or **handheld** when setting up (on its card on the hub page; the phone remembers it). Tripod: focus measured once and locked (as now). Handheld: continuous autofocus. Both keep calibrated exposure and white balance locked for the take; handheld phones are not framing-checked on the snapshot page.

**B6. "Auto, then lock" at REC (fallback).** For a take with no time or no card: on REC every device meters for about 1–1.5 s (B-S2: gain settles in 0.4–0.5 s) and locks. Less exact than the card (each device meters its own view; B-S2 measured a 0.26-stop spread across the Pis).

**B7. Manual trims.** On the snapshot page: an overall brightness trim and warmer/cooler, applied to every device on top of its calibration, and a per-device trim. Contrast, saturation and "looks" belong to the editor's colour step (`docs/editor-plan.md`), not to capture.

### Spikes

| | Spike | Done when |
|---|---|---|
| **B-S1** | Live mode: how long the stream blips when a setting changes. | **Done 2026-11-16: no blip** (`docs/phase4-spikes.md`). |
| **B-S2** | Auto metering time and spread across the cameras. | **Done 2026-11-16.** |
| **B-S3** | `rpicam-still` on a Zero 2 W while idle. | **Done 2026-11-16: 0.6 s, 21 MB.** |
| **B-S5** | Card detection: with the printed card, from how far a Zero (low-resolution frames, detection on the hub) and the Pixel 9a still find it, and how fast. User holds the card at a few distances, lights on, about 10 min. | Works at the performers' distance (expected 3–4 m), within about 2 s. |
| **B-S6** | Phone colour: on the Pixel 9a, whether the page can set exposure, ISO and colour temperature from the card, and how close to neutral the grey gets. User with the phone and the card, about 5 min. One phone stands for all. | Settings apply and lock; the leftover tint is measured. |
| **B-S7** | Watching for the card on an idle Zero: CPU, temperature, and how much it adds to REC and LIVE start. | No noticeable REC delay; temperature well below 70 °C. |

### Exit criteria

1. From the hub page alone, the user sets up a new room or light and calibrates every device in the take just by showing the card, with no buttons and no SSH.
2. After calibration, the card filmed by every Pi: grey brightness and white balance match within a set tolerance (fixed after B-S5/B-S6). Phones: within a looser tolerance, with the leftover recorded for the editor.
3. Calibrated and auto-then-lock takes keep the same exposure and white balance for their whole length, on every device.
4. Handheld phones refocus as the distance changes; tripod phones and Pis stay at their set focus.

## Track C: studio polish

- **C1. Recording light (smart bulb).** hubd sets the bulb from the rig state on every 2 s poll. Draft colours: dim green ready, blue live, amber arming, red recording, cyan ingest running, purple ingest done (safe to eject), flashing red error. It never blocks REC or STOP. Config holds the type and address. When it arrives: check its firmware (Tasmota or ESPHome) and set a DHCP reservation. Keep the lamp out of shot or dim, because white balance is fixed.
- **C2. Small items from Phases 2–3:**
  - Wi-Fi shown red below −65 dBm, with the link rate, on the camera cards.
  - Reword the deploy guard's "state idle" message when cameras are live.
  - Bump camnode's version number (the LED and lint changes still say 0.1.0).
  - Runbook (`docs/runbook.md`): mounting checklist, Wi-Fi −60 dBm per spot, OBS "Close file when inactive", phone local-network permission, stick repair (First Aid, eject).
  - Pin versions in `docs/versions.md`.
- **C3.** The rest of the original Phase 4 list (tally LEDs, GPIO REC button, OBS scene export, LTC) moved to future ideas (decision 4).

## Not in Phase 4 (future ideas, unless the user moves them in)

- Automatic edit on the hub beyond A4's fixed rotation (beats, movement, wide shots): the editor plan, `docs/editor-plan.md`.
- Pixel phones as offline camera units: moved ahead of Phase 4 as its own plan (`docs/phone-plan.md`, accepted 2026-11-15).
- Network share and Mac auto-pull (`docs/phase3-plan.md`, decision 6).
- "Discard take" button (parked 2026-11-15).
- Copying takes at their native timing without re-encoding: likely, now that the editor is our own (user, 2026-11-15: no Premiere; `docs/design.md`, "Editing").
- The AI-assisted editor itself (`docs/design.md`, "Editing").
- Live latency tuning (deferred 2026-11-14).
- From the original Phase 4 list (decision 4): tally LEDs on the cameras (the board LED and the bulb cover it), a physical REC button on the hub's GPIO, OBS scene-collection export, LTC timecode (only if Track A disappoints).

## Milestones

| | Milestone |
|---|---|
| B-S1–S3 | Picture spikes: done 2026-11-16. B-S5–S7 (card detection, phone colour, idle watching) need the printed card and about 15 min of the user's time. |
| **M-B** | Snapshot page and focus help; calibration card; automatic calibration by showing the card (Pis and phones); tripod/handheld phones; card readings saved for the editor; auto-then-lock fallback; manual trims. Spikes B-S5–B-S7 first. |
| A1–A3 | Audio spikes. A1 needs one real session from the user; it can run any time, in parallel with M-B. |
| **M-A** | Scratch recording during takes; bounce upload; matching and anchors; synced BWF on the drive; UI per take ("synced, confidence high, anchor: hub mic"). Tests. |
| **M-A4** | Quick mix: "Make video" per song version; 10 s rotation; render with progress; checks. Right after clap sync and upload work, before the hub-mic anchor. |
| **M-C** | Recording light (when the bulb is set up); small items; runbook. |
| **M-X** | Exit checks per track; `docs/phase4-results.md`. |

Order: **B (picture) first** (user priority), then A, then C. C1 whenever the bulb is ready.

## Decisions for the user

1. **Autofocus: answered (2026-11-15).** Build continuous AF as a per-camera, per-take option, off by default, without a separate test first. B-S4 becomes the check of the built option. CLAUDE.md updated.
2. **Auto-then-lock: answered (2026-11-15).** Yes, as an option next to manual settings; values are fixed for the whole take. CLAUDE.md updated.
3. **Order: answered.** Visuals first (user, 2026-11-15): B, then A, then C.
4. **C3: answered (2026-11-15).** None selected, so all four (tally LEDs, GPIO REC button, OBS export, LTC) move to future ideas.
5. **Bounce upload: answered (2026-11-15).** Yes: uploads are written to the drive like ingest. Plus songs and versions per take (see Track A).
6. **Quick mix (A4): answered (2026-11-16).** Yes, as described: fixed 10 s rotation, Pi cameras only (phones once they have their own anchor). Cuts on beats stay with the editor.
