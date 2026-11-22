# Phase 2 plan: control

> **Closed at 1.0 (2026-11-22).** This is a record of the work as planned, kept for reference. Nothing in it is still to do: see [release-1.0.md](release-1.0.md).

Status: **complete. All exit criteria pass (`docs/phase2-results.md`, 2026-11-14).** Written 2026-11-14 after Phase 1 GO (`docs/phase1-results.md`).

## Goal

Press REC on the hub's web page, get a synced take on every camera's SD card, each with a correct manifest. Press LIVE and every camera appears in OBS. Every host is provisioned from Ansible, with nothing set up by hand.

**Exit criteria:**
1. From a clean `ansible-playbook site.yml`, the hosts end up in the documented state. A second run reports 0 changes.
2. From the hub page, three takes (5, 5 and 30 min) on cam1–cam4 succeed. Each node writes a manifest with a first-frame wall-clock time, a frame count, a PTS gap list and an exit status, and the manifests agree with a stopwatch check.
3. LIVE on/off from the hub page brings all four cameras into OBS over SRT, and back off.
4. REC is refused, with a reason, when a camera is offline, unsynced, hot, low on space or in the wrong mode.
5. The agents and the hub survive a node reboot and a hub reboot, recovering by themselves. Each reboot is done with the user's OK.

**Out of scope:** ingest, rewrap, timecode stamping, deleting takes and drive handling (all Phase 3). Image-profile UI, snapshots for calibration, tally LEDs, the physical button and LTC (Phase 4).

## The five Phase 1 conditions

| Condition | Where Phase 2 handles it |
|---|---|
| 1. Per-node start time | Spike S1 finds a reliable first-frame wall-clock source. The agent writes it to the manifest. Phase 3 uses it for timecode. |
| 2. Flag PTS gaps | The agent computes gaps from `--save-pts` at STOP and puts them in the manifest. The UI shows dropped frames per camera. (Dedicated 2.4 GHz channel: the user's router, listed under decisions.) |
| 3. `rpicam-vid` exit crash | Spike S2 picks a stop method: `-t 0` plus a signal instead of `-t`, measured over 50 or more starts. The agent checks the exit code and PTS completeness, and rebuilds PTS from the stream if it's missing. |
| 4. Provision with Ansible | Milestone M1. Everything done by hand in Phase 1 becomes roles, pinned to `docs/versions.md` and held with `apt-mark hold`. |
| 5. Calibrate image settings | Per-camera settings (gain, AWB gains, lens position) go in Ansible `host_vars`, one place feeding both the agent and MediaMTX. Calibrating with a grey card is a user session at any time; the UI for it is Phase 4. |

## Components

### Ansible (`deploy/`, run from the Mac)

- **Inventory:** `hub` (studio-hub) and `nodes` (cam1–cam4, with cam5 and cam6 ready to uncomment).
- **`host_vars/camN.yml`:** shutter, gain, AWB gains and lens position.
- **Roles:**
  - `common`: pinned packages and holds, persistent journal (capped at 50 MB, so unexpected reboots leave logs), timezone.
  - `chrony_server` (hub) and `chrony_client` (nodes): the Phase 1 configs, with DHCP sources disabled on the nodes.
  - `node_net`: the Wi-Fi power-save-off config and `iw`.
  - `mediamtx`: v1.21.3 to `/opt/mediamtx`, with the sha256 checked; `mediamtx.service` is installed but not enabled at boot, because live mode is started by the agent.
  - `camnode`: the agent, its systemd unit, `/var/lib/camnode/`, and a sudoers rule limited to starting and stopping `mediamtx.service`.
  - `hubd`: the hub service, its UI, and `/var/lib/hubd/`.
- **Check:** the playbook asserts `iw … get power_save` is off, chrony is synced to the hub, and the services are active.

### Node agent `camnode` (`node/camnode/`)

Python standard library only (`http.server`, `subprocess`, `json`), run as user `studio` by a systemd unit with `Restart=on-failure`. It serves a JSON API on port 8080:

| Endpoint | Does |
|---|---|
| `GET /status` | Mode (idle, live, armed, recording), temperature, throttle flags, free space, chrony offset and sync state, Wi-Fi signal and power-save state, the current take with elapsed time and frames so far, and the agent version |
| `POST /live/start`, `/live/stop` | Starts or stops `mediamtx.service`. Refused while recording. |
| `POST /take/arm` | Body: take id, role (client or server), settings. Starts `rpicam-vid --sync <role>` writing to `/var/lib/camnode/takes/<id>/`. Returns at once; status shows `armed` until "Sync achieved", then `recording`. |
| `POST /take/stop` | Stops `rpicam-vid` with the method from S2, waits for it to exit, then writes `manifest.json` |
| `GET /takes` | Takes on the card, with their manifests (read-only; deleting is Phase 3) |

**Manifest:** camera, take id, sync role, settings, first-frame wall-clock time (from S1), frame count, PTS gap list, exit code, whether PTS was complete or rebuilt, and file sizes. A sha256 is computed in the background after STOP, because hashing 4 GB on a Zero takes minutes.

The camera is owned by one thing at a time. The agent refuses to arm while MediaMTX runs, and the hub stops live mode first.

### Hub service `hubd` (`hub/hubd/`)

Python standard library, a systemd unit, and port 8000 (or 80, to be decided). It serves the UI's static files and an API:

- It polls every node's `/status` every 2 s and keeps a live picture of the rig.
- **REC:**
  1. Run the arm checks: online, chrony offset under 5 ms, temperature under 70 °C, free space for at least 1 h, power save off, not throttled. If any fail, refuse and say which camera and why.
  2. Stop live mode on the nodes.
  3. Pick the sync server: default cam1, otherwise the first healthy node.
  4. Arm the clients, then the server.
  5. Wait for every node to report `recording`. On a timeout, stop all and report.
- **STOP:** stop every node in parallel, collect the manifests, and show a take summary: duration, frames per camera, gaps, start-time spread.
- **LIVE on/off:** starts or stops MediaMTX on all nodes, or on selected ones.
- A take log in `/var/lib/hubd/takes.json`.
- No authentication: LAN only, as in the design. It binds to the LAN interface.

### Web UI (`hub/hubd/static/`)

A single page of plain HTML and JS with no build step:
- one card per camera: state, temperature, free space and hours left, clock offset, Wi-Fi signal and drops;
- big REC/STOP and LIVE controls;
- an OBS URL with a copy button for each camera;
- the last take's summary.

## Milestones

| | Milestone | Delivers |
|---|---|---|
| **S1** | Spike: first-frame wall clock | Check whether `rpicam-vid --metadata` exposes `FrameWallClock` or `SensorTimestamp` per frame. If only the sensor timestamp, convert it via `CLOCK_BOOTTIME`. Validate against the stopwatch across 4 nodes, then write up in `docs/progress.md`. |
| **S2** | Spike: stop method and crash | Compare 50 short runs each of `-t N` against `-t 0` stopped by SIGINT (and the `--signal` mode). Measure the abort rate and PTS completeness, and choose one. |
| S3 | Optional spike | A `--sync off` run under 30 Mbps load, to find out whether the client stalls come from sync handling. Only if the user wants it. |
| **M1** | Ansible baseline | Roles reproduce today's hand-made state. The hosts converge with 0 changes on a second run. Package holds are in place. |
| **M2** | camnode | The agent and its unit, deployed by Ansible. Tests (`pytest`, run on the Mac) for PTS gap parsing, manifest writing, and the process state machine with a fake `rpicam-vid`. |
| **M3** | hubd and UI | The API, arm checks, orchestration and UI. Tests against fake nodes. |
| **M4** | Acceptance | The exit criteria above on real hardware, written up in `docs/phase2-results.md`. |

Order: S1 and S2 first (they shape the agent), then M1, M2, M3 and M4. Each milestone ends with a `docs/progress.md` update.

## Decisions (answered 2026-11-14)

Answers: 1 passwordless sudo; 2 port 80; 3 cam1 with fallback; 4 `/var/lib/camnode/takes`; 5 still the user's job on the router; 6 skip S3.

Original questions:

1. **Sudo for Ansible.** Either passwordless sudo for `studio` on all five hosts (recommended: needed for unattended runs, and it avoids passwords in files), or typing the password each run with `--ask-become-pass`.
2. **Hub UI port:** 80 (just `http://studio-hub.local`) or 8000. Recommended: 80.
3. **Default sync server:** cam1 (recommended), with automatic fallback.
4. **Take storage on the nodes:** `/var/lib/camnode/takes` (recommended) rather than the home directory.
5. **Router (the user's job):** DHCP reservations for studio-hub and cam1–cam4, and ideally a dedicated 2.4 GHz channel (1, 6 or 11) for the cameras.
6. **S3** (the stall-cause spike): yes or no.
