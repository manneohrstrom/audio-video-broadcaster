# Phase 1 results

2026-11-14 · four Pi Zero 2 W + Camera Module 3 Wide nodes (cam1–cam4) and a Pi 5 hub. Versions are in `docs/versions.md`; the session log is in `docs/progress.md`. Spike scripts are in `node/spike/`.

## Verdict

**Recommendation: GO**, with five conditions for Phase 2–3 (listed at the end). Both gates pass:

| Gate | Result | Pass? |
|---|---|---|
| Under one frame of offset after 60 minutes | Time-aligned within the measurement resolution (about 17 ms); no drift between 25 and 61.5 min. One node started one frame early, a start-index offset, not drift. | ✅ (caveats below) |
| 30 Mbps combined from the nodes without loss | 4 × 7.5 Mbps UDP for 10 min, run twice: 0 datagrams lost on every node; jitter 0.7–3.3 ms | ✅ |

## 1. Preflight

- All four nodes detect `imx708_wide`. The 2304×1296 @ 56.03 fps mode is present, and `rpicam-vid` lists `--sync off|server|client`.
- 10 s at 1080p30 recorded on each node: H.264 High 1920×1080, decodes with no errors.
- **Frame drops with the default denoise.** At the stock settings, every node dropped frames after the first 4–5 s.
  - A 60 s matrix on all four nodes:

    | Denoise | CPU governor | Drops per 1800 frames |
    |---|---|---|
    | `cdn_fast` (default) | ondemand | 97–129 |
    | `cdn_fast` | performance | 0–1 |
    | `cdn_off` | ondemand | **0** (also 0 in 120 s runs) |
    | `off` | ondemand | 0, but 10–11 Mbps instead of 7–9 |
    | `cdn_hq` | either | ~800 |

  - Cause: the ISP and encoder run on the GPU core clock, which sits at 320 MHz under `ondemand`.
  - **Decision: `--denoise cdn_off`** (now in CLAUDE.md).
- **`rpicam-vid` 1.15.0 sometimes aborts at exit** with `double free or corruption (fasttop)`, in 3 of about 40 short runs and 0 of the long ones. The video survives, but `--save-pts` (written only at exit) was lost once.

## 2. Sync drift (60 minutes)

The run went from 15:15 to 16:18, recording for 62 min. Clients were cam2–cam4, the server was cam1. Settings: 1080p30, 16 Mbps cap, `cdn_off`, shutter 20000 µs, fixed gain/AWB/focus. Chrony was served by the hub, which was on Wi-Fi at the time.

- Every node: `Sync achieved` (difference 0 to −13 µs), a clean exit, PTS written, **0 dropped frames**. Frame counts were 111639 on three nodes and 111640 on cam3.
- The stopwatch was a laptop showing hundredths on a 60 Hz screen, so the resolution is about 17 ms (half a frame).
  - The screen was blank at 0 and 15 min, and the timer was reset at about 29 min, so readable points are 25, 31 and 61.5 min.
  - Frames were cut on the node at exact indices with `node/spike/cutframes.py`.

  | Frame (min) | cam1 | cam2 | cam3 | cam4 |
  |---|---|---|---|---|
  | 45000 (25) | 41:09.00 | 41:09.00 | 41:08.98 | 41:09.00 |
  | 55800 (31) | 1:38.65 | 1:38.65 | 1:38.62 | 1:38.65 |
  | 110700 (61.5) | 32:08.00 | 32:08.00 | 32:07.98 | 32:08.00 |

- cam1, cam2 and cam4 match exactly. **cam3's frame N matches the others' frame N−1** at all three points, confirmed over 8 consecutive frames. With its one extra frame, that means cam3 started recording one frame early.
  - Corrected for that, all four agree.
  - **No drift from 25 to 61.5 min.** The user decided not to re-run for the full 60 min.
- In the two later 13-min runs, all four started on the same index (checked by PTS and stopwatch). So the off-by-one start happens sometimes, not always.
- cam3 lost contact with chrony for about 15 min (reported offsets up to 41 ms), and its frames didn't shift. So frame sync is less sensitive to NTP than the design inferred.

## 3. Thermals

Logged every minute during all runs:

| Run | cam1 | cam2 | cam3 | cam4 | hub |
|---|---|---|---|---|---|
| 62-min drift | 40.2 °C | 40.2 °C | 41.9 °C | 39.7 °C | 43.9 °C |
| Load run A | 41.9 | 41.9 | 43.5 | 41.9 | 43.3 |
| Load run B | 42.9 | 41.9 | 44.0 | 42.4 | — |

**No throttle flags at any time.** The hub fan (Pi 5 header, stock control) is verified working and starts at 50 °C.

## 4. Throughput and sync under load

The hub, now wired at 1 Gb/s, ran four one-off `iperf3` servers. Each node sent UDP at 7.5 Mbps for 10 min while recording a synced 13-min take.

| | Run A (Wi-Fi power save ON, by mistake) | Run B (power save OFF) |
|---|---|---|
| Loss | 0 on all four | 0 on all four |
| Jitter (cam1–4) | 1.67 / 3.28 / 1.51 / 0.67 ms | 1.71 / 1.55 / 1.91 / 0.85 ms |
| Recording stalls | 5 stalls, 167–300 ms, 30 frames lost (cam2, cam3, cam4) | 2 stalls, 100–133 ms, 5 frames lost (cam2, cam3) |
| Stopwatch, matched by PTS at 3 points | identical on all four | identical on all four |

- **Wi-Fi power save was on** on all nodes (the OS default), against the settled decision. It's now off via `node/networkmanager/wifi-powersave-off.conf`, which cut stalls by about 85%.
- **The remaining stalls only affect sync clients**, never the server. Sync recovers immediately after: the frames on either side of a stall are still time-aligned.
- **Not tested:** whether the stalls come from the sync client's handling of delayed broadcasts, or from Wi-Fi load in general. A `--sync off` run under load would settle it.
- Network: the cameras share the household network on 2.4 GHz channel 7, and the router passes the sync broadcasts.

## 5. Live

- MediaMTX v1.21.3 runs on cam1 with its built-in camera source (`node/mediamtx/mediamtx.yml`): 1080p30 H.264, 5 Mbps, 1 s GOP, the same locked exposure and `cdn_off`.
- About 8% CPU on the Zero, and 36.5 °C.
- OBS on the Mac receives it over SRT (`srt://cam1.local:8890?streamid=read:cam`). RTSP over TCP was also verified from the Mac.
- **Latency about 1 s**, measured by the user with the stopwatch. That's the top of the design's 0.3–1 s estimate. It hasn't been tuned yet: OBS network buffering, SRT latency and the GOP length are the knobs.

## Changes made during Phase 1

- **Hub:** a Pi 5, not a Pi 4. Moved from Wi-Fi to wired Ethernet (192.168.68.73).
- **Chrony:** the hub serves the LAN, and the nodes use only the hub.
  - DHCP-supplied servers were disabled on the nodes. The router hands out an unreachable public NTP server.
  - Offsets were 13–144 µs wired, against ±0.1–2.5 ms when the hub was on Wi-Fi.
- **Wi-Fi power save:** off on all nodes.
- **Settled decision:** `--denoise cdn_off` for offline takes.
- **`docs/design.md`:** updated with these findings.

## Conditions for GO (carry into Phase 2–3)

1. **Per-node start time.**
   - Software sync keeps frames time-aligned but doesn't guarantee a common start frame.
   - Each node records the wall-clock time of its first frame in the manifest. The hub derives each `.mov` start timecode from that, not from one shared value.
   - Verify tolerates a ±1-frame difference in count between nodes.
2. **Flag PTS gaps at ingest.** Stalls under heavy Wi-Fi load are rare but real. Give the cameras a dedicated 2.4 GHz channel (1, 6 or 11) away from household traffic, as the design intends.
3. **Handle the `rpicam-vid` exit crash.** The agent checks the exit code and that the PTS file is complete. Options:
   - Rebuild PTS from the stream if needed.
   - Try stopping with SIGINT instead of `-t`.
   - Report it upstream.
4. **Provision with Ansible** from `docs/versions.md`: chrony configs, the power-save config, MediaMTX, and package holds. Today's setup was done by hand.
5. **White balance, exposure and focus** are placeholders (green cast). Calibrate with a grey card under the studio lights before real takes, and check the `cdn_off` picture quality there.

## Not done on that date (closed at 1.0)

- The sync drift readable span was 36.5 min, not 60. Accepted by the user.
- The cause of the client-only stalls under load is unconfirmed (`--sync off` control run).
- Live latency isn't tuned. Only cam1 is on MediaMTX, so multi-camera live in OBS is untested. Bitrate headroom is covered by the 30 Mbps test.
- The `STUDIO` drive (or USB stick) hasn't been tested on the hub yet.

## Appendix: the Phase 1 checklist (moved from CLAUDE.md)

1. **Preflight.** On each node:
   - `rpicam-hello --list-cameras`
   - confirm `rpicam-vid --help` lists the `--sync` options (if not, update packages)
   - record 10 s at 1080p30 and check it plays.
2. **Sync drift test.** Start `--sync client` on cam2–cam4, then `--sync server` on cam1. Record 60 minutes at 1080p30 with all four cameras framing a phone stopwatch.
   - Extract frames at the start, 30 minutes and 60 minutes with ffmpeg.
   - Read the stopwatch in each and report the offset in frames.
3. **Thermals.** Log temperature and throttle flags every minute during the 60-minute run.
4. **Throughput.** Run an iperf3 server on `studio-hub`. Have all four nodes send 7.5 Mbps at the same time for 10 minutes; this stands in for six cameras at 5 Mbps. Report loss and jitter, and repeat the sync check under this load.
5. **Live.** Run MediaMTX on cam1, add it to OBS over SRT, and note the latency.
6. **Write up.** Write `docs/phase1-results.md` with the measurements and a go/no-go against the gates:
   - under one frame of offset after 60 minutes;
   - 30 Mbps combined from the nodes without loss.
