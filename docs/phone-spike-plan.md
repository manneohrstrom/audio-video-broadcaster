# Phone camera spike (Pixel 3, browser)

Status: started 2026-11-15 at the user's request, before Phase 4 Track B. Exploratory: nothing here changes hubd, ingest or the main UI. Background: "phones as offline camera units" in `docs/design.md`.

## Question

Can a Pixel 3 running nothing but Chrome act as a temporary offline camera that the hub controls, with footage that lines up with the Pi cameras within one frame?

## What the spike builds (`hub/spike/phone/`)

- `phone_spike.py`: a standalone stdlib HTTP server, run by hand on the hub on port 8090. It is not a systemd unit, and hubd is untouched.
  - `GET /api/time`: hub wall clock (ns), for the page's clock-offset pings.
  - `POST /api/poll`: the phone reports its status (battery, state, frames, drops, offset) and gets the pending command (rec/stop).
  - `POST /api/cmd`: REC/STOP for one or all phones (control page `/control`).
  - `PUT /api/chunk/<take>/<phone>/<seq>`: one ~2 s chunk (H.264 Annex B plus its frame timestamps). Idempotent, so uploads can be retried.
  - `POST /api/finish/<take>/<phone>`: joins the chunks into `video.h264` + `video.pts` + `manifest.json`, the same layout as a Pi take, so `mkvmerge`/conform and `hub/tools/clap_check.py` work on it unchanged.
  - Data goes to `/var/lib/hubd/phone-spike/<take>/<phone>/` on the hub's SD card (24 GB free; 1 h at 16 Mbps is ~7 GB). Never to `STUDIO`.
- `index.html`: the phone page.
  - Camera at 1920×1080, 30 fps. It shows `getCapabilities()` and `getSettings()` and uploads both to the hub log.
  - Locks: exposure time (1/50 s), ISO, colour temperature, focus distance, plus "auto, then lock" (read what auto picked, then set it manually).
  - Recording: `MediaStreamTrackProcessor` → `VideoEncoder` (H.264, hardware preferred, 16 Mbps, keyframe every 1 s), with each frame's capture timestamp kept.
  - Timestamps logged three ways for the spike: `VideoFrame.timestamp`, `performance.now()` on arrival, and `VideoFrame.metadata()` if present. Which one is the real capture time is a spike result.
  - Clock: an NTP-style ping to `/api/time` every 10 s (lowest-RTT sample out of 5) maps the page clock to hub time. The phone's own wall clock is not used.
  - Chunks are written to the browser's private storage (OPFS) first, then uploaded, either live or after STOP (a toggle), so a Wi-Fi drop doesn't lose footage.
  - Wake Lock keeps the screen on. The page warns if it goes to the background.

## Tests and pass marks

1. **Locks:** exposure 1/50 s, ISO, WB and focus can be set manually and stay put for a whole take. Record what the Pixel 3 offers.
2. **Encode:** 1080p30 hardware H.264 sustained for 60 min plugged in, with no dropped frames (gaps < 0.1%) and temperature OK.
3. **Sync:** a phone take alongside cam1 with claps (or a phone stopwatch in view of both). The offset between them must be under 33 ms at the start and still under 33 ms after 60 min. Measured with `clap_check.py`'s motion peaks.
4. **Quality:** the user compares it against footage from the Google Camera app.
5. **Robustness:** Wi-Fi drop mid-take, screen-off attempt, page reload after STOP (re-upload from OPFS).

## Setup on each phone (one-off)

- Chrome → `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, add `http://studio-hub.local:8090`, then relaunch. The browser only allows camera access in a secure context. For a real build, a local CA certificate replaces this.
- Chrome local-network permission (as on the Pixel 9a).
- 5 GHz Wi-Fi, plugged in.

## Outcome

`docs/phone-spike-results.md` with GO / NO-GO. On NO-GO for locks or timestamps, the fallback is a native Android app (e.g. from RecSync).
