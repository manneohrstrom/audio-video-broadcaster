# Phase 4 spikes: results

Spikes from `docs/phase4-plan.md`. Scripts used are kept in the session scratchpad only (one-off measurements); the commands are given here.

## B-S3: snapshot with `rpicam-still` on a Zero 2 W (2026-11-16): PASS

cam1 idle (camnode running), rpicam-apps 1.15.0, `--mode 2304:1296:10:P --width 1920 --height 1080 --denoise cdn_off -e jpg -q 85`, settings from `/etc/camnode/config.json` (shutter 20000 µs, gain 2, AWB gains 1.5/1.5, lens position 1.0):

| Variant | Wall time | Notes |
|---|---|---|
| Locked settings, `-t 1 --immediate` | **0.61 s** | 67 KB JPEG (dark room) |
| Locked settings, `-t 300` | 0.82 s | |
| Auto exposure + AWB, `-t 1000`, `--metadata` | 1.58 s | gives the auto values (ExposureTime, AnalogueGain, ColourGains, ColourTemperature, Lux) |

Peak memory of the largest child process: 21 MB (camera buffers are DMA, not counted). Zero's free memory before/after: ~250 MB available. camnode stayed idle and responsive. While a snapshot runs, the camera is busy: REC on that node would fail, so the snapshot page must never run one during arming or a take, and hubd should refuse REC for the second a snapshot is in flight (or wait for it).

## B-S1: live stream when a setting changes (2026-11-16): PASS, no blip

cam1 alone in LIVE (through hubd's `/api/live` with `nodes: ["cam1"]`), MediaMTX `rpiCamera` source. The hub read the stream over RTSP/TCP (ffprobe, packet times stamped on arrival) while the Mac sent `PATCH /v3/config/paths/patch/cam` to cam1's MediaMTX API (127.0.0.1:9997, so it has to come from the node itself or through camnode):

- `{"rpiCameraGain": 4}` then `{"rpiCameraAWBGains": [1.8, 2.0]}`, 9 s apart: 713 packets in 22 s, **no gap in stream time** and no arrival gap over 84 ms (normal jitter). MediaMTX logged "reloading configuration (API request)" each time and **did not restart the camera**.
- The change takes effect live: picture level above black went from 0.92 (gain 2) to 2.26 (gain 4), about ×2 (dark room, so small numbers).
- API changes are in memory only; restarting MediaMTX goes back to `mediamtx.yml`. So B2 must write the new values to the node's config as well as patching the running stream.
- Settings put back to gain 2, AWB 1.5/1.5; LIVE stopped.

So live-mode changes can be applied instantly with no interruption, and the plan's "a change restarts the stream" worry doesn't apply (at least for gain and AWB gains).

## B-S2: auto metering spread across the cameras (2026-11-16): PASS

All four cameras at once, `rpicam-vid -t 3000` at 1080p30 with `--shutter 20000` (fixed 1/50 s), gain and AWB on auto, manual focus at each camera's lens position, per-frame metadata (`--metadata`, JSON). Run twice with the studio lights on; the second run gave the same values (gain identical, colour gains within 0.5%).

| Camera | Gain | Lux | Colour temp | Red gain | Blue gain | Gain settled (frame) |
|---|---|---|---|---|---|---|
| cam1 | 12.80 | 33.4 | 2513 K | 1.257 | 4.001 | 15 |
| cam2 | 15.28 | 28.1 | 2455 K | 1.242 | 4.124 | 13 |
| cam3 | 14.63 | 29.1 | 2455 K | 1.242 | 4.122 | 15 |
| cam4 | 14.22 | 31.2 | 2498 K | 1.253 | 4.032 | 12 |

- **Exposure:** gains 12.8–15.3, a spread of 0.26 stop (±9% around the mean). Each camera meters its own view, so part of that is real scene difference. Small enough that "auto, then lock" per camera gives matching pictures, with a per-camera trim in the editor's colour step if needed.
- **White balance:** 2455–2513 K (tungsten studio light); red and blue gains within about ±1.5%. Tight enough to lock per camera; a global "same AWB for all" from one camera (or the grey card) would remove even that.
- **Settling:** gain within 3% of its final value by frame 12–15 (0.4–0.5 s). AWB reported its final value from the first frame. So "auto, then lock" at REC needs about 1 s of metering (allow 1.5 s), which fits the snapshot's auto variant (B-S3, 1.6 s).
- **Light level:** ~30 lux at the cameras needs gain 13–15 at 1/50 s, close to the 16 maximum, so images will be noisy. More light on the performers is the best image improvement available; the software can't fix it.
- First run (room lights off, 1–2 lux): all cameras at max gain 16, AWB 3838–4073 K; nothing about exposure spread could be measured.

## B-S7: watching for the calibration card on an idle Zero (2026-11-16): PASS

cam1 idle, `rpicam-vid -t 0 --framerate 1 --codec mjpeg -q 80 --segment 1` at 1920×1080 (same sensor mode and shutter as takes), one JPEG per second into `/dev/shm`, for 2 min:

- CPU 7.9 s user over 120 s (about 2% of the Zero), no temperature rise (32.2 °C before and after), 113 frames, about 320 KB each, so about 2.6 Mbps per camera if every frame went to the hub.
- Releasing the camera: SIGINT 671–973 ms; **SIGKILL 30–42 ms**, and the next `rpicam-vid` (a take-style H.264 open) started fine every time with no kernel errors (3 tries each). So REC and LIVE kill the watcher: no noticeable delay.
- Wi-Fi: four cameras watching send about 10 Mbps while idle, which competes with ingest (also idle-time). So watch only while the hub page is open or a device is waiting for the card, not all night.

Card detection (desk check before B-S5): OpenCV 4.10 (`python3-opencv` on the hub) finds both markers of the printed-card PDF down to 72 px across the whole A4 card, i.e. about 3 m from a Camera Module 3 Wide at 1080p in ideal conditions. B-S5 measures it for real; an A3 print roughly doubles the range.
