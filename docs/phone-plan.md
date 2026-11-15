# Phone cameras: build plan

Status: **accepted 2026-11-15** (questions answered below). Next session starts with P1. After the spike's GO (`docs/phone-spike-results.md`), the user made this the next item, before Phase 4 Track B. Track B moves back by however long this takes (estimate: 2 days).

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
- REC/STOP: every connected, ready phone records with the Pi cameras under the same take id. The Pi sync start is unchanged; phones don't need it, since they're aligned by timestamp. Phones never block REC (user, 2026-11-15: many sessions use none, and that's expected). A phone that's open but not ready gets a warning; a phone that isn't open isn't mentioned at all.
- At REC the hub warns (but doesn't refuse) if a phone isn't charging, its page isn't in front, its settings aren't locked, or the hub lacks disk space for it (next point).
- A phone that drops off mid-take carries on recording to its own storage, and its chunks upload when it comes back (the spike's OPFS resume, now tested: see Tests).

## P2. Where phone footage goes

During a take, chunks upload live to the hub's SD card (`/var/lib/hubd/phones/<take>/<phone>/`), as in the spike, and are kept on the phone too until the hub has verified them. The `STUDIO` drive stays unmounted during takes, as now. One phone uses ~6.7 GB per hour. The hub shows the phone-hours left and warns at REC when they run short.
- At finish, the hub joins the chunks into `video.h264` and then deletes them. The spike kept both, which doubled the space.
- Sizing (user's target, 2026-11-15): 5 phones × two 1-hour sessions = 10 phone-hours ≈ 67 GB. The current 32 GB card has ~23 GB free (~3 phone-hours). A 64 GB card (~55 GB free) is not enough; a **128 GB** card (~115 GB free) is.

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

## Open question: ingest time (decide before P3 is finished)

Ingest re-encodes every camera-hour onto the exact 30 fps grid at about real time on the Pi 5, one camera at a time (Phase 3 S2). Two 1-hour sessions with 4 Pi cameras and 5 phones are 18 camera-hours, so **~18 h of ingest**: more than a night. It pauses during takes and resumes when the rig is idle, so nothing is lost, but the footage isn't on `STUDIO` the next morning. Options to weigh, with numbers, during P3:
- Copy phone footage at its native timing (`-c copy`, a gap becomes one long frame), skipping the re-encode for phones. This is only possible if the editor handles it; see "native 30.0107 fps" in the Phase 4 plan's future ideas.
- Conform on the Mac instead of the hub (the drive goes to the Mac anyway).
- Accept the delay for big sessions.

## Not in this plan

Colour and look matching to the Pi cameras, live mode for phones, the phone's audio for sync (Track A can add it later), and HTTPS.

## Decisions (user, 2026-11-15)

1. **Phone footage during a take: live upload to the hub's SD card** (P2). Measured 2026-11-15 on the current card (unbranded 32 GB, running at 50 MHz "SD high-speed"): 15.6 MB/s write with ingest running, 23.8 MB/s read. A 3-phone load (2 MB chunk with fsync per phone per second) took 0.29 s median and 0.77 s worst per chunk: it works, but with little headroom, and the card is also ingest's working disk. **The user will buy a 128 GB high-endurance UHS-I (U3/V30) card**, the same type as the nodes', for about 2–4× the speed and ~17 phone-hours (64 GB was considered first, but it doesn't fit 5 phones × two 1-hour sessions; see P2). Five phones write ~10 MB/s together, too close to this card's 15.6 MB/s. The build doesn't wait for it; swapping the hub's card is a reflash or clone, so ask first.
2. **Phones never block REC.** Ready phones record; others are ignored or warned about (P1).
