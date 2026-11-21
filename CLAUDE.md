# Studio Multicam

Synchronised multi-camera capture for a home music studio: Raspberry Pi Zero 2 W + Camera Module 3 Wide camera nodes, a Raspberry Pi 5 hub, OBS for live, and our own AI-assisted editor for editing (planned; user decision 2026-11-15, see `docs/design.md`, "Editing").

The full design is in `docs/design.md`. Read it before proposing any architectural change.

## Current phase

**Phone cameras first: plan accepted 2026-11-15 (`docs/phone-plan.md`), after the spike's GO (`docs/phone-spike-results.md`). P1–P5 are built and deployed (2026-11-15); left: robustness tests on the rig and the ingest-time question. Then Phase 4 (plan accepted 2026-11-15, `docs/phase4-plan.md`): Track B (visuals), then A (audio sync), then C (polish). Editor plan accepted 2026-11-16 (`docs/editor-plan.md`): spikes E1–E3 alongside Track B, the main work after Track A.** Phase 3 is done: GO on 2026-11-15 (`docs/phase3-results.md`). Stay within the plans; ask before adding scope.

Earlier phases: Phase 1 GO on 2026-11-14 (`docs/phase1-results.md`); its five conditions still apply. Phase 2 passed on 2026-11-14 (`docs/phase2-results.md`). Phase 3 scope, kept for reference:
- pull takes from the nodes;
- conform or rewrap to `.mov` with timecode, keeping sync (see Ingest below);
- verify, and lazy delete on the nodes;
- `STUDIO` drive handling (mount on demand, Eject);
- optional network share and Mac auto-pull.

Start by writing a Phase 3 plan in `docs/phase3-plan.md` and get my OK before building. Don't write Phase 4 code until I say so.

## Settled decisions (ask before changing any of these)

- **Hosts:** `studio-hub` (Pi 5, wired Ethernet). Nodes `cam1`–`cam4` now, possibly scaling to `cam6`. SSH user `studio`. All reachable as `<name>.local`.
- **Video:** 1080p30 H.264, the hardware encoder's ceiling. Shutter locked at 1/50 s (20000 µs). Gain and AWB gains are fixed for each take: set by calibration (show each device the calibration card while idle; it locks on its own), by hand, or as a fallback chosen automatically at REC ("auto, then lock"), and never changed during a take. Focus: the Pis are fixed on the wall with one fixed focus each (no autofocus); phones are marked tripod (focus locked) or handheld (continuous autofocus) (user decisions 2026-11-15 and 2026-11-16, Phase 4, `docs/phase4-plan.md` Track B).
- **Live mode:** MediaMTX on each node with its built-in Raspberry Pi camera source. OBS pulls SRT (fallback RTSP over TCP) at 4–5 Mbps per camera, or 3–4 at six cameras. Pis do not record in live mode; ISOs are recorded in OBS.
- **Offline mode:** `rpicam-vid` software camera sync, with `--sync client` on all nodes but one, started first, then `--sync server` on the last. Raw H.264 plus `--save-pts`, about 16 Mbps, to SD. `--denoise cdn_off` (the default `cdn_fast` drops ~6% of frames on the Zero 2 W at normal clocks; see `docs/progress.md`).
- **Clocks:** chrony on every node; the hub is the NTP server.
- **Ingest:** the hub pulls takes only when no take or live session is active (typically overnight). It produces `.mov` files and stamps a shared start timecode. **Sync takes priority over `-c copy`** (user decision 2026-11-14): cameras are aligned by each take's first-frame wall clock (`first_frame_wallclock_ns` in the manifest), and dropped frames (the manifest's `gaps`) must not shift later frames, even if that means re-encoding. The sensor really runs at about 30.011 fps, so stamping frames as exactly 30 fps drifts about 1.3 s per hour against audio. The user accepts native 30.011 fps if the editor and YouTube handle it. So the preferred path is `-c copy` with each frame stamped at its real time (a gap becomes one long frame), and conforming by timestamp onto an exact 30 fps grid (re-encode) is the fallback. Phase 3 spike S1 decided between them (conform, at the time). What's ruled out is stamping frames as exactly 30.000 fps without fixing the timing. Frame counts are verified against the manifest, gaps included. Nodes delete a take as soon as the hub has verified it on the STUDIO drive (user decision 2026-11-17; before that they kept it until they needed the space), and never before. *(2026-11-16, user decision, `docs/editor-plan.md`: the editor is our own. Ingest switches to `-c copy` with every frame at its real time once spike E1 passes; the render of the final cut does the conform onto the exact 30 fps grid, locked to the bounce. **E1 passed 2026-11-16 (`docs/editor-spikes.md`); ingest 0.4.0 copies at real timing (`video_timing: real`). The conform stays available as `video_timing: conform`.**)*
- **Storage:** a portable exFAT drive labelled `STUDIO`, plugged into the hub and swapped to the Mac for editing.
  - The hub mounts it read-write only while writing, and read-only while someone plays a take back on the hub page (unmounted 5 min after the last playback; user decision 2026-11-15). Otherwise it's unmounted. It provides an Eject action.
  - It writes to temporary names and renames when done, and checks the filesystem on plug-in.
  - The hub never deletes from the drive.
- **Phone cameras** (user decisions 2026-11-15, `docs/phone-plan.md`): Pixel phones join offline takes from a page on the hub (browser, no app; camera access through a one-off Chrome flag, no HTTPS). Ready phones record on the hub's REC/STOP; phones never block REC. Chunks upload live to the hub's SD card during the take. One capture-delay correction per model (default −40 ms); one tested phone stands for all, so no per-phone tests. Dropped frames, on phones and Pi cameras alike, are tolerated: marked as rough sections, not fixed. *(2026-11-21, user: phones are known by a device id kept in Chrome (`phone-k7q2`), get a colour from the hub, and are named on the hub page; a tripod page `/phone/tripod` for the studio's own phones in brackets: dark, nothing to press, set up from the hub page.)*
- **Editing** (user decision 2026-11-15): our own editor. The plan is a custom editor with an agent that listens to the music, analyses the video and proposes cut points, plus a simple timeline UI to pick which camera is on at each point. Everything the rig produces (timecode, manifests, `gaps`, rough sections, bounce sync) is data for that editor; nothing targets a commercial editor's formats. Plan: `docs/editor-plan.md` (accepted 2026-11-16): web editor on the hub, rule-based automatic cut first (agent later), one look per song (filters and effects with a strength, Instagram-style; camera matching automatic from the card readings), a short dissolve at every cut, hub renders a YouTube master; a vertical story version is a maybe.
- **YouTube** (user decision 2026-11-18, `docs/youtube-plan.md`): finished videos marked "YouTube" in the editor upload to the band's channel as **private** videos, one after another as they're rendered (hub-upload; resumable; pauses while recording). Upload-only login, made on the Mac, never in git. The STUDIO drive keeps the master.
- **Audio sync:** a clap or slate by default. LTC from the hub is Phase 4 and optional.
- **Network:** cameras on their own 2.4 GHz channel. Wi-Fi power saving off on the nodes.

## Hardware constraints

- **Zero 2 W:** 4× Cortex-A53 at 1 GHz, 512 MB RAM, 2.4 GHz-only Wi-Fi, one USB 2.0 OTG port. Keep CPU-heavy work off the nodes: no software encoding, no heavy denoise.
- **Camera Module 3 Wide:** 1080p is scaled from the binned 2304×1296 mode (56 fps max), which keeps the full field of view.
- **Thermals:** throttling starts at 80 °C. Log `vcgencmd measure_temp` and `vcgencmd get_throttled` during long runs.
- **Bulk transfers:** SSH encryption may be CPU-bound on the Zero. Measure rsync-over-SSH throughput; switch to an unencrypted rsync daemon on the LAN if it is.

## Repository layout

- `node/`: camera agent, MediaMTX config template, systemd units
- `hub/`: control API and web UI, ingest worker, chrony config
- `mac/`: optional auto-pull script
- `deploy/`: Ansible inventory and playbooks (run from the dev machine)
- `docs/`: design, phase results, progress log, runbook

## Stack

Python 3 from Raspberry Pi OS, a lightweight HTTP service for the node agent and hub API, plain HTML and JS for the hub UI (no build step), systemd for services, Ansible for provisioning.

The editor (user decision 2026-11-16, `docs/editor-plan.md`) and, since 2026-11-17, the hub page (user decision: "production grade", same stack as the editor) use Preact with `htm`: copied into the repo and pinned like mp4box.js, still no build step. Both share one design system in `hub/hubd/static/shared/` (tokens, fonts, Preact/htm, `html`, icons; imported as `shared/…` through the import map), and type checking with JSDoc and `tsc --checkJs` on the Mac. The hub page is `static/hub/` (served at `/`, with deck, monitor, focus and archive views; `/snap` opens focus). The phone page is still plain JS.

## Working rules

- Ask before rebooting, reflashing, or deleting anything on a Pi. Installing packages is fine.
- Keep command output short: tail and grep logs instead of dumping them.
- Once something works, pin the package versions and record them in `docs/versions.md`.
- At the end of each session, update `docs/progress.md` with what changed and what's next. That file is the memory between sessions.
