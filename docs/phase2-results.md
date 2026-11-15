# Phase 2 results: control

Date: 2026-11-14. Plan: `docs/phase2-plan.md`. Detailed log: `docs/progress.md`.

**Verdict: all five exit criteria pass. One open issue: occasional frame stalls (0.03% of frames). Every stall is detected and reported, and the manifest timing stays exact.**

## Exit criteria

| # | Criterion | Result |
|---|---|---|
| 1 | A clean `ansible-playbook site.yml` gives the documented state, and a second run reports 0 changes | **Pass.** The second run reported `changed=0` on all five hosts, both after the first apply and after the final deploy. |
| 2 | Three takes from the hub (5, 5 and 30 min) on cam1–cam4, with manifests that agree with a stopwatch | **Pass, with drops noted.** See [Takes](#takes). Same-instant frames, picked by manifest wall clock, show identical stopwatch readings on all four cameras at all 8 instants checked. |
| 3 | LIVE on/off brings all four cameras into OBS over SRT, and back off | **Pass.** The user confirmed all four in OBS. LIVE on now returns only once every stream is publishing (3.6 s). |
| 4 | REC is refused, with a reason, when a camera is offline, unsynced, hot, low on space or in the wrong mode | **Pass.** With camnode stopped on cam4, REC returned HTTP 409 `cam4 offline` and armed nothing. The other reasons are covered by unit tests. |
| 5 | Agents and hub survive node and hub reboots, recovering by themselves | **Pass.** All five hosts were rebooted at once (with the user's OK). All were back in 72 s with every service up, chrony locked, power save off and holds kept, and the hub saw all four cameras healthy. A hubd restart mid-take adopts the running take and stops it cleanly. |

## Takes

All three were started and stopped through the hub API, which is the same code path as the UI buttons. cam1 was the sync server.

| Take | Length | Start spread | Sync error per client | Frames per camera (cam1/2/3/4) | Dropped frames | Exit codes |
|---|---|---|---|---|---|---|
| 1 `20261114-182242` | 302 s | 23 µs | −12 to +11 µs | 9044 / 9070 / 9069 / 9070 | cam1 27 (one 933 ms stall), cam3 1 | all 0 |
| 2 `20261114-183258` | 301 s | 32 µs | 1 to 32 µs | 9038 / 9034 / 9038 / 9038 | cam2 4 (one 167 ms stall) | all 0 |
| 3 `20261114-184358` | 1801 s | 17 µs | 4 to 21 µs | 54041 / 54004 / 54030 / 54041 | cam1 3, cam2 40, cam3 14, cam4 3 | all 0 |

- In every take, every PTS file came from `--save-pts`, so no rebuild was needed. The sha256 was computed after STOP.
- Peak temperatures were 42–43.5 °C, with no throttling.

### Stopwatch check

Method: for each instant, each camera's frame is chosen by `FrameWallClock` (nearest frame, within 0.1 ms across cameras). That frame is cut on the node with `node/spike/cutframes.py`, decoded on the Mac, and the four are tiled 2×2.

| Take, instant | cam1 | cam2 | cam3 | cam4 |
|---|---|---|---|---|
| 1, 30 s | 2:38:26.97 | same | same | same |
| 1, 150 s | 2:40:26.98 | same | same | same |
| 1, 280 s (after cam1's 933 ms stall) | 2:42:36.95 | same | same | same |
| 2, 60 s and 250 s (250 s is after cam2's stall) | identical on all four, 2:52:23.15 at 250 s | | | |
| 3, 60, 900 and 1750 s | identical on all four: 3:14:12.94 and 3:28:22.93 | | | |

- Between take 3's 900 s and 1750 s readings the stopwatch advanced 849.99 s, for 850 s requested.
- **Conclusion:** the manifest's first-frame time and the per-frame wall clocks are correct across cameras and stay correct through drops. Aligning by wall clock, not by frame number, is right.

## What was built

- **`deploy/`:** Ansible on the Mac (ansible-core 2.20.11, Python 3.14). `bootstrap.yml` sets up passwordless sudo. `site.yml` has the roles `common` (pins and holds, persistent journal, timezone), `chrony_server` and `chrony_client`, `node_net`, `mediamtx`, `camnode` and `hubd`, and ends with checks.
- **`node/camnode/`:** the camera agent (stdlib, port 8080). 17 tests, using a fake `rpicam-vid`.
- **`hub/hubd/`:** the control service and UI (stdlib, port 80). 12 tests, using a fake rig.
- **Templates:** `node/mediamtx/mediamtx.yml.j2`, and systemd units in `node/systemd/` and `hub/systemd/`.

## Findings

1. **First-frame time (S1):** `rpicam-vid --metadata` gives `FrameWallClock` for every frame. PTS can be rebuilt from `SensorTimestamp` if the exit crash loses it.
2. **Stop method (S2):** SIGINT, 200/200 clean. The exit crash is now rare: 1 in 600 runs, and only with `--signal`.
3. **A sync client can start late while reporting "Sync achieved":** up to 969 ms, on cam4, 6 of 150 test takes. `Difference` in the log equals the real offset. camnode records it, and hubd warns right after REC. It didn't occur in the acceptance takes; the largest error there was 32 µs.
4. **MediaMTX v1.21.3:** MoQ is on by default and hangs shutdown under systemd, so `moq: false` is required. The local API is now on, at 127.0.0.1 only.
5. **Frame stalls (open):** 60 of 216,116 frames in take 3 (0.03%), in single stalls of 67–933 ms.
   - The server stalls too, unlike Phase 1.
   - Stalls on different cameras cluster in time (within 2–9 s), which suggests a shared cause.
   - They don't line up with Wi-Fi transmit failures on the stalling node.
   - Phase 1's 62-minute run had 0 drops with the same capture settings. New since then: `--metadata`, and the hub's 2 s polling (which runs `vcgencmd`, `chronyc` and `iw` on each node).
   - Every stall is reported in the manifest and the take summary, and timing stays exact.

## Carried into Phase 3 (needs the user's decision)

- **Rewrap must respect the PTS.** CLAUDE.md says to rewrap with `ffmpeg -c copy` at 30 fps. With a dropped frame, a constant 30 fps stamp shifts every later frame earlier by the gap, so that camera drifts out of sync for the rest of the take. Options:
  - mux with the real timestamps (`mkvmerge --timestamps` or ffmpeg with a timestamp file, then `.mov`);
  - fill each gap with repeated frames, which needs re-encoding of those GOPs and so is not `-c copy`.

  **Decided by the user (2026-11-14): prioritise sync.** CLAUDE.md's Ingest decision is updated to match. The method is for the Phase 3 plan.
- Ingest must align cameras by `first_frame_wallclock_ns`, not frame number, and allow for frame counts that differ.

## Next steps (suggested)

1. ~~Isolate the stalls with an A/B test~~. **The user declined (2026-11-14).** Stalls are accepted as they are: detected, reported, and handled at ingest.
2. Router: DHCP reservations, and a dedicated 2.4 GHz channel for the cameras.
3. Grey-card calibration into `deploy/host_vars/`.
4. Phase 3 (ingest) when the user says so.
