# Phone cameras: build plan

Status: **draft for the user's OK** (2026-11-15). After the spike's GO (`docs/phone-spike-results.md`), the user made this the next item, before Phase 4 Track B. Track B moves back by however long this takes (estimate: 2 days).

## Goal

Any Pixel phone can join a session as an extra offline camera from a page on the hub. It records with the Pi cameras on the hub's REC/STOP, and its footage ends up on `STUDIO` as `phoneN.mov` next to `camN.mov`, on the same timecode and in sync, with no extra steps after the session.

## Settled (from the spike and the user)

- Browser page, no app. Camera access through the Chrome flag, set once per phone; no HTTPS.
- One phone tested stands for all: no per-phone tests. One capture-delay correction per model, default −40 ms for every model, kept in the hub config.
- Dropped frames are tolerated: marked, not fixed (see P4).
- Quality and look matching to the Pi cameras: later, not in this plan.
- Offline takes only. Live mode stays as it is (Pi cameras; phones via DroidCam if wanted).

## P1. Phones in hubd (the spike server goes away)

- hubd serves `http://studio-hub.local/phone`: the spike page, tidied. It adds a name picker, and the hub warns about a name already in use.
- Endpoints move into hubd: `/api/time`, `/api/phone/poll` (status in, command out), chunk upload, finish.
- Phones are rig members while their page is open: they're on the hub page as cards with camera cards, showing state, battery and charging, frames, drops, upload queue, clock RTT, page in front or not, and the locked settings.
- REC/STOP: every connected phone records with the Pi cameras under the same take id. The Pi sync start is unchanged; phones don't need it, since they're aligned by timestamp. A phone that's missing or not ready doesn't block REC. It shows as a warning, like a slow camera.
- At REC the hub warns (but doesn't refuse) if a phone isn't charging, its page isn't in front, its settings aren't locked, or the hub lacks disk space for it (next point).
- A phone that drops off mid-take carries on recording to its own storage, and its chunks upload when it comes back (the spike's OPFS resume, now tested: see Tests).

## P2. Where phone footage goes

During a take, chunks upload live to the hub's SD card (`/var/lib/hubd/phones/<take>/<phone>/`), as in the spike, and are kept on the phone too until the hub has verified them. The `STUDIO` drive stays unmounted during takes, as now. The hub's SD has ~23 GB free, and one phone uses ~7 GB per hour, so about 3 phone-hours fit. The hub shows the hours left and warns at REC when they run short. Question 1 below has an alternative.

## P3. Ingest

- `pending()` also lists phone takes from the hub folder, finished and not yet verified. There's no pull step, since they're already on the hub.
- Conform as for a Pi camera (mkvmerge with `video.pts`, then the 30 fps grid with timecode), with the capture-delay correction applied to `first_frame_wallclock_ns`. Verify frame counts the same way.
- Output `phoneN.mov` in the take's folder on `STUDIO`, next to `camN.mov`, written through `.part` and renamed, as now.
- After verify: the hub deletes its staging copy (hub SD, not the drive), and the next time the phone's page opens it clears its own copy. The hub never deletes from the drive.
- The take list and playback on the hub page include phones.

## P4. Rough-section markers (all cameras)

- From each manifest's `gaps`: a "rough" section is any 2 s window missing ≥ 3 frames (to tune). The take list shows them per camera, for example "phone1: 6 rough sections, 2.1 min".
- Premiere: markers on each clip. Spike P4a: which route Premiere reads reliably, either XMP markers embedded in the `.mov` or an importable marker file per clip. If neither works cleanly, a plain list per take (`markers.txt`, timecode in and out per camera) ships instead.
- The later auto-edit uses the same sections.

## P5. Docs

Runbook: phone setup (Chrome flag, local-network permission, 5 GHz, plugged in, landscape), and what the warnings mean. Pin anything new in `docs/versions.md`. Remove `hub/spike/phone/phone_spike.py` once hubd has taken over; keep `sync_check.py` in `hub/tools/`.

## Tests

- Unit tests (hubd and ingest, as now): clock fit, chunk assembly, delay correction, phone in REC/STOP, take list with phones, rough sections.
- On the rig, with one Pixel 3: (1) a take with cam1–cam4 and the phone from the hub page, through ingest to `STUDIO`: `phone1.mov` plays in the hub page and in Premiere, on the same timecode as cam1; (2) Wi-Fi off on the phone for 1 min mid-take, then back: no chunks lost; (3) page reload after STOP: the leftovers upload.

## Not in this plan

Colour and look matching to the Pi cameras, live mode for phones, the phone's audio for sync (Track A can add it later), and HTTPS.

## Questions for the user

1. **Phone footage during a take.** Recommended: **live upload to the hub SD** (as above), so footage is safe on the hub even if a phone dies, but it's limited to about 3 phone-hours per session before ingest runs. The alternative is to keep it on the phone and upload after STOP, which is limited only by phone storage, but the page must stay open after the take until the upload finishes (an 11-min take took about as long to upload).
2. **Phones block REC?** Recommended: **no**, just a warning. A phone is an extra angle, and the Pi cameras shouldn't wait for one.
