# Phone camera spike: results (2026-11-15)

Plan: `docs/phone-spike-plan.md`. Code: `hub/spike/phone/`. Device: Pixel 3, Chrome 157, browser page only (no app). Spike data was on the hub in `/var/lib/hubd/phone-spike/` (deleted 2026-11-15 after the results were written); comparison clips kept on the Mac in `~/Movies/phone-spike/`.

## Verdict

**GO** (user, 2026-11-15). Building it is the next item, before Phase 4 Track B. A Pixel 3 in Chrome works as a hub-controlled offline camera: REC/STOP from the hub page, 1080p30 hardware H.264, upload while recording, and frames timed on the hub clock to within half a frame after a fixed correction. The user accepts the picture quality and tolerates dropped frames (marked, not fixed). Per the user, the other phones (Pixel 3s, Pixel 9s, Pixel 8) are assumed to behave the same, with no per-phone tests.

## Results against the plan

| Test | Result |
|---|---|
| 1. Locks | Exposure time (1/50 s) and ISO lock. Focus locks; a contrast sweep picks it (user: looks right). WB locks to Chrome's AWB presets (2850–7000 K), not a smooth scale. Chrome doesn't report live auto ISO/WB/focus, so "auto, then lock" measures the picture. In this room 1/50 s needs ISO ~1550 (auto wanted 1/16 s). |
| 2. Encode, 11 min | Hardware H.264 High 1920×1080, ~15 Mbps, no encoder backlog, no lost chunks, page stayed in the foreground. **776 frames missing (3.8%)**, in skips of 2–3 frames, up to 148/min for minutes 2–6, then 3–24/min. The camera pipeline skipped them (frames that did arrive came 3–4 ms after capture), likely load or heat. Accepted by the user: marked, not fixed (`docs/design.md`). |
| 3. Sync | Take 20261115-215022, 11 min, claps confirmed by cam1 and cam2. Phone vs cam1: **+45 ms at the start (27 claps), +51 ms at 6 min (16)**, trend −0.6 ms/min. Control cam2 vs cam1: +0 / −2 ms. Short take 20261115-213520: +31 ms (4 claps). So there's **no drift, and a fixed delay of ~40 ms ± 15**: the capture→page latency, which isn't in Chrome's frame timestamps. |
| 4. Quality | User: "fine". It doesn't look like the Pi cameras (colour/look); matching is later, not a concern. |
| 5. Robustness | Not stressed (no Wi-Fi drop or reload mid-take). Chunks are in OPFS for resume; untested. |

## What a real build needs (plan to follow for the user's OK)

- A fixed capture-delay correction per phone model (start at −40 ms for all), as one config value.
- Phones in hubd itself (not a separate server), shown with the Pi cameras, and in the take list and ingest. The output is already in the Pi layout (`video.h264`, `video.pts`, `manifest.json` with `first_frame_wallclock_ns` and `gaps`).
- Camera access: the Chrome flag stays (user, 2026-11-15: it's a one-off per phone, so no local CA or HTTPS).
- Rough-section markers from `gaps` (hub timeline + Premiere markers), for Pi cameras and phones alike.
- Optional cross-check later: the phone's own mic against the hub scratch mic (Track A).
