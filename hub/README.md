# hub/: the studio hub

Everything that runs on `studio-hub`, a Raspberry Pi 5 (1 GB) on wired Ethernet. The hub is the brain of the rig: the cameras only capture, and the Mac only edits.

It runs two independent services, so work on the drive can never disturb a take:

| Service | Code | Job |
|---|---|---|
| **`hubd`** | `hubd/` | The web pages and control API: REC / STOP / LIVE across all cameras, phones, calibration, camera health, record-time estimate, bounce uploads, the hub mic, the recording light |
| **`hub-ingest`** | `ingest/` | Works only while the rig is idle: pulls takes from the cameras, copies them to `.mov`, verifies, writes them to the `STUDIO` drive; copies bounces there; renders quick-mix videos |

The hub is also the **clock** for the whole rig (chrony, `chrony/studio-hub.conf`). Every camera and phone stamps its frames against the hub's time, which is what lets takes from different cameras line up.

## What's here

| Path | What it is |
|---|---|
| `hubd/hubd.py` | Control service, port 80 (IPv4 and IPv6) |
| `hubd/phones.py` | Phone cameras: who's ready, chunk uploads during a take, assembling each phone's video |
| `hubd/calibrate.py`, `hubd/calib.py` | Calibration: watches idle cameras and phones for the card, reads it (OpenCV ArUco), sets gain and colour gains; card layout in `calib/card.json` |
| `hubd/budget.py` | Record-time estimate (and how much fits on the drive); stops a take that runs out of space |
| `hubd/audio.py` | Bounce uploads: songs and versions per take, resumable |
| `hubd/scratch.py` | The hub mic: records every take from a USB mic, stamped with the hub's clock |
| `hubd/lamp.py` | The recording light: a Tasmota or ESPHome smart bulb showing the rig state |
| `hubd/static/index.html` | **The hub page.** One plain HTML/JS file, no build step: camera and phone cards, REC/STOP, LIVE, OBS URLs, take list with playback, ingest and drive, audio upload, quick mix |
| `hubd/static/snap.html` | The snapshot page (`/snap`): every camera's picture, focus help, calibration status |
| `hubd/static/phone.html` | The phone page (`/phone`): turns an Android phone into a camera |
| `hubd/tests/` | Unit tests with fake cameras, phones and mic |
| `ingest/ingest.py` | The ingest worker |
| `ingest/render.py` | Quick mix: the automatic cut (in the editor's edit format) and the render |
| `ingest/tests/` | Unit tests (the render test runs real ffmpeg) |
| `calib/` | The calibration card: `card.json` (layout the hub reads) `make_card.py` (makes the printable PDF), and `calibration-card.pdf` (the PDF itself; print A4 landscape at 100%, colour, matte; regenerate with `make_card.py card.json calibration-card.pdf` if `card.json` changes) |
| `tools/clap_check.py`, `tools/sync_check.py` | Checks: claps in a bounce against motion on each camera; each camera's offset from a reference camera at every clap (raw take folders, Pis and phones) |
| `systemd/` | `hubd.service` and `hub-ingest.service` |
| `chrony/studio-hub.conf` | NTP server config for the cameras |
| `spike/` | Spike code kept for reference: `e1/`–`e3/` (editor spikes: real-timing copy, browser playback, render cost), `a3/` (clap-only sync), `convert.py` (Phase 3: one take converted two ways for comparison) |

## hubd: what happens when you press REC

1. **Checks:** every selected camera must be online, clock-synced, cool enough and have free space, and there must be at least 2 minutes of recording space. If a check fails, REC is refused and the page says which camera and why.
2. Live mode is stopped if it was on, and no new calibration stills are taken.
3. The hub mic starts recording (if one is plugged in).
4. All cameras except one are armed as sync **clients**, each with the hub's settings for that camera (calibrated gain, colour gains, focus). After 2 s the last one is armed as the sync **server** (cam1 by default). Ready phones start too.
5. hubd waits until every camera reports recording. If sync doesn't happen within 20 s, the take is aborted on every camera.
6. **STOP** stops all cameras in parallel, then the mic, and shows a summary: start spread, frame counts, dropped frames and sync warnings.

If hubd restarts mid-take, it picks the running take back up. The cameras carry on regardless. If the hub's or a camera's space runs out, hubd stops the take itself.

### API (main calls)

| Call | What it does |
|---|---|
| `GET /api/status` | Rig state, every camera's and phone's status, record-time estimate, calibration, hub mic, recording light |
| `GET /api/takes` | Take log |
| `POST /api/rec`, `POST /api/stop` | Start and stop a synced take |
| `POST /api/live` | Live mode on or off (`{"on": true}`) |
| `GET /api/snap/camN.jpg` | A still from a camera, with its settings |
| `POST /api/camera/camN/settings` | Change a camera's settings (focus, gain, colour gains) |
| `GET /api/ingest` | Ingest, drive, audio and render status per take |
| `POST /api/ingest/start`, `POST /api/drive/eject` | "Ingest now"; unmount and power off the `STUDIO` drive |
| `GET /api/audio/<take>`, `POST .../songs`, `POST .../<song>/versions`, `PUT .../<song>/v<N>?offset=X`, `POST .../v<N>/finish`, `POST .../<song>/choose` | Bounce upload (see `hubd/audio.py`) |
| `POST /api/render` | Quick mix for a synced song version (`{"take", "song", "version"}`) |
| `GET /api/lamp`, `POST /api/lamp/preview` | Recording light status; show a look for a few seconds |
| `GET /media/<take>/<cam>.mov`, `GET /media/<take>/edits/<song>/<name>.mp4` | Play a take or a rendered video from the drive (with seeking) |
| `/api/phone/...` | The phone page's own calls (poll, chunks, finish, snapshots, calibration) |

## hub-ingest: when the rig is idle

It starts after 30 minutes with no take or live session, or when you press **Ingest now**. For each take and each camera, it does the steps below. Each step is saved, so it resumes after a pause, a crash or a pulled drive.

1. **Pull** the take with `rsync` over SSH, from all cameras in parallel. The hub's key can only *read* the takes folder on the cameras. Phones' takes are already on the hub.
2. **Check** the video against the camera's sha256.
3. **Convert** (the "conformed" step): copy the H.264 into a `.mov` without re-encoding, with **every frame at its real capture time** and a time-of-day timecode. This is the subtle part:
   - The sensor really runs at about 30.011 fps, and Wi-Fi hiccups occasionally drop frames.
   - Stamping every frame as exactly 1/30 s would drift about 1.3 s per hour against the studio audio, and each dropped frame would shift everything after it.
   - So each frame keeps its real time; a dropped frame becomes one long frame. The render (quick mix, later the editor) puts frames onto an exact 30 fps grid, locked to the bounce.
   - The older way, re-encoding onto an exact 30 fps grid at ingest, is still there as `video_timing: conform`.
4. **Verify** the result: every frame's time against the manifest (gaps included), the timecode, and a full decode.
5. **Write** to `STUDIO/takes/<YYYY-MM-DD>_<take>/<cam>.mov`. Files are written under a temporary `.part` name, flushed to disk, checked, then renamed. A pulled drive leaves only `.part` files, never a half-written `.mov`.
6. **Mark verified** on the camera, so the camera may delete its copy later when it needs space. Each camera is marked as soon as its own `.mov` is safely on the drive. `take.json` (manifests, rough sections) is written once every camera is on the drive.

Then, still only while the rig is idle:
- **Audio:** finished bounce uploads and hub mic recordings are copied to `takes/<take>/audio/` the same way.
- **Quick mix renders** that were asked for: the cut goes round the Pi cameras every 10 s, skipping cameras with dropped frames there; the render reads the drive read-only, writes to the hub's SD, checks frame count, length and that sampled shots show the right camera, then copies the `.mp4` and its edit file to `takes/<take>/edits/<song>/` (never replacing an older one).

**It pauses the moment you press REC or LIVE.** rsync is stopped and later resumes from partial files, and ffmpeg is suspended. Ingest never competes with a take for Wi-Fi or CPU.

### The `STUDIO` drive

- Only a drive labelled `STUDIO` is ever written to.
- It's checked (`fsck.exfat`) each time it's plugged in and before every write, and refused if it isn't clean.
- It's mounted at `/mnt/studio` read-write only while writing, read-only while someone plays a take back or a render reads it, and otherwise unmounted. **Eject** unmounts it and powers the port down.
- **The hub never deletes anything from the drive.** Clearing old takes is your call.

hubd and hub-ingest talk through files in `/var/lib/hubd/ingest/`: ingest writes `state.json`, and hubd drops command files into `commands/`. Uploads and mic recordings wait in `/var/lib/hubd/audio/` until they're on the drive.

## Tests

```sh
cd hub/hubd   && python3 -m unittest discover -s tests
cd hub/ingest && python3 -m unittest discover -s tests
```
