# hub/: the studio hub

Everything that runs on `studio-hub`, a Raspberry Pi 5 on wired Ethernet. The hub is the brain of the rig: the cameras only capture, and the Mac only edits.

It runs two independent services, so the overnight ingest can never disturb a take:

| Service | Code | Job |
|---|---|---|
| **`hubd`** | `hubd/hubd.py` | The web UI and control API: REC / STOP / LIVE across all cameras, camera health, take history |
| **`hub-ingest`** | `ingest/ingest.py` | The overnight worker: pull takes from the cameras, convert, verify, write to the `STUDIO` drive |

The hub is also the **clock** for the whole rig (chrony, `chrony/studio-hub.conf`). Every camera stamps its frames against the hub's time, which is what lets takes from different cameras line up.

## What's here

| Path | What it is |
|---|---|
| `hubd/hubd.py` | Control service, port 80 (IPv4 and IPv6) |
| `hubd/static/index.html` | **The web UI.** One plain HTML/JS file with no build step: camera cards, REC/STOP, LIVE, OBS stream URLs with copy buttons, live-preview links, take history, and the ingest and drive panel |
| `hubd/tests/` | Unit tests with fake cameras |
| `ingest/ingest.py` | The ingest worker |
| `ingest/tests/` | Unit tests |
| `systemd/` | `hubd.service` and `hub-ingest.service` |
| `chrony/studio-hub.conf` | NTP server config for the cameras |
| `spike/convert.py` | Phase 3 test tool: converts one camera's take two ways, native timing and conformed to 30 fps, for comparison in Premiere |

## hubd: what happens when you press REC

1. **Checks:** every selected camera must be online, clock-synced, cool enough and have free space. If any check fails, REC is refused and the page says which camera and why.
2. Live mode is stopped if it was on.
3. All cameras except one are armed as sync **clients**. After 2 s the last one is armed as the sync **server** (cam1 by default).
4. hubd waits until every camera reports recording. If sync doesn't happen within 20 s, the take is aborted on every camera.
5. **STOP** stops all cameras in parallel and shows a summary: start spread, frame counts, dropped frames and sync warnings.

If hubd restarts mid-take, it picks the running take back up. The cameras carry on regardless.

### API

| Call | What it does |
|---|---|
| `GET /api/status` | Rig state plus every camera's status (the page polls this every 2 s) |
| `GET /api/takes` | Take log |
| `POST /api/rec`, `POST /api/stop` | Start and stop a synced take |
| `POST /api/live` | Live mode on or off (`{"on": true}`) |
| `GET /api/ingest` | Ingest and drive status |
| `POST /api/ingest/start` | "Ingest now" |
| `POST /api/drive/eject` | Unmount and power off the `STUDIO` drive |

## hub-ingest: overnight processing

It starts after 30 minutes with no take or live session, or when you press **Ingest now**. For each take and each camera, it does the steps below. Each step is saved, so it resumes after a pause, a crash or a pulled drive.

1. **Pull** the take with `rsync` over SSH, from all cameras in parallel. The hub's key can only *read* the takes folder on the cameras.
2. **Check** the video against the camera's sha256.
3. **Conform** to exactly 30.000 fps by timestamp, re-encoded with libx264. This is the subtle part:
   - The sensor really runs at about 30.011 fps, and Wi-Fi hiccups occasionally drop frames.
   - Stamping every frame as exactly 1/30 s would drift about 1.3 s per hour against the studio audio, and each dropped frame would shift everything after it.
   - So every frame is placed by its real timestamp onto a 30 fps grid anchored to the time of day. A drop becomes a repeated frame, and sync is kept.
4. **Verify** the result: frame count against expected, frame rate, timecode, and a full decode.
5. **Write** to `STUDIO/takes/<YYYY-MM-DD>_<take>/<cam>.mov`. Files are written under a temporary `.part` name, flushed to disk, checked, then renamed. A pulled drive leaves only `.part` files, never a half-written `.mov`.
6. **Mark verified** on the camera, so the camera may delete its copy later when it needs space.

Every `.mov` gets a time-of-day timecode, so in Premiere you create a multicam sequence by **Timecode**.

**It pauses the moment you press REC or LIVE.** rsync is stopped and later resumes from partial files, and ffmpeg is suspended. Ingest never competes with a take for Wi-Fi or CPU.

### The `STUDIO` drive

- Only a drive labelled `STUDIO` is ever written to.
- It's checked (`fsck.exfat`) once each time it's plugged in, and refused if it isn't clean.
- It's mounted only while writing (at `/mnt/studio`), so it's safe to unplug whenever the page shows it idle. **Eject** forces that.
- **The hub never deletes anything from the drive.** Clearing old takes is your call.

hubd and hub-ingest talk through files in `/var/lib/hubd/ingest/`: ingest writes `state.json`, and hubd drops command files into `commands/`.

## Tests

```sh
cd hub/hubd   && python3 -m unittest discover -s tests
cd hub/ingest && python3 -m unittest discover -s tests
```
