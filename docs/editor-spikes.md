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
