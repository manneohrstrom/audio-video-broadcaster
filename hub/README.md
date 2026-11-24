# hub/: the studio hub

Everything that runs on `studio-hub`, a Raspberry Pi 5 (1 GB) on wired Ethernet. The hub is the brain of the rig: the cameras only capture, and any computer's browser is the screen. For parts, assembly and the workflow, see the [main README](../README.md).

It runs three independent services, so slow work can never disturb a take:

| Service | Code | Job |
|---|---|---|
| **`hubd`** | `hubd/` | The web pages and control API: REC / STOP / LIVE, phones, calibration, health, names, bounce uploads, editor projects and the automatic cut, the hub mic, the recording light |
| **`hub-ingest`** | `ingest/ingest.py` and its modules | Pulls takes, verifies them and writes them to the drive; syncs bounces; finds beats and movement; makes thumbnails and editing proxies; renders videos; deletes takes when asked |
| **`hub-upload`** | `ingest/youtube_upload.py` | Uploads finished videos marked "YouTube", one at a time, as private videos |

The hub is also the **clock** for the whole rig (chrony, `chrony/studio-hub.conf`). Every camera and phone stamps its frames against the hub's time, which is what lets takes from different cameras line up.

## What's here

### `hubd/`: control and web pages

| Path | What it is |
|---|---|
| `hubd.py` | Control service, port 80 (IPv4 and IPv6) |
| `request_guard.py` | Refuses requests from other websites (Host and Origin checks) |
| `phones.py` | Phone cameras: who's ready, chunk uploads during a take, joining each phone's video |
| `calibrate.py`, `calib.py` | Calibration: watches for the card, reads it (OpenCV ArUco), sets gain and colour gains |
| `budget.py` | Record-time estimate; stops a take that runs out of space |
| `audio.py` | Bounce uploads: songs and versions per take, resumable |
| `scratch.py` | The hub mic: records every take, stamped with the hub's clock |
| `editor_projects.py` | What the editor loads and saves for each song |
| `auto_cut.py` | The automatic cut: rules on beats, movement and rough sections |
| `hand_sync.py` | Sync by hand from the editor |
| `names.py` | Names given to cameras, phones and takes |
| `health_history.py` | Recent health readings for the monitor's charts |
| `lamp.py` | The recording light (Tasmota or ESPHome bulb) |
| `static/hub/` | The hub page (Preact + htm): deck, monitor, cameras and archive views |
| `static/editor/` | The editor (Preact + htm): Cut, Look and Title steps, and Sync mode |
| `static/shared/` | What the two share: design tokens, icons, vendored libraries and fonts |
| `static/phone.html` | The phone page (plain JS): `/phone` for handheld, `/phone/tripod` for phones in brackets |
| `tests/` | Unit tests with fake cameras, phones and mic; `tests/editor/` tests the editor's JavaScript |

### `ingest/`: the slow work

| Path | What it is |
|---|---|
| `ingest.py` | The worker: ingest, drive handling and the job queue |
| `audiosync.py` | Places a bounce on the take by matching it to the hub mic's recording |
| `beats.py`, `motion.py` | Analysis for the automatic cut: beats and bars, movement per camera |
| `thumbnails.py`, `proxies.py` | Timeline thumbnails and 540p editing copies, kept on the hub's SD card |
| `edit_render.py` | Renders the editor's edit |
| `story_render.py` | Its Instagram story: the same render, a 9:16 slice per camera, 1080x1920 |
| `look_render.py`, `grade.py`, `title_render.py` | The look and the title in the render, matching the editor's preview |
| `render.py` | The older quick mix: a new camera every 10 s |
| `drive_records.py` | The drive as the record of every take |
| `youtube_upload.py` | The `hub-upload` service |
| `tests/` | Unit tests (some run real ffmpeg) |

### The rest

| Path | What it is |
|---|---|
| `calib/` | The calibration card: `card.json` (layout), `calibration-card.pdf` (print A4 landscape at 100%, colour, matte), `make_card.py` (regenerates the PDF) |
| `brand/` | The source logo for the video border |
| `tools/clap_check.py`, `tools/sync_check.py` | Sync checks from claps |
| `tools/beat_clicks.py` | Hear what the beat finder found |
| `tools/add_overlay.py` | Add a film overlay (grain, dust, light leak) for the editor; none ship with the repo |
| `tools/make_brand_frame.py` | Rebuild the border and logo images |
| `systemd/` | The three services |
| `chrony/studio-hub.conf` | Time server config. Its `allow` line must match your network. |
| `spike/` | Experiments kept for reference, not deployed |

## hubd: what happens when you press REC

1. **Checks:** offline cameras are left out with a warning, and at least one must be online. Each online camera must be clock-synced, under 70 °C, not throttled, with Wi-Fi power saving off and an hour of free space, and there must be at least 2 minutes of recording space overall. If a check fails, REC is refused and the page says which camera and why. Phones never block REC.
2. Live mode is stopped if it was on, and no new calibration stills are taken.
3. The hub mic starts recording (if one is plugged in).
4. All cameras except one are armed as sync **clients**, each with the hub's settings for that camera (calibrated gain, colour gains, focus). After 2 s the last one is armed as the sync **server** (cam1 by default). Ready phones start too.
5. hubd waits until every camera reports recording. If sync doesn't happen within 20 s, the take is aborted. If a camera started more than a frame out, hubd restarts the take by itself, up to twice, then records with a warning.
6. **STOP** stops all cameras in parallel, then the mic, and shows a summary: start spread, frame counts, dropped frames and sync warnings.

If hubd restarts mid-take, it picks the running take back up. The cameras carry on regardless. If the hub's or a camera's space runs out, hubd stops the take itself.

### API (main calls)

The routes are all in `hubd/hubd.py`. None need a login. A request is answered only if its `Host` is a local name or IP address and its `Origin`, if it has one, is the hub's own (`hubd/request_guard.py`); tools like curl send no `Origin`.

| Call | What it does |
|---|---|
| `GET /api/status` | Rig state, every camera's and phone's status, record-time estimate, calibration, hub mic, recording light |
| `GET /api/history` | Recent health readings |
| `GET /api/takes`, `GET /api/ingest` | Take log; ingest, drive, audio, render and upload status |
| `POST /api/rec`, `POST /api/stop` | Start and stop a synced take |
| `POST /api/live` | Live mode on or off (`{"on": true}`) |
| `GET /api/snap/camN.jpg` | A still from a camera, with its settings |
| `POST /api/camera/camN/settings` | Change a camera's focus, gain or colour gains |
| `POST /api/names` | Name a camera or take |
| `POST /api/ingest/start`, `POST /api/drive/eject` | "ingest now"; eject the drive |
| `POST /api/takes/<take>/delete` | Delete a take from the drive, the hub and the cameras |
| `/api/audio/<take>/...` | Bounce upload (see `hubd/audio.py`) |
| `POST /api/sync` | Run the automatic sync again |
| `/api/editor/...` | The editor: project, save, sync by hand, waveform, Make video |
| `POST /api/render` | The older quick mix (no button on the hub page) |
| `GET /api/youtube` | Whether the hub is signed in to YouTube |
| `GET /api/lamp`, `POST /api/lamp/preview` | Recording light status; show a look for a few seconds |
| `/api/phone/...` | The phone pages' own calls |
| `/media/...`, `/media-audio/...`, `/media-proxy/...` | Footage and videos from the drive, bounces and editing proxies from the hub (all seekable) |

## hub-ingest: when the rig is idle

It starts after 30 minutes with no take or live session, or when you press **ingest now**, and only with the `STUDIO` drive plugged in. Without the drive, Pi takes wait on the cameras. For each take and each camera, it does the steps below. Each step is saved, so it resumes after a pause, a crash or a pulled drive.

1. **Pull** the take with `rsync` over SSH, from all cameras in parallel. The hub's key can only *read* the takes folder on the cameras. Phones' takes are already on the hub.
2. **Check** the video against the camera's sha256.
3. **Convert** (the "conformed" step): copy the H.264 into a `.mov` without re-encoding, with **every frame at its real capture time** and a time-of-day timecode. This is the subtle part:
   - The sensor really runs at about 30.011 fps, and Wi-Fi hiccups occasionally drop frames.
   - Stamping every frame as exactly 1/30 s would drift about 1.3 s per hour against the studio audio, and each dropped frame would shift everything after it.
   - So each frame keeps its real time; a dropped frame becomes one long frame. The render puts frames onto an exact 30 fps grid, locked to the bounce.
   - The older way, re-encoding onto an exact 30 fps grid at ingest, is still there as `video_timing: conform`.
4. **Verify** the result: every frame's time against the manifest (gaps included), the timecode, and a full decode.
5. **Write** to `STUDIO/takes/<YYYY-MM-DD>_<take>/<cam>.mov`. Files are written under a temporary `.part` name, flushed to disk, checked, then renamed. A pulled drive leaves only `.part` files, never a half-written `.mov`.
6. **Mark verified** on the camera, which then deletes its copy. Each camera is marked as soon as its own `.mov` is safely on the drive, so from then on the drive holds the only copy. `take.json` (manifests, rough sections) is written once every camera is on the drive.

The other jobs:

| Job | When it runs |
|---|---|
| Automatic sync, beats | As soon as the rig is quiet. Neither needs the drive. |
| Delete take | As soon as the rig is quiet, with the drive in if the take is on it |
| Copy bounces and mic recordings to the drive | With ingest |
| Thumbnails | After ingest, or at once when the editor opens a take without them |
| Editing proxies, movement | With ingest, for takes that have a synced song |
| Render (**Make video**) | Queued, then run as soon as the rig is quiet, without the 30-minute wait. It needs the take on the drive. The finished file is checked and written to `takes/<take>/edits/<song>/`, never replacing an older video. |
| YouTube upload | `hub-upload`, after the video is on the drive, whenever no take or LIVE is running |

**It pauses the moment you press REC or LIVE.** rsync is stopped and later resumes from partial files, and ffmpeg is suspended. Ingest never competes with a take for Wi-Fi or CPU.

### The `STUDIO` drive

- Only a drive labelled `STUDIO` is ever written to.
- It's checked (`fsck.exfat`) each time it's plugged in and before every write, and refused if it isn't clean.
- It's mounted at `/mnt/studio` read-write only while writing or deleting, read-only while it's being read, and otherwise unmounted. **eject drive** unmounts it and powers it down.
- **The drive is the record:** a take deleted from it on a computer disappears from the hub, and a drive moved to a new hub brings its takes along.
- **The hub deletes a take from the drive only when asked,** with the archive's **delete take**, after a confirmation. Videos already on YouTube stay there.

hubd and hub-ingest talk through files in `/var/lib/hubd/ingest/`: ingest writes `state.json`, and hubd drops command files into `commands/`. Uploads and mic recordings wait in `/var/lib/hubd/audio/` until they're on the drive.

## Branding

"Brockley Live" is the original band's name. To use your own:

| Where | What to change |
|---|---|
| `hubd/static/hub/index.html`, `hubd/static/hub/js/components/App.js` | The hub page's title and name |
| `hubd/static/editor/index.html`, `hubd/static/editor/js/components/TitlePanel.js` | The editor's title and labels |
| `BRAND_TEXT` in `hubd/static/editor/js/title/title-layout.js` **and** `ingest/title_render.py` | The title sequence. Change both, or the preview and the render differ. |
| `SLICE_WIDTH` in `hubd/static/editor/js/story.js` **and** `ingest/story_render.py` | The story's slice. Change both, or the preview and the render differ. |
| `brand/brockley-live-logo.png` | Replace the logo, then run `python3 hub/tools/make_brand_frame.py` from the repo root |

Some tests check the original name and logo.

## Tests

From the repo root. They need Python 3 with NumPy and Pillow, ffmpeg, and Node.js.

```sh
(cd hub/hubd   && python3 -m unittest discover -s tests)
(cd hub/ingest && python3 -m unittest discover -s tests)
(cd hub/hubd   && node --test tests/editor/*.test.mjs)
```
