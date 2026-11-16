# Editor spikes: results

Spikes from `docs/editor-plan.md`. Tools in `hub/spike/e1/`.

## E1: copy at real timing (2026-11-16): PASS

**Method.** Raw takes still on the cameras, copied to `~studio/e1-spike/` on the hub (hubd and ingest untouched). `mkvmerge --timestamp-scale 1000 --timestamps 0:video.pts`, then `ffmpeg -itsoffset <residual> -c copy -video_track_timescale 90000 -movie_timescale 90000 -timecode <tc>` into `.mov`. The residual is the first frame's offset from its 30 fps timecode slot. Check: every packet's time (ffprobe) against `video.pts` + residual; frame count; timecode; full decode.

| Take | Frames | Gaps (dropped) | Max time error | Timecode | Rewrap + verify |
|---|---|---|---|---|---|
| cam1 `20261115-231111` (14 s) | 431 = 431 | 0 | 0.006 ms | ok | 0.6 + 1.9 s |
| cam2 `20261114-195054` (20 s) | 536 = 536 | 63 (67) | 0.006 ms | ok | 0.7 + 2.7 s |
| cam2 `20261114-202934` (15.3 min) | 27524 = 27524 | 1 (3) | 0.0064 ms | ok | 290 + 219 s |
| phone-like: cam1 14 s, frames re-timed 24–44 ms apart at random | 431 = 431 | — | 0.006 ms | ok | 0.3 + 2.0 s |

- Decoded pixels are identical to the raw stream (framemd5, `20261114-195054`). Size: the `.mov` is the raw `.h264` size plus about 0.02%.
- **Pitfalls found:** (1) mkvmerge always starts at 0, so the residual has to go on as `-itsoffset`. (2) The mov stores it in an edit list in the *movie* timescale, which defaults to 1000, so the offset was truncated to whole ms (up to 0.97 ms error) until `-movie_timescale 90000`. Baking the residual into the timestamps file doesn't work, for reason (1).
- **The first frame's offset is in an edit list** (`start_time` 0, first packet at the residual). Players that ignore edit lists show the clip up to 33 ms early. The editor takes each camera's start from `take.json` (`first_frame_wallclock_ns`), not from the player.
- **Cost:** 0.55× real time on the 15-minute take (290 s rewrap + 219 s verify), against about 1.25× for conform + verify. The rewrap is mostly SD card I/O (the 945 MB stream is read and written twice: `.mkv`, then `.mov`), so a faster hub card helps directly. Short takes run at about 0.15×. Verify's full decode is now the larger share on short takes. Hub at 47.7 °C afterwards.
- Built into ingest as `video_timing: "real"` (`rewrap()`, `verify_real()`); off by default until E1 is complete.

**Browser playback (user's Mac, `play.html`, 2026-11-16):**
- Safari: all three files play at 1920×1080. Every shown frame's media time is within 0.011 ms of the manifest. Safari honours the edit list: the first frame shows at the residual (30.478 / 25.967 ms). Pi files: every frame shown (431/431, 536/536, gaps included). The phone-like file showed 430 of 431. Its frames are as little as 24 ms apart, so two can fall inside one 60 Hz screen refresh and one is never displayed. That's the screen, not the file; the frame times are right.
- Chrome: all three play at 1920×1080. Every shown frame is within 0.03 ms. Chrome also honours the edit list: `pi-gaps` and the phone-like file start at the residual. On `pi-short` Chrome didn't report the first frame, so the first one seen is the second frame (63.8 = 30.478 + 33.319). It reported 430/431, 534/536 and 419/431 frames: `<video>` playback doesn't promise to show every frame, and Chrome skips some, more of them on uneven spacing. The frame times are right in both browsers. Frame-exact display is what WebCodecs is for in the editor (spike E2), not `<video>`.

**Real phone take** (Pixel 9a, take `20261116-072121`, 30 s, raw files copied from staging before ingest): 804 = 804 frames, 102 dropped (measured 26.5 fps), every frame within **0.0016 ms**, timecode right; rewrap 4.7 s + verify 6.5 s (ingest's own code). Browser playback not separately checked for the phone file; the phone-like re-timed file played in both browsers.

**Result: PASS.** Ingest switched to `video_timing: real` (ingest 0.4.0, 2026-11-16, per the user's decision of the same day).

## E2: browser playback (2026-11-16): PASS

**Method.** `hub/spike/e2/`: `e2.html` (WebCodecs, mp4box.js 0.5.2 vendored for the sample tables), `serve.py` (local server with HTTP Range). Each camera: moov read with one 1 MB range request, then one range request per GOP (cached, next GOP prefetched); one `VideoDecoder` per camera, kept at most 6 frames ahead; frames placed by their real time (`first_frame_wallclock_ns` + sample time; conformed takes on their grid). The master clock is the audio output (`AudioContext.getOutputTimestamp`). Seeking decodes from the previous keyframe and throws away frames before the target. Takes copied from `STUDIO` to the Mac. User's Mac, Chrome 157, 8 cores.

**Take `20261116-072121`** (4 Pis + Pixel 9a, real timing, 30 s; the phone starts 4.8 s before the Pis; dim scene, Pi files ~0.7 Mbps):
- Open: all 5 cameras in 25 ms (one 1 MB request each).
- Seek test (30 random seeks): every camera showing the exact target frame after p50 **95 ms**, p90 140 ms, max 151 ms.
- Play (25.5 s, all 5 decoding, thumbnails + main view): every frame shown on every camera (744/744/745/745/665+19 pre-roll), **0 skipped, 0 late**; render loop never slower than 17.7 ms (60 Hz); audio output latency 32 ms.

**Take `20261114-202934`** (4 Pis, conformed by Phase 3 ingest: CRF 16, ~8.5 Mbps, B-frames, a keyframe every ~8.3 s; 15 min; `claptest.mp3` placed at −35.071 s from `clap_check.py` on cam1, mean of 6 claps):
- Open: 4 cameras in 37 ms (27518-frame sample tables from one 1 MB request each).
- **Claps in sync with the video (user, by eye and ear).**
- Play (60.2 s, all 4 decoding): 1806 frames shown per camera = 60.2 × 30, **0 skipped, 0 late**; render loop ≤ 17.7 ms; audio output latency 40 ms. Before that, the user's own playing and scrubbing: ~1700 frames per camera, 0 skipped, 0 late.
- Seek test: exact frame on every camera after p50 **312 ms**, p90 381 ms, max 483 ms. Slower than on the copied take because the conformed files have a keyframe only every ~250 frames, so a seek decodes up to 8 s of video (and fetches ~16 MB per camera). Takes ingested from now on keep the cameras' own ~2 s keyframe spacing, so they seek in ~0.1 s, as take `20261116-072121` showed.

**Result: PASS.** WebCodecs plays 4 cameras at normal bitrate, and 5 with a 14 Mbps phone, frame-accurately in sync with audio on the user's Mac, with exact-frame seeking. Not tested: 5–6 cameras all at full bitrate at once (no such take yet); the page shows `late_ticks` if that ever falls behind (then: lower-resolution thumbnails). Notes for the editor: keep the tab visible (Chrome stops drawing in hidden tabs; the page flags it); a played-back file needs a sample table, so mp4box.js (or our own moov parser) is part of the editor.

## E3: render cost on the hub (2026-11-16): PASS

**Method.** `hub/spike/e3/e3.py` on the hub, reading take `20261114-202934` from `STUDIO` under the playback lease (read-only mount). A 4-minute cut from 60 s in: 33 cuts, 4–10 s each, cameras in a fixed pseudo-random order; a 33³ `.cube` LUT per camera (mild exposure, warmth and saturation changes); audio = 48 kHz stereo noise standing in for the bounce, AAC 320 kbps; `.mp4` with faststart. The script stops at once if the rig leaves idle. Hub: Pi 5 with **1 GB RAM**.

**Two pipelines:**
- *concat*: one ffmpeg, each cut its own input, joined with the concat filter. 541 MB peak with only 4 cuts (every input's decoder is open at once), so with 30+ cuts per song it would run the 1 GB hub out of memory. Dropped.
- *pipe*: one decoder process per cut, one after another (input seek, `fps=30`, LUT), raw frames piped into one encoder. Memory stays flat whatever the number of cuts. **Chosen.**

| 4-minute render (pipe) | × real time | 4-min song | Mbps | Peak MB (largest process) | Max °C |
|---|---|---|---|---|---|
| veryfast CRF 16, LUT | 0.53 | 7.5 min | 7.5 | 316 | 62.6 |
| veryfast CRF 16, no LUT | 0.79 | 5.1 min | 8.0 | 363 | 63.1 |
| faster CRF 16, LUT | 0.45 | 9.0 min | 7.8 | 444 | 64.8 |
| superfast CRF 16, LUT | 0.54 | 7.4 min | 16.9 | 307 | 63.1 |
| veryfast CRF 18, LUT | 0.58 | 6.9 min | 5.5 | 322 | 63.7 |

Every render: exactly 7200 frames (30 × 240), 30/1, duration 240.000 s. No throttling (`0x0`) in any run.

- **The LUT is the bottleneck, not the encoder:** superfast is no faster than veryfast (and doubles the size). On 40-second cuts, an explicit planar-RGB format, 4 filter threads, a 17³ LUT and trilinear interpolation all came out at 0.54–0.59×, the same within noise; only `nearest` was faster (0.69×) and it bands. So a camera with no colour changes should skip the LUT (0.8–0.9×).
- **Preset: veryfast, CRF 16** (the ingest's old setting; YouTube re-encodes anyway, CRF 18 is the fallback if file size matters). About 1.9 s of hub time per second of output with colour: a 4-minute song in ~7.5 min, an hour of finished video in ~2 h. Overnight, like ingest.
- Not measured: real-timing sources (only this conformed take is long enough). The per-segment `fps=30` step that puts them onto the output grid was in every run; on real-timing input it does real work, but it's cheap next to the LUT.
- Output kept on the hub for a look: `~studio/e3-spike/out/keep-veryfast.mp4` (226 MB; noise for audio).
