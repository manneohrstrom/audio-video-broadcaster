# Phase 3 plan: ingest

> **Closed at 1.0 (2026-11-22).** This is a record of the work as planned, kept for reference. Nothing in it is still to do: see [release-1.0.md](release-1.0.md).

Status: **plan accepted by the user ("the plan sounds good", 2026-11-14). S1 done: Path B chosen. S3 measured. Building M1–M3.** Written 2026-11-14 after Phase 2 passed (`docs/phase2-results.md`).

## Goal

After a session, the hub pulls every take from the cameras onto the `STUDIO` drive as `.mov` files. All files share one timeline (time-of-day timecode on an exact 30 fps grid), stay in sync with each other **and with real time (the DAW audio)**, and are verified. The user then presses Eject, moves the drive to the Mac, and lines the cameras up by timecode in the editor. Cameras free space only after verify passes, and only when they need it.

**Exit criteria:**
1. A multi-camera take of at least 30 min, with at least one dropped frame on some camera, is ingested unattended. It verifies, and the cameras line up by timecode. A stopwatch or clap on all angles lines up at start, middle and end, within 1 frame, against each other and against a real-time reference (DAW audio, or the stopwatch read against the timecode).
2. Starting REC or LIVE during ingest pauses ingest at once, with no effect on the take. Ingest resumes when the rig is idle, without re-pulling finished files.
3. Pulling the drive or cutting power mid-write leaves no file that looks complete but isn't. The next ingest repairs or redoes it.
4. Cameras delete only verified takes, oldest first, only below a free-space threshold. The hub never deletes from the drive.
5. Eject in the UI unmounts the drive cleanly. A drive without the `STUDIO` label is never written to.

**Out of scope:** LTC, image-profile UI and the other Phase 4 items. Samba share and Mac auto-pull too, unless the user wants them (see the decisions).

## The key problem: sync against real time

Phase 1–2 measurements show two separate sources of error in a "rewrap at 30 fps" approach:

| Source | Size | Affects |
|---|---|---|
| **Sensor rate**: the cameras really run at 30.011 fps (33.32 ms frames; 19.9596 s for 600 frames in S1). Stamping the frames as exactly 30.000 fps stretches the video. | About 1.3 s per hour (one frame every 80 s), the same on every camera | Video against audio and real time. Camera against camera is fine, because all drift equally. |
| **Dropped frames**: stalls of 67–933 ms. Constant-rate stamping shifts every later frame earlier by the gap. | 0.03% of frames in Phase 2, on most cameras per 30-minute take | That camera against everything, from the stall on |

**User input (2026-11-14): 30.011 fps is fine if the editing software and YouTube are OK with it, but the video must sync against the audio.** Audio sync is the hard requirement. YouTube only sees the edited export, at 30 fps, so it's unaffected. That leaves two candidate paths, and spike S1 decides between them:

- **Path A, native timing (preferred if the editor accepts it):** `-c copy` into `.mov`, with each frame stamped at its real time from `video.pts` (via `mkvmerge --timestamps`). There's no re-encode, so it's fast and lossless. The file runs at about 30.011 fps, and a dropped frame becomes one long frame, which makes it variable frame rate. An editor conforms clips to a 30 fps timeline, so sync with audio should hold. The risk is editing software's trouble with VFR files.
- **Path B, conform:** each camera goes onto an exact 30.000 fps grid by timestamp. This fixes both sources of error but needs a re-encode.

Path B in detail: **conform each camera onto an exact 30.000 fps grid by timestamp.** Each output frame shows whichever input frame was on screen at that wall-clock instant. A gap repeats the frame before it, and the rate difference repeats one frame about every 80 s. The grid is anchored to time of day (frame k at `HH:MM:SS:FF`), so every camera lands on the same grid, and the timecode is simply the time of day. That also gets Phase 4's LTC almost for free.

Conforming means **re-encoding** on the hub. The Pi 5 has no hardware H.264 encoder, so it's libx264 on 4 cores. This changes the design's "no re-encode". It follows from the user's "prioritise sync" decision, but costs time and some quality, so spike S2 measures both. Spike S1 checks whether the sensor can run close enough to 30.000 to avoid re-encoding gapless takes.

## Spike results (2026-11-14)

- **S1: Path B (conform) is the default** (user, 2026-11-14). There was no editing software to test with, and Path B is plain constant 30 fps that any editor handles. A Path A option may follow later.
  - Clap check against DAW audio (`claptest.mp3`, take `20261114-202934`, cam1). Claps were found by audio onset and by video motion peak. Intervals from clap 2 to claps 3 and 4: audio 389.341 / 464.764 s; video wall clock 389.329 / 464.735 s, so **−12 / −29 ms (within 1 frame)**.
  - Naive 30.000 fps stamping would be +126 / +136 ms (about 4 frames) off.
  - The −29 ms may be the audio interface's clock (about 60 ppm). Over 1 hour that's about 6 frames against the DAW whatever the video does, so check a clap at the end of long takes.
- **S2:** libx264 veryfast CRF 16 on the hub runs at about 1× real time per camera (17–19 s per 20 s). That's fine overnight. **Quality (2026-11-15):** take `20261114-195054` cam3, original camera stream against the conformed `.mov` (607 frames, aligned): VMAF mean 96.5 (1% low 96.1, one 3-frame dip to 87 at a sudden change), SSIM 0.995, PSNR 51.5 dB. Visually transparent. Output ~8–9 Mbps against the camera's ~7–16 Mbps.
- **S3:** scp from all 4 Zeros in parallel gave about 4.5 Mbps each, 18 Mbps in total. The Zeros' CPUs were idle, so the limit is Wi-Fi airtime, not SSH encryption. **Keep rsync over SSH**; an rsync daemon wouldn't help. An hour-long session (4 × about 3.5 GB at the measured about 8 Mbps average bitrate) takes about 4 h to pull.
- **Hub access to the cameras (user-approved):** the hub's key `~/.ssh/id_ingest` is authorised on each node with `restrict` and `command="/usr/bin/rrsync -ro /var/lib/camnode/takes"`: read-only rsync of the takes folder, no shell (verified). Ansible roles `ingest_key_hub` and `ingest_key_node`.

## Spikes (as planned)

| | Spike | Question | Done when |
|---|---|---|---|
| **S1** | Editor: native against conformed, **against DAW audio** (user) | One real synced take of about 15 min, with the DAW recording throughout. Clap on camera at the start, middle and end. Make Path A files (native timing, `-c copy`) and Path B files (conformed 30.000, re-encoded), both with time-of-day timecode. If no camera drops a frame naturally, simulate a gap by removing about 10 frames (frames and PTS) from a copy of one camera, clearly marked. In the editor: a 30 fps multicam by timecode from each set, with the DAW audio lined up on the first clap. | **Pass: every angle's clap lines up with the audio clap within 1 frame at start, middle and end.** Path A passes means no re-encode, and S2 is skipped. Otherwise Path B. |
| **S2** | Conform cost (only if Path B) | libx264 speed and quality on the Pi 5 (1 GB RAM) for 1080p30 conform: presets veryfast to medium, CRF 14–18. Files: size, decode in the editor. Temperature. | One preset and CRF are chosen, with real-time factor ≥ 1× (1 hour per hour per camera at most) and no visible loss against the source at 100% |
| **S3** | Pull throughput | rsync over SSH from a Zero (CPU-bound encryption?) against an unencrypted rsync daemon on the LAN (CLAUDE.md asks for this measurement), while the other nodes are idle. | One method is chosen; Mbps per node and for 4 in parallel are recorded |
| **S4** | Exact sensor rate (optional, only if Path A fails on rate rather than gaps) | Can `--framerate` or `FrameDurationLimits` bring the sensor within ±0.002% of 30.000 fps with sync still working? | The real rate is measured over 10 min on 4 synced nodes |

## Design

### Pipeline (hub, per take, per camera)

1. **Pull:** `rsync` from `/var/lib/camnode/takes/<id>/` to the hub's working area (`/var/lib/hubd/ingest/`, on the hub SD: 24 GB free, or on the drive; see the decisions). It resumes partial files and runs one camera at a time, or in parallel per S3. Files: `video.h264`, `video.pts`, `metadata.json`, `manifest.json`.
2. **Check source:** the sha256 of `video.h264` must match the manifest. If the manifest has no sha256 (hashing unfinished), the hub computes it and compares again after a re-pull.
3. **Rewrap (Path A) or conform (Path B), whichever S1 picks.** Path A: `mkvmerge --timestamps 0:video.pts`, then ffmpeg `-c copy` to `.mov` with timecode and metadata. Path B: mux the timestamps (`mkvmerge --timestamps 0:video.pts`; mkvtoolnix is already on the hub). The first frame is placed at its `first_frame_wallclock_ns`. Then ffmpeg `fps=30` with the start snapped to the time-of-day grid, encoded with libx264 (S2's settings). The `.mov` gets the timecode (`-timecode HH:MM:SS:FF`, local time), the camera name and take id in metadata, and `faststart`. If S1 says the sensor rate is close enough, a gapless camera can use `-c copy` instead.
4. **Verify:**
   - frame count = round(duration × 30) ±1;
   - the frames repeated equal the manifest's gaps plus the rate correction (a count, not exact positions);
   - a full decode passes (`ffmpeg -v error -f null`);
   - the start timecode matches the grid.
5. **Write:** to `STUDIO/takes/<YYYY-MM-DD>_<take_id>/<cam>.mov.part`, then fsync and rename to `<cam>.mov`. Next to it: `take.json` (summary, gaps, timecodes, sha256 of each `.mov`) and the camera manifests. A folder is complete when `take.json` is written last (also via `.part`).
6. **Mark verified:** `POST /takes/<id>/verified` on the camera, with the `.mov` sha256 recorded.

### Scheduling

An ingest worker inside hubd (a thread) or as its own systemd service (`hub-ingest`, preferred, so a crash or memory spike can't touch REC). It talks to hubd through a small state file or local API.
- Runs only when the rig is idle and the drive is present. Started by: a schedule, idle for N minutes after the last take, or "Ingest now" in the UI (see decisions).
- **REC or LIVE pauses it at once:** rsync and ffmpeg get SIGSTOP or are killed and resumed, so nothing competes with sync traffic or Wi-Fi. This takes priority over everything.
- Resumable at every step. State is per take and camera: pulled, checked, conformed, verified, written, marked.

### Drive (`STUDIO`)

- Detected by label (`/dev/disk/by-label/STUDIO`, exFAT). Any other drive is ignored.
- On plug-in: `fsck.exfat -n` (check only). If it's dirty, the UI asks before repairing.
- Mounted only while writing (`/mnt/studio`, owned by `studio`), unmounted when idle. Eject unmounts and powers down the drive (`udisksctl power-off`, or a sysfs remove).
- Free-space check before each take; the UI shows hours left on the drive.

### Cameras (camnode additions)

- `POST /takes/<id>/verified`: stores the verified flag and hash in the take folder.
- **Lazy delete:** before arming and every 10 minutes when idle, if free space < threshold, delete the oldest *verified* takes until above it. Unverified takes are never deleted. If space runs short with only unverified takes, REC is refused with a reason, as now.
- The UI shows takes per camera: pending, verified, deleted.
- **Live dropped-frame counter** (added 2026-11-14 after a 20 s test take where cam2 lost 67 frames, transient and unexplained): camnode reads new `metadata.json` frames while recording, and `/status` reports gaps so far. The UI shows it per camera during the take, so a bad take can be redone at once.

### UI

An ingest panel: queue, current step, progress, ETA, errors per take and camera. Drive status (absent / present / mounted / writing / dirty, free space) and an Eject button. Verify results per take (frames, repeats, gaps).

### Tests

Unit tests for timestamp conform maths (grid snapping, repeat counting), the state machine with fake rsync and ffmpeg, the `.part` and rename rules, and the delete policy. Real-hardware tests for S1–S4 and the exit criteria.

## Milestones

| | Delivers |
|---|---|
| S1 | Editor test with the user. Decides Path A or B. |
| S2–S3 | Conform cost (Path B only) and pull throughput, written up in `docs/progress.md`. |
| **M1** | Drive handling: detect, check, mount on demand, Eject. Ansible: mount point, sudo rules, packages pinned. |
| **M2** | Ingest worker: pull, check, conform, verify, write. Pause and resume. Tests. |
| **M3** | camnode: verified flag and lazy delete. UI panels. |
| **M4** | Exit criteria on real hardware, including a 30-minute check in the editor with the user. Write `docs/phase3-results.md`. |

## Addition (2026-11-15): play takes from the drive on the hub page

User request: a quick way to see what's in each take. The user ruled out extra conversion and extra space, so files are served straight from the `STUDIO` drive.
- **Settled rule changed (user OK, 2026-11-15):** the drive is mounted read-write only while writing, and read-only while someone plays a take back. A read-only mount writes nothing and never marks exFAT dirty, so a pull mid-playback is harmless. This keeps the reason for the original rule.
- **hub-ingest owns all mounting.** hubd touches `playback.lease` in the ingest state dir on each media request. A keeper thread in hub-ingest mounts read-only while the lease is fresh (5 min, `playback_idle_s`) and unmounts afterwards. Writes switch to read-write and back under a drive lock. Eject removes the lease.
- **hubd** serves `GET /media/<take_id>/<cam>.mov` with Range support (seeking), as `video/mp4`, and waits up to 8 s for the mount. 503 if the drive isn't available, 404 if the file isn't on it.
- **UI:** ▶ buttons per camera on each take that has a camera written to the drive (disabled without the drive), and a player overlay with camera tabs. Switching camera keeps the position.
- Also fixed: the plug-in fsck is now keyed on the device node's creation time, so a stick pulled without Eject and plugged back in is checked again (it used to be keyed on the device name, which is usually the same).

## Decisions (answered 2026-11-14)

Answers:
1. A USB stick labelled `STUDIO` for now.
2. Re-encode only if S1 picks Path B.
3. Ingest starts automatically after 30 min idle, plus an "Ingest now" button.
4. Working space on the hub SD, one take at a time.
5. Cameras delete the oldest verified takes below 10 GB free.
6. Samba share and Mac auto-pull: dropped at 1.0.
   - **Share:** Samba on the hub, read-only, the `STUDIO` drive's `takes/` folder, shown in Finder as `studio-hub` (Bonjour via avahi). Read-only keeps "the hub never deletes from the drive", and nothing can be written from the network.
   - **Conflicts with a settled decision:** the drive is "mounted only while writing". A share needs it mounted while browsing. Options: mount on demand when the share connects, or a "Share drive" toggle in the UI that keeps it mounted until Eject (simplest). Needs the user's OK.
   - **Speed:** the hub is on wired Gigabit, so it's limited by the USB stick or the Mac's Wi-Fi, typically 30–100 MB/s. One hour of 4 cameras (40–55 GB) is about 10–30 min.
   - **Ingest must not write while a copy is in progress, or the reverse:** only finished `.mov` files (never `.part`) are visible; hide `.part` with Samba `veto files`.
   - **Mac auto-pull** (`mac/`, launchd): rsync of new complete take folders (those with `take.json`) when the Mac is on the studio network. Builds on the share or on rsync over SSH.

Original questions:

1. **The drive:** which drive and what size? Ingest can't be tested without one. A USB stick labelled `STUDIO` is enough for M1–M2. At roughly 20–30 Mbps after conform, one hour of 4 cameras is about 40–55 GB.
2. **Re-encode, only if S1 picks Path B:** OK to conform with libx264 at high quality (target CRF about 16, settings from S2), giving larger files than the camera's 16 Mbps? The alternative is ProRes 422 LT: easier to edit, but about 100 Mbps, so roughly 45 GB per hour per camera.
3. **When ingest runs:** automatically once the rig has been idle for 30 min, plus an "Ingest now" button (recommended); or only at a fixed overnight time; or only by button.
4. **Working space:** conform from the hub's SD card (24 GB free; one camera-hour of source is about 7 GB) or directly on the `STUDIO` drive (it would then be mounted for longer). Recommended: the hub SD, one take at a time.
5. **Camera delete threshold:** free space below 10 GB (about 1.4 h of recording at 16 Mbps), oldest verified first (recommended)?
6. **Samba share and Mac auto-pull:** defer to later (recommended), or include in Phase 3?
