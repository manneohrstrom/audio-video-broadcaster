# Progress log

## 2026-11-14 — Setup before Phase 1

### What changed
- All five hosts (`studio-hub`, `cam1`–`cam4`) apt-upgraded and rebooted; 0 upgrades pending afterwards.
- iperf3 `3.18-2+deb13u2` installed on all five; `iperf3` service is disabled everywhere.
- ffmpeg `7.1.5-0+deb13u1+rpt2` installed on `studio-hub` (done by hand: `sudo` on the hub asks for a password, so it can't be run over non-interactive SSH).
- CLAUDE.md corrected: the hub is a **Raspberry Pi 5 Model B Rev 1.1**, not a Pi 4.

### Device state
| Host | Model | Kernel | Notes |
|---|---|---|---|
| studio-hub | Pi 5 Model B Rev 1.1 | 6.18.60+rpt-rpi-2712 | 3 A supply detected, USB capped at 600 mA total; 5.12 V, no throttling |
| cam1–cam4 | Zero 2 W Rev 1.0 | 6.18.60+rpt-rpi-v8 | imx708_wide detected; 2304×1296 @ 56.03 fps mode present; rpicam-apps 1.15.0-1 has `--sync off/server/client`; idle 32–42 °C |

### Pi 5 hub: implications
- **USB power:** with a 3 A supply the Pi 5 limits total USB current to 600 mA. The `STUDIO` drive will have **its own power supply**, so this is fine. Keep it that way, or switch to the 27 W supply for a bus-powered drive.
- **No 3.5 mm audio jack:** LTC from the hub (Phase 4) would need a USB audio interface.
- **Runs hotter:** use an active cooler, and log hub temperature during long runs and ingest.
- **No hardware H.264 encoder:** this doesn't matter because the hub only rewraps (`-c copy`). Any future proxy or transcode work on the hub would run in software.
- **Built-in RTC:** with a backup battery fitted, the hub keeps time through power cuts. That's useful because it's the NTP server.

### Open items
- `sudo` on the hub needs a password. Passwordless sudo for `studio` would let Claude run admin commands there; the user hasn't decided.
- The `STUDIO` drive isn't plugged in yet. When it is, check it mounts as exFAT with that label and watch dmesg during a test write.
- `docs/design.md` doesn't exist in the repo yet.
- `docs/versions.md` isn't created yet; pin versions once things work.

### 10 s test recordings (Phase 1 step 1)
Command used on each node:
`rpicam-vid -n -t 10000 --mode 2304:1296:10:P --width 1920 --height 1080 --framerate 30 --codec h264 --bitrate 16000000 --shutter 20000 --gain 2 --awbgains 1.5,1.5 --autofocus-mode manual --lens-position 1 --save-pts test10s.pts -o test10s.h264`
(gain, AWB gains and lens position are placeholders, not tuned yet).
- All four clips are H.264 High, 1920×1080, and decode with no errors. Files are about 8.5–10 MB, so roughly 7 Mbps of the 16 Mbps cap on this scene.
- **First run, cam1 and cam4: `rpicam-vid` aborted (exit 134).** cam4's PTS file was empty. Nothing appeared in dmesg, and the crash didn't happen when re-run. The abort message wasn't captured.
- **Intermittent dropped frames:** a dropped frame shows as a 66.6 ms PTS gap, and there were 0–12 per 10 s. The first 4–5 s are always clean; after that, frames drop every 0.4–0.9 s. CPU is about 95% idle and nothing is throttled.
  - On cam2, recording to RAM gave 5 gaps, to `/dev/null` 7, and to SD 0. So **the SD card isn't the cause**, and the rate varies from run to run.
- This has to be resolved before the 60-minute sync test, because drops would invalidate the frame-offset readings.

### Frame-drop diagnosis: cause found
These were 60 s runs, one variant per camera, written to SD (1800 frames expected):
| Variant | Drops |
|---|---|
| baseline (as above) | 63 |
| `--buffer-count 12` | 142 |
| no `--mode` / AF options | 123 |
| no `--mode` + `--buffer-count 12` | 130 |
| `--profile baseline` | 130 |
| **`--denoise cdn_off`** | **0** |

- Confirmed with 120 s `--denoise cdn_off` runs on all four cameras: 3596/3600 frames each, 0 gaps. The 4 missing frames are at startup, before the first saved PTS.
- During the runs the CPU sat at 600–700 MHz and the GPU core mostly at 320 MHz, with no throttling. The default colour denoise (`cdn_fast` for video) evidently can't keep up on the Zero 2 W. That fits the "no heavy denoise" rule.
- The `rpicam-vid` abort (exit 134) hasn't happened again in 14 more runs. Watch for it in long runs.
- The CPU governor wasn't tested, and isn't needed for the fix.

### Capture matrix (60 s, all four cameras per setup, 1800 frames expected)
"Normal" CPU speed means the `ondemand` governor; "full" means `performance`, set temporarily in sysfs and restored afterwards.
| Denoise | CPU speed | Bitrate cap | Drops per camera | Actual Mbps | Verdict |
|---|---|---|---|---|---|
| `cdn_off` (120 s runs) | normal | 16M | 0 / 0 / 0 / 0 | — | ✅ |
| `off` | normal | 16M | 0 / 0 / 0 / 0 | 10–11 | ✅ but noisier, so more bits |
| `cdn_fast` | **full** | 16M | 0 / 0 / 0 / 1 | 7–8.5 | ✅ little margin |
| `cdn_fast` (default) | normal | 16M | 104–129 | 7–9 | ❌ |
| `cdn_fast` | normal | 8M | 97–113 | 7.5 | ❌ bitrate isn't the bottleneck |
| `cdn_hq` | normal | 16M | ~828 (46%) | 4 | ❌ |
| `cdn_hq` | full | 16M | ~800 | 5 | ❌ |
- With the `performance` governor, the GPU core clock sits at 400 MHz the whole run. With `ondemand`, it's at 400 MHz only 1–13% of the time. The ISP and encoder are clocked from the core, which explains why `cdn_fast` keeps up only at full speed.
- Peak temperatures over 60 s were 35–49 °C. Full speed added roughly 3 °C.
- **The `rpicam-vid` abort is a heap corruption at exit** (`double free or corruption (fasttop)`, exit 134). It happened after "Halting: reached timeout", in 3 of about 40 runs. The video survives, but the first occurrence left an empty `--save-pts` file. This needs a workaround or an upstream report before production.
- Picture: the placeholder AWB gives a green cast. At this lighting, the denoise modes look nearly the same at 100% crop.

### Clocks: chrony set up (replaced systemd-timesyncd)
- Hub: chrony 4.6.1-3+deb13u2 takes time from the Debian pool and serves the LAN. The config is `hub/chrony/studio-hub.conf`, installed to `/etc/chrony/conf.d/`.
- Nodes: the stock `pool` line in `/etc/chrony/chrony.conf` is commented out. The only source is `studio-hub.local` with minpoll 4 / maxpoll 6, and steps happen only in the first 3 updates. The config is `node/chrony/studio-node.conf`.
- After 10 minutes, all four nodes were locked to the hub, but offsets jitter ±0.1–2.5 ms, with RMS 0.9–3.9 ms. That's because the hub is on Wi-Fi (see below). It's well under one frame (33 ms).
- Not in Ansible yet: this was installed by hand over SSH.

### Network findings (conflict with CLAUDE.md)
- **The hub isn't wired.** `eth0` has no carrier, and the hub is on 5 GHz Wi-Fi (channel 40, signal 46%, 192.168.68.80/22). CLAUDE.md says wired Ethernet. This needs fixing before the throughput test (step 4) and to tighten NTP.
- The cameras are on 2.4 GHz channel 7 of the home network "Miles Smiles" (signal 50–59%), at .81–.84. Whether channel 7 is free of other traffic hasn't been checked.

### Step 2 started: 60-minute sync drift test
- Started 2026-11-14 15:15:30. Clients ran on cam2–cam4, then the server on cam1, using `node/spike/synctest.sh`: the offline settings with `--denoise cdn_off`, recording for 62 min.
- All four logged `Sync achieved` (differences 0 to −13 µs).
- Output is in `~/synctest/` on each node: `<host>.h264/.pts/.log`, plus `<host>.mon.csv` with temperature, throttle flags and chrony offset every minute. The hub logs its own `.mon.csv` too.
- `--save-pts` is only written when `rpicam-vid` exits, so the exit crash would lose it.

### Step 2 + 3 results (run 15:15–16:18)
- All four exited cleanly, with no abort, and every PTS file was written. **0 dropped frames** in 62 min on every camera.
- Frame counts: cam1, cam2 and cam4 had 111639; **cam3 had 111640**.
- Thermals (step 3): peaks were cam1 40.2, cam2 40.2, cam3 41.9, cam4 39.7 and hub 43.9 °C. **No throttle flags** anywhere.
- Stopwatch (a laptop; `node/spike/cutframes.py` cuts snippets at exact frame numbers on the node, so nothing large is copied):
  - The laptop screen was **blank at frames 150 and 27000** (0 and 15 min), so they can't be read. The user also reset the timer at about 29 min.
  - Readable at frames 45000, 55800 and 110700 (25, 31 and 61.5 min). cam1, cam2 and cam4 show **identical readings** at every point.
  - **cam3 shows, at frame N, what the others show at N−1, at all three points.** An 8-frame digit strip at 61.5 min confirms the shift is exactly 1 frame. That plus its extra frame means **cam3 started recording one frame early**.
  - Aligned by that one frame, the offset is 0 within what's readable: a 60 Hz laptop display under a 20 ms exposure resolves about 17 ms. There was **no measurable drift from 25 to 61.5 min**.
- cam3 lost contact with the hub's chrony from about 15:38 to 15:54, with reported offsets up to 41 ms. Its frames didn't shift during that window (the 31 min reading is inside it).
- **Implications:**
  - Software sync keeps frames time-aligned, but **the start frame isn't guaranteed to match across nodes.** Ingest can't assume frame N is the same instant everywhere: it has to align by timestamp (e.g. wall-clock time of the first frame per node), or detect the shift.
  - The frame-count verify in ingest has to tolerate ±1 start frame.
- The gate "< 1 frame after 60 min" passes on timing, with the caveats: start alignment is off by one on 1 of 4 nodes, and the readable span is 36.5 min, not 60.

### Next
- **Decided: `--denoise cdn_off` for offline recording** (added to CLAUDE.md settled decisions). Still check image quality under studio lighting. MediaMTX's camera source has its own denoise setting, which needs checking in step 5.
- The user decided **not to re-run step 2**. The 36.5 min readable span is accepted.
- Find out how to get an absolute start time per node (e.g. log wall-clock time at the first frame; `--save-pts` is relative), so ingest can align starts.

### Hub now wired; fan; clock fix (after step 2)
- The hub is on **eth0, 1000 Mb/s full duplex, 192.168.68.73** (DHCP). Wi-Fi isn't connected now.
- **Fan:** a Pi 5 fan header `pwm-fan` (cooling_device0). The stock kernel controls it, so nothing needs installing. Tested: 5897 rpm at state 4, 1773 rpm at state 1, off at idle. Trip points (step_wise): 50, 60, 67 and 75 °C.
- **Clock fix:** after the hub's IP changed, node chrony had dropped the hub and fallen back to a DHCP-supplied public NTP server, which was unreachable. `sourcedir /run/chrony-dhcp` is now commented out on all nodes and chrony restarted.
  - All four are locked to 192.168.68.73 (the only source), at 13–144 µs from NTP time. That's tighter than over Wi-Fi.
  - The hub is at stratum 3.
- Suggestion: a DHCP reservation for the hub on the router, so its IP stays fixed.

### Step 4: throughput + sync under load (run A, 16:35–16:48, Wi-Fi power save ON by mistake)
- Four one-off `iperf3 -s -1` servers on the hub (ports 5201–5204); the daemon isn't enabled. Each node sends UDP `-b 7.5M -t 600` while recording a 13-min synced take (`OUTDIR=~/synctest_load`).
- **iperf3: 0/388,5xx datagrams lost on every node; jitter 0.67 / 3.28 / 1.51 / 1.67 ms (cam1–4), 7.50 Mbit/s steady in every 60 s interval.** That's 30 Mbps combined without loss, so the gate passes.
- **Sync under load:** frames were matched by PTS at 60, 400 and 700 s, and the stopwatch reads identically on all four at all three points. No start off-by-one this time.
- **But the clients stalled:**
  - cam2 had stalls of 300 and 167 ms (12 frames lost), cam3 one of 267 ms (7), cam4 233 and 200 ms (11). The server, cam1, had none.
  - There were no stalls in the 62-min run without load. The temperatures stayed at or below 43.5 °C, with no throttling. Chrony stayed within 4 ms.
- **Found: Wi-Fi power save was ON on all nodes** (`iw` showed "Power save: on"; NetworkManager was at "default"), contrary to CLAUDE.md.
  - Fixed: `node/networkmanager/wifi-powersave-off.conf` (wifi.powersave = 2) is installed in `/etc/NetworkManager/conf.d/`, plus `iw dev wlan0 set power_save off` live.
  - `iw` was installed on the nodes.
  - Hypothesis: power save delays broadcast sync messages until DTIM, which could cause the client-only stalls.
- Run B (same test, power save off) started 16:51:29 to test that.

### Step 4 run B (16:51–17:04, power save OFF)
- **iperf3: 0 loss on all four again; jitter 1.71 / 1.55 / 1.91 / 0.85 ms (cam1–4).**
- **Stalls dropped from 5 (30 frames) to 2 (5 frames):** cam2 had 133 ms at 409 s (3 frames), cam3 100 ms at 520 s (2 frames). cam1 (server) and cam4 had none.
  - Still clients only. Power save was part of the cause, not all of it.
- Sync under load: matched by PTS at 60, 450 and 700 s, all four read identically, so sync was unaffected by the stalls.
- Temperatures were 41.9–44.0 °C with no throttling. Chrony stayed within 0.5 ms (the hub is wired now).
- **Open question for the write-up:** do the client stalls come from the sync client handling delayed broadcasts, or from Wi-Fi load in general?
  - A quick test: record with `--sync off` under the same load.
  - In normal use, takes don't overlap heavy network traffic (ingest runs only when idle). But the cameras share home channel 7, so a dedicated channel matters. Ingest must flag PTS gaps.

### Step 5: live (17:08)
- MediaMTX v1.21.3 (arm64 tarball, sha256 checked) is in `~/mediamtx/` on cam1, run with `node/mediamtx/mediamtx.yml` as `studio.yml`.
  - It's started by hand with setsid nohup: no systemd unit yet. **It's still running and holds cam1's camera:** `pkill -x mediamtx` before any `rpicam-vid` on cam1.
- OBS picks it up over SRT. Latency is about 1 s, measured by the user. The user's photos haven't been received yet; read them to confirm when they arrive.
- The Mac's Homebrew ffmpeg has no SRT support, so I tested over RTSP/TCP: H.264 Main 1080p30.

### Step 6
- Wrote `docs/phase1-results.md` (GO with 5 conditions) and `docs/versions.md`.

### Phase 2 started (user said "start phase 2", 2026-11-14)
- **Test data removed from all hosts:** `~/preflight`, `~/synctest*`, `~/iperf` and the spike `~/mediamtx` on cam1. MediaMTX was stopped. `/tmp` setup files were removed too. Every camera has 25 GB free.
- **Kept, because it's provisioning the hosts depend on:**
  - chrony configs on all hosts;
  - the NetworkManager power-save-off config on the nodes;
  - `iw` and the pinned packages.
  
  Ansible must reproduce all of these.
- CLAUDE.md "Current phase" now says Phase 2. The first task is `docs/phase2-plan.md`, for the user's OK.

- Phase 1 checklist moved from CLAUDE.md to the appendix of `docs/phase1-results.md`.
- **`docs/phase2-plan.md` written (draft).** It covers spikes S1–S3, milestones M1–M4 and six decisions for the user.

### Phase 2 session 1 (2026-11-14, 17:15–18:20)
- **Plan approved** with the recommended answers: passwordless sudo, UI on port 80, cam1 as default sync server, `/var/lib/camnode/takes`, S3 skipped. The router (DHCP reservations, camera channel) is still the user's job.
- Passwordless sudo is set up on all five hosts by `deploy/bootstrap.yml` (run once by the user with `-K`).
- Ansible is in `deploy/.venv` (Homebrew Python 3.14.8, ansible-core 2.20.11).

**S1, first-frame wall clock: solved.** `rpicam-vid --metadata <f> --metadata-format json` writes `FrameWallClock` (epoch ns) and `SensorTimestamp` for every frame, streamed as the take runs.
- In a synced 20 s take, the first-frame wall clocks agreed within 18 µs across the four cameras. Metadata, PTS and frame counts all matched (600 each).
- `SensorTimestamp` deltas equal the `--save-pts` deltas, so PTS can be rebuilt exactly from metadata if the exit crash loses it.
- Metadata is about 700 bytes a frame, roughly 38 MB per 30 minutes.
- The absolute check against a stopwatch is folded into M4.

**S2, stop method: SIGINT.** 50 synced 4-node takes for each method, 600 node-runs in all. Note: these runs didn't use `--mode`, `--bitrate` or manual focus, but the log shows the sensor still picked 2304×1296.
- `-t 5000`: 200/200 exit 0. Frame counts sometimes differ by one at the end.
- `-t 0` stopped with SIGINT: 200/200 exit 0, every PTS file complete. **Chosen**, because takes are open-ended.
- `-t 0 --signal` stopped with SIGUSR2: one exit 134 on cam1 (`malloc_consolidate(): unaligned fastbin chunk`, PTS still written), and one run on cam4 where the camera never started within 8 s (no files).
- The abort rate is far below Phase 1's 3 of 40. camnode still rebuilds PTS from the metadata if it's ever missing.

**S2 side finding: a sync client can start late while reporting "Sync achieved".** On cam4 only, 6 of 150 takes, the client logged `Sync achieved! Difference -183870us`, `-968851us`, `-33325us` (one frame), `-1069us`, `-2918us` and `+3079us`.
- Each value exactly matches its first-frame wall-clock offset.
- All nodes start the camera at the same moment, but cam4 declares sync later by about the Difference. So it's missing or receiving late the server's broadcasts.
- Power save is off on all nodes. cam4 has the most Wi-Fi tx failures (348, against 43–216), but cam2 has a weaker signal and was fine.
- Handled by detection:
  - camnode records `sync_difference_us` in the status and the manifest;
  - hubd warns right after REC when any camera is more than 1 ms off ("consider STOP and REC again");
  - the take summary flags it.
- Open question: does a late client then lock frame phase, or stay fractionally off? Check during M4 if it happens again.

**M1, Ansible: done.** `deploy/site.yml` has the roles `common` (pinned packages, apt holds, persistent journal capped at 50 MB, timezone), `chrony_server`, `chrony_client`, `node_net`, `mediamtx`, `camnode` and `hubd`, and ends with checks (power save off, chrony synced).
- From the hand-built state, the first run made 18 changes per node and 11 on the hub; **the second run reported changed=0 on every host.**
- The MediaMTX config is now the template `node/mediamtx/mediamtx.yml.j2`, fed from `deploy/host_vars/camN.yml`. The image settings there are still placeholders (`cam_calibrated: false`).

**M2, camnode (`node/camnode/camnode.py`, stdlib, port 8080): deployed on cam1–cam4.**
- It runs `rpicam-vid -t 0` with the Phase 1 offline settings plus `--metadata`, and stops it with SIGINT.
- Each take has `take.json`, `video.h264`, `video.pts`, `metadata.json`, `rpicam.log` and `manifest.json`. The manifest holds the first and last frame wall clocks, frame count, gaps and dropped frames, `pts_source` (`save-pts` or `rebuilt-from-metadata`), the exit code, `sync_difference_us`, and a sha256 computed in the background.
- Takes left without a manifest after an agent restart are finalised on startup.
- The systemd unit uses KillMode=mixed, so stopping the agent stops the take cleanly.
- 15 tests (`python3 -m unittest tests.test_camnode` in `node/camnode`), using a fake `rpicam-vid` that can drop frames, crash on exit, ignore signals or die.

**M3, hubd (`hub/hubd/`, stdlib, port 80 as `studio` with CAP_NET_BIND_SERVICE): deployed.**
- REC runs the arm checks (with reasons), stops live mode, arms the clients, waits 2 s, then arms the server and waits for every camera to report recording. A sync timeout aborts the take and stops every camera. STOP stops all cameras in parallel and returns a summary (start spread, frame-count spread, dropped frames, sync warnings).
- Takes are logged to `/var/lib/hubd/takes.json`.
- The UI is `http://studio-hub.local/`: camera cards, REC/STOP/LIVE controls, OBS SRT URLs with copy buttons, and the last-take table.
- 10 tests using a fake rig.

**Bugs found on real hardware and fixed:**
1. `systemctl stop mediamtx` hung for 90 s. MoQ is on by default and writes its key to the working directory (`/` under systemd). A goroutine dump (SIGQUIT) showed main blocked in `moq.(*Server).Close`. The fix is `moq: false`, plus KillMode=mixed and TimeoutStopSec=10 in the unit. Stop now takes 0.2–0.5 s.
2. camnode held its lock during `systemctl`, so `/status` blocked and the hub showed every node offline. The agent now has a `switching` state and runs `systemctl` without the lock.

**End-to-end results through the hub (real hardware):**

| Take | Frames per camera | Start spread | Result |
|---|---|---|---|
| 10 s | 319 | 22 µs | clean, no drops |
| 5 s, REC from live | 165 | 20 µs | clean |
| 8 s, REC from live | 252 | 26 µs | clean, REC took 7 s |

- LIVE on/off: all four cameras serve 1080p30 H.264 (checked with ffprobe over RTSP).
- The test takes are still in `/var/lib/camnode/takes/` on the nodes: 4 small takes, `20261114-18*`. They're safe to delete.

### Phase 2 session 1, continued: M4 acceptance (18:20–19:40)
- **All five hosts rebooted** (the user asked). They were back in 72 s, with everything recovered by itself: services up, chrony locked, power save off, holds kept, journal persistent.
- **Three takes through the hub:**
  - 5 min: 23 µs start spread; cam1 dropped 27 frames in one 933 ms stall.
  - 5 min: 32 µs; cam2 dropped 4.
  - 30 min: 17 µs; 60 frames dropped across all cameras, in 13 stalls of 67–733 ms.
- **The stopwatch matches on all four cameras at all 8 instants checked**, including after stalls. Frames were chosen by `FrameWallClock`. The tool used is described in `docs/phase2-results.md`.
- **OBS:** the user confirmed all four cameras. The first attempt looked empty because OBS connected before MediaMTX had a stream. Fixed: camnode now waits for MediaMTX's local API (`127.0.0.1:9997`, `/v3/paths/get/cam` ready) before LIVE returns, about 3.6 s. There's also a `live-starting` mode.
- **REC refused** with HTTP 409 `cam4 offline` while camnode was stopped on cam4.
- **hubd adopts a running take after a restart** (tested on the hardware mid-take). The summary's `all_clean` now counts dropped frames.
- **Stalls:** they cluster across cameras in time and don't line up with Wi-Fi tx failures (1 Hz logger during takes 2 and 3; the CSVs were in the scratchpad and aren't kept). The cause is open. See `docs/phase2-results.md` for the suggested A/B test.
- **Tooling note:** from about 19:14, this session's command sandbox stopped passing SSH (banner timeout to all hosts, the hub included) while HTTP still worked. Outside the sandbox SSH was fine. The hosts weren't affected.
- The test takes are kept on the nodes: 7 per node, about 2 GB each, 23 GB free. The user should decide whether to delete them.
- **`docs/phase2-results.md` is written: all exit criteria pass.**

- **User decisions (19:45):**
  1. No stall A/B test.
  2. **Prioritise sync** at ingest. CLAUDE.md's Ingest line now says alignment by first-frame wall clock, gaps must not shift later frames, and re-encoding is allowed where needed. A plain 30 fps rewrap only for cameras with no gaps.
  3. Test takes deleted on all nodes (25 GB free each). The hub log was renamed to `/var/lib/hubd/takes-phase2-tests.json`, not deleted.

### Phase 3 started (user said "start phase 3", 2026-11-14 ~19:50)
- CLAUDE.md "Current phase" now says Phase 3.
- **New finding for the plan: the real frame rate is 30.011 fps.** S1 measured 600 frames over 19.9596 s, so 33.32 ms per frame. Stamping the frames as 30.000 fps drifts about 1.3 s per hour against audio and real time, even without dropped frames. CLAUDE.md's Ingest line is updated: conform by timestamp to an exact 30 fps grid, unless spike S1 can get the sensor close enough to 30.000.
- Hub check:
  - no external drive attached;
  - 1 GB RAM, 4 cores, no hardware H.264 encoder (libx264, libx265 and ProRes are available);
  - mkvtoolnix 92.0, rsync 3.5.0 and exfatprogs 1.2.9 already installed;
  - 24 GB free on the SD card.
- **`docs/phase3-plan.md` written (draft):** the key problem (rate and gaps), spikes S1–S4, design, milestones M1–M4, and six decisions for the user.

- **User:** "30.011 is fine if editing software and YouTube are OK with it", and "it has to be able to be synced against the audio". So the plan has Path A (native timing, `-c copy`, VFR at gaps) or Path B (conform to 30.000, re-encode). **S1 decides between them in Premiere against DAW audio** (claps at start, middle and end, within 1 frame). CLAUDE.md's Ingest line is updated.
- **Decisions (answered):**
  1. A USB stick `STUDIO` for now.
  2. Re-encode only if Path B.
  3. Ingest after 30 min idle, plus a button.
  4. Working space on the hub SD.
  5. Cameras delete verified takes below 10 GB free.
  6. Samba and Mac auto-pull deferred.
- **A transient drop burst:** in a 20 s test take (`20261114-195054`), cam2 lost 67 of 603 frames, one every 0.3 s from 0.77 s on. That's the Phase 1 encoder-overload pattern, but `cdn_off` was in use.
  - Not reproduced in two retries.
  - Nothing in the journal; idle, 1 GHz, not throttled.
  - apt-daily had run 18 min earlier (4 s).
  - Cause unknown. **A live drop counter is added to the plan (M3).**
- **S1 tooling, `hub/spike/convert.py`:** makes Path A and Path B `.mov` files, with a time-of-day start timecode (first-frame wall clock floored to the 30 fps grid, local time). Tested on the hub with that take:
  - Path A: copy in 0.2–0.4 s. cam1 runs at 30.011 fps; cam2 has 536 frames with variable timing.
  - Path B: libx264 veryfast CRF 16 takes 17–19 s per 20 s, so about real time per camera on the Pi 5. Frame counts: 608 and 604, exactly 30.000.
  - A stopwatch crop shows cam1 and cam2 identical (4:06:28.17) at Path B frame 590 and at Path A t=19.6 s, so both paths keep sync between cameras through 67 gaps.
  - Test files are in `~/convtest/` on the hub (my files).

### S1 run and Phase 3 progress (20:29–21:30)
- **S1 take `20261114-202934`** (15 min, DAW recording, claps): 11.8 µs spread. cam2 dropped 3 frames and cam4 dropped 5.
  - **cam1 exited −6** (`malloc_consolidate(): invalid chunk size`, the rpicam exit crash) *after* writing everything: the PTS file was complete (27527 = metadata).
  - The user bounced the DAW audio to `claptest.mp3` in the repo root (their file; left in place).
- **The user has no Premiere at the moment, so no test files.** The S1 clap check was done in code instead. **Video stamped at real time matches the DAW within 1 frame (−12 / −29 ms over 389 / 465 s); naive 30 fps would be about 4 frames off.** **Path B (conform) is the default.**
- The user asked what "cam1 server" in the UI means: it's the sync role (explained).
- **OBS:** the user saw one camera at a time with sluggish switching. The MediaMTX logs showed OBS closing and reopening SRT on every switch ("closed: EOF"). Fix: in each Media Source, untick "Close file when inactive"; use Multiview to see all four. **The user confirmed OBS is fine now.** Put this in the runbook.
- **S3:** about 4.5 Mbps per node, 18 Mbps in total, with idle Zero CPUs. Wi-Fi-bound, so keep rsync over SSH.
- **The hub's ingest key is deployed (user-approved),** restricted to read-only rsync of `/var/lib/camnode/takes`. A shell, other paths and writes are all refused (tested). Fixed the `known_hosts` idempotency (OpenSSH 10 `ssh-keyscan` prints a banner comment on stdout). `site.yml` second run: changed=0 on all hosts.
- Pinned `rsync` on all hosts, and `mkvtoolnix` and `exfatprogs` on the hub.
- A dead-end, recorded: per-frame timing via the ffmpeg `setts` bitstream filter with a changed time base fails in the MOV muxer (duration overflow). Use `mkvmerge --timestamps` (`hub/spike/convert.py`).
- The user may move the rig to its final studio location next.

### Quick wins for mounting (21:30–21:45)
- **Live dropped-frame counter:** camnode `LiveGapCounter` reads only new `metadata.json` bytes on each `/status` (complete lines only). The status `take` now has `frames`, `dropped_so_far` and `gaps_so_far`. The UI card shows "dropped" (red when non-zero). 18 camnode tests.
- **Preview link** on each UI card: `http://camN.local:8889/cam/` (MediaMTX WebRTC), active only in live mode.
- **Deploy guard:** the first play of `site.yml` reads `http://127.0.0.1/api/status` on the hub and refuses unless the hub is idle and every camera is idle (`-e force_deploy=true` overrides). Tested: it refuses during LIVE; changed=0 when idle. (Added after I deployed without checking the rig state; no take was affected.)

### M1–M3 built and deployed (21:45–22:30)
- **`hub/ingest/ingest.py`, the `hub-ingest` service** (separate from hubd, runs as `studio`, Ansible role `hub_ingest`). Per take and camera, every step resumable and persisted in `/var/lib/hubd/ingest/state.json`:
  1. rsync pull with the restricted key (all cameras in parallel);
  2. sha256 against the manifest (waits if the camera is still hashing);
  3. conform (mkvmerge timestamps, ffmpeg `fps=30` on the time-of-day grid, libx264 veryfast CRF 16, nice 19);
  4. verify (frame count = expected ±1, 30/1, timecode, full decode);
  5. write to `STUDIO/takes/<YYYY-MM-DD>_<take>/<cam>.mov` via `.part`, fsync, sha re-check, rename; `take.json` last, merged if a camera arrives later;
  6. mark verified on the camera.
- **Busy handling:** rig busy pauses it. rsync is killed (`--partial` resume); ffmpeg gets SIGSTOP/SIGCONT; the drive copy checks every 2 s. It starts after 30 min idle (`ingest_idle_minutes`) or on "Ingest now".
- **Drive:** found by `/dev/disk/by-label/STUDIO`, `fsck.exfat -n` once per plug-in (refuses if dirty), mounted at `/mnt/studio` only while writing, Eject = unmount + `udisksctl power-off`.
- **hubd:** `GET /api/ingest`, `POST /api/ingest/start`, `POST /api/drive/eject` (command files in `/var/lib/hubd/ingest/commands/`). The UI has an "Ingest & drive" panel.
- **camnode:** `POST /takes/<id>/verified` (writes `verified.json`); `/takes` shows it. **Lazy delete:** below 10 GB free (`delete_below_gb`), the oldest *verified* takes are deleted, never unverified ones or the current take. It runs before arm and every 10 min when idle, logged to `/var/lib/camnode/deleted.jsonl`.
- **Tests:** ingest 14, camnode 19, hubd 14, all pass on the Mac.
- **Real dry run on the hub** (scratch folder as the drive, camera marking disabled) on takes `195054` and `195143`: all 8 camera-takes went through every step in 255 s.
  - 195054/cam2: 536 frames in (67 dropped), 604 out (68 repeated).
  - Others: 607 → 608 (rate repeat) or 607 → 607.
  - Each take's timecode was identical across cameras.
  - Confirmed afterwards: 0 takes marked on the cameras; scratch removed.
- Production ingest is deployed and waiting: "no STUDIO drive".

### Phase 4 requirements from the user (2026-11-14, ~22:40). Not built yet; for the Phase 4 plan.
- **When:** in the next phase, *after* the user has moved the cameras to the final studio location.
- **Lighting isn't fixed** (moods and vibes), so capture must be adjustable.
- **One set of controls for all four cameras**, changing them consistently. Proposed:
  - global brightness (gain), white balance as colour temperature plus named presets, optional contrast and saturation, and saved "looks";
  - per-camera calibration offsets from a grey card underneath, so the angles stay matched.
- **Autofocus:** the user wants it, e.g. when someone walks up to a camera.
  - **This conflicts with the settled CLAUDE.md decision** ("manual focus … no auto exposure, white balance or focus during takes"), so it needs the user's OK in the Phase 4 plan.
  - Proposed: continuous PDAF (CM3) per camera, limited in range and speed, switchable per camera or take.
  - Test first on one camera with someone approaching. Notes: the wide lens has deep depth of field (sharp from about 1 m at 1080p), and continuous AF can hunt or breathe mid-take. AF does not affect frame sync.
- **Exposure and white balance during takes:** settings are fixed when `rpicam-vid` starts, so changes apply between takes (live mode: brief blip).
  - Proposed option: **"auto, then lock" at REC.** The cameras measure about 1 s, agree on one exposure and white balance, and lock it for the take. Each take suits the current light; no mid-take drift or mismatch between angles.
- Also planned for Phase 4: a grey-card calibration session (per-camera offsets) and focus help (Preview, sharpness readout).

### Next
- **The user plugs in the `STUDIO` USB stick** (exFAT). Then M1 on hardware: detect, fsck, mount, write, Eject.
- **M4 acceptance:**
  - a 30-min take ingested unattended after 30 min idle;
  - REC during ingest pauses it, then it resumes;
  - pulling the stick mid-write leaves only `.part` files;
  - lazy delete on a camera;
  - write `docs/phase3-results.md`.
  - Note: the existing 5 takes (including the 15-min S1 take) will be ingested first, about 1 h of hub time.
- Runbook: the OBS "Close file when inactive" note; mounting checklist.
- The deploy guard's message says "state idle" when only the cameras are live; reword it.
- Old: Build M1 (drive), M2 (ingest worker), M3 (camnode verified flag, lazy delete, live drop counter; UI: ingest panel, drive, Eject, preview links). Unit tests on the Mac.
- Hardware tests need the `STUDIO` USB stick in the hub.
- Old S1 instructions, kept for reference:
- **S1 with the user:** a 15-minute take with the DAW recording and claps at start, about 7 min, and end. Make Path A and Path B files for all 4 cameras (Path B takes about 15 min per camera on the hub, or run on the Mac if ffmpeg there is enough; the Mac lacks mkvmerge). Copy them to the user. The user checks in Premiere against the DAW audio.
- If S1 picks Path A, skip S2. Then S3 (pull throughput), then M1–M4.
- The user will format a USB stick as exFAT `STUDIO` and plug it into the hub (for M1).
- Tooling: SSH from this session's sandbox stopped working at about 19:14 (HTTP still works). Run SSH and Ansible with the sandbox off. Still open: router reservations and channel, and grey-card calibration.
- Live latency tuning (deferred by the user, 17:20). When it's picked up again, try these, with a stopwatch photo after each:
  1. OBS Media Source Network Buffering 0 MB plus hardware decoding.
  2. `&latency=100000` on the SRT URL.
  3. WebRTC Browser Source `http://camN.local:8889/cam/?controls=false&muted=true&autoplay=true`.
- Also measure DroidCam the same way, then match the angles with OBS's Render Delay filter on the faster source.

## 2026-11-15: hub UI on Android ("too many retries")
- Pixel 9a Chrome failed to load the hub UI; the Mac was fine. Cause: avahi publishes the hub's IPv6 link-local address, but hubd listened on IPv4 only (`0.0.0.0:80`, backlog 5). The Mac has IPv6 off on en0, so it always used IPv4.
- Fix: hubd now listens dual-stack (`*:80`, `IPV6_V6ONLY=0`) with backlog 64 (`DualStackServer` in `hub/hubd/hubd.py`). Verified: IPv4 200, and IPv6 link-local 200 from cam1 over Wi-Fi. Unit tests pass. Kept, but it was not the cause.
- **Real cause: Chrome on Android lacked the local-network permission** (App info → Permissions). A hub tcpdump showed no packets from the phone (192.168.68.66) while hub↔phone ping worked both ways. With the permission granted, the UI loads. Add to the runbook: "Phone can't open the hub page → allow Chrome's local network / nearby devices permission."
- Deploy snag: `site.yml --limit hub` fails in `ingest_key_hub` (ssh-keyscan) when cam2–cam4 are offline, before handlers run, so hubd had to be restarted by hand. Make that keyscan tolerate offline nodes.

## 2026-11-15: recording light (future, Phase 4 candidate)
- The user wants a big recording light. They chose a LocalBytes RGB smart bulb (pre-flashed local firmware, Tasmota or ESPHome; check which) in an ordinary lamp. Ordered 2026-11-15.
- Plan: hubd sets the bulb from rig state on every 2 s poll (idempotent, so it catches up after a restart or Wi-Fi blip). Colours (draft): dim green = ready; blue = live; amber = arming; red = recording; slow-pulsing cyan = ingest running; purple = ingest done, safe to eject; flashing red = error (camera offline, arm refused, drops during a take, drive full). Keep the lamp out of shot or dim: coloured light spills onto the scene, and white balance is fixed. Short timeout, never blocks REC/STOP. Config in `/etc/hubd/config.json` (type + address).
- To do when it arrives: DHCP reservation for the bulb, note its IP and firmware here, then build when the user says so.

## 2026-11-15: hub scratch mic (Phase 4 plan)
- The user bought a USB mic for the hub, for automatic audio sync against the DAW bounce. Added to the Phase 4 list in `docs/design.md` (with the Synchronisation table updated). The user is fine with it not being perfect: confidence check, with "needs sync" as the fallback.
- First step when it arrives: record a scratch WAV on the hub during one real take, get the bounce, and test the matching offline.
- Also added to Phase 4: "add media to a take" (phone/handheld video, bounce, stems) from the web page, with audio matching for sync.
- Also added as a future idea: an automatic edit on the hub. Each partial bounce (one per song) is located within the take, flagged if it was edited in time, and an "Auto edit" makes a cut video with the bounce as soundtrack (beat-aware switching, skips cameras with drops), plus an EDL for Premiere.
- Also added as a future idea: Pixel 3 phones as offline camera units via a hub web page (REC/STOP from the hub, preview, chunked upload over 5 GHz, sync by browser frame timestamps plus audio match). Long sessions and quality are already confirmed by the user. The first test covers exposure/WB/focus lock, timestamps and quality in the browser.
- Also added to Phase 4: a snapshot overview page (a still from every camera side by side, for setup and calibration; not stored).
- Not in the plan (user's call): YouTube streaming / live auto-switching.

## 2026-11-15: git + GitHub
- The project is now a git repo (`main`), pushed to the user's private GitHub repo `manneohrstrom/studio-hub` over SSH. `.gitignore` keeps out media (`claptest.mp3` etc.), `deploy/.venv`, `__pycache__` and `.DS_Store`.
- `gh` isn't installed; the repo was created on github.com by the user.

## 2026-11-15: cam1 live preview lag (Wi-Fi placement)
- The hub moved into the studio (wired, same IP .73, 1 Gb/s). cam1 at a temporary spot on a new power supply: no throttling, 49.9 °C.
- Bad quality and multi-second lag in the WebRTC preview. Causes:
  1. Three WebRTC preview sessions from the Mac at once (each a full 5 Mbps copy). Later OBS SRT plus a preview made two readers.
  2. Weak signal at the temporary spot: −61 → −72 dBm, link 5.5–21.6 Mbit/s, ping 2–5.6 s (queues full). MediaMTX logged "reader is too slow, discarding N frames".
- After moving cam1: −35 to −37 dBm, 72.2 Mbit/s, ping 4–29 ms, 0 discards. Fixed.
- Lessons, for the runbook and camera placement: aim for −60 dBm or better at each camera spot (check with `/usr/sbin/iw dev wlan0 link`). One live reader per camera where possible; each extra viewer costs a full stream. `iw` lives in `/usr/sbin`, which isn't on the non-login SSH PATH.
- The hub card already shows Wi-Fi dBm. Small idea: turn it red below −65 dBm, and add the link rate.

## 2026-11-15: board LED as recording light (side item, outside Phase 3)
- camnode drives the Zero's green ACT LED from agent state (`StatusLed` in `node/camnode/camnode.py`): armed = 0.1 s blip per second; recording/stopping = 2 Hz flash; idle/live = the kernel default trigger (`actpwr`) restored. Blinking happens in a camnode thread (trigger `none` + `brightness`), because the kernel `timer` trigger recreates `delay_on`/`delay_off` root-owned on every trigger change. All LED errors are swallowed; a missing or unwritable LED is a no-op. Config key `led_path` (`""` disables).
- Permissions: udev rule `node/udev/99-camnode-led.rules` (chgrp video + g+w on `trigger` and `brightness`), installed by the camnode role, which runs `udevadm trigger` when the rule changes.
- Tests: 2 new (`LedTest`, fake sysfs dir); all 21 pass.
- Deployed to **cam1 only** (cam2–cam4 offline). `site.yml --limit cam1` fails at `ingest_key_node` (needs the hub's facts), and `--limit hub,cam1` fails at the hub keyscan of offline nodes (same snag as above), so the camnode restart was done by hand after both runs. Verified on cam1 with a 1-camera server take: armed blip, then 2 Hz flash while recording, then `actpwr` after stop. Test take deleted.
- Next: deploy to cam2–cam4 when they're back online (a normal full `site.yml` run). Fix the playbook so a single camera can be deployed (keyscan tolerates offline nodes; node key role tolerates the hub being out of the limit).
