# Phase 3 results: ingest

Date: 2026-11-15. Plan: `docs/phase3-plan.md`. Detailed log: `docs/progress.md`.

**Verdict: all five exit criteria pass on real hardware, with three deviations from the criteria as written (below). Two bugs were found by the tests and fixed. Waiting for the user's GO.**

## Exit criteria

| # | Criterion | Result |
|---|---|---|
| 1 | A ≥30-min multi-camera take with a dropped frame is ingested unattended, verifies, opens in Premiere as a multicam by timecode, and claps line up within 1 frame against each other and the DAW | **Pass, with deviations.** The S1 take `20261114-202934` (15:17, four cameras, cam2 dropped 3 frames and cam4 5) was ingested and verified: all four `.mov` files have exactly 27518 frames and timecode 20:29:40:06. Clap check on the final files against the DAW bounce: every camera within 1 frame at +389 s and +465 s (−31 to +29 ms), and every clap on the same frame on all cameras (±1, the measurement's resolution). Unattended start after 30 min idle shown on take `20261115-192629` (four cameras, 1201 frames each, same timecode). |
| 2 | REC or LIVE during ingest pauses it at once without affecting the take; ingest resumes without re-pulling finished files | **Pass.** REC at 37% of a conform: ffmpeg was SIGSTOPped at once (state T), the 43 s take recorded with 0 drops, and the conform resumed after STOP. The page now says "paused: rig busy" meanwhile. |
| 3 | Pulling the drive or cutting power mid-write leaves no complete-looking file that isn't; the next ingest redoes it | **Pass after a fix.** The user pulled the stick during cam2's copy: only `cam2.mov.part` (243 MB) was left, no `cam2.mov`. **Bug found:** fsck only ran at the start of a round, so the next camera's copy would have written to the damaged stick. Fixed: the drive is re-checked before every write. After a repair on the Mac, the next ingest redid cam2's copy and finished the take. |
| 4 | Cameras delete only verified takes, oldest first, only below a free-space threshold; the hub never deletes from the drive | **Pass.** cam1 with the threshold raised to 30 GB: all 9 verified takes deleted oldest first, each logged, stopping when none were left; restored to 10 GB. The hub has no code path that deletes from the drive. |
| 5 | Eject unmounts cleanly; a drive without the `STUDIO` label is never written to | **Pass.** Eject: cache flushed, USB port powered off, "safe to unplug"; the replugged stick was checked and accepted. The drive is only ever found by `/dev/disk/by-label/STUDIO`. |

## Deviations from the criteria as written

- **15 minutes, not 30.** The S1 take was reused instead of recording a new 30-min session (discussed with the user 2026-11-15). The drift measured is linear and well within a frame over 15 min.
- **No Premiere check.** The user has no Premiere at the moment. The multicam-by-timecode property was checked directly instead: identical timecode and frame count on all four cameras, and the clap frames agree.
- **Cutting power mid-write** was not tested separately; the stick pull covers the same failure (an interrupted write to exFAT).

## Measurements

| | Result |
|---|---|
| Conform speed (libx264 veryfast CRF 16) | 0.92 s per video second per camera; pull 0.61; verify 0.15; copy to stick 0.02 |
| Quality, conformed against the camera's own stream | VMAF 96.5 (1% low 96.1), SSIM 0.995, PSNR 51.5 dB: visually transparent |
| Pull, three cameras in parallel over Wi-Fi | 2.8 GB in about 10 min |
| Hub temperature | Peak 68 °C during conform, fan on its lowest steps, no throttling |
| Camera power (final supplies) | `throttled=0x0` on all four, also while recording |

## Also built in Phase 3 (user requests)

- Ingest page: step checklist per camera, progress bars, ETA (learned per step), take sizes.
- Playback of takes from the stick on the hub page: read-only mount while playing (the settled storage rule was changed for this, with the user's OK), seeking, camera tabs.
- Hub card (temperature, fan, CPU, throttling, SD free); offline cameras grey, recording red; a camera counts as offline only after 3 missed polls.
- `hub/tools/clap_check.py`, the sync check used for criterion 1.

## Findings for later

- The sensor's frame length moves in 2-line steps: 30.0107 fps (+1.28 s/h) or 29.9866 fps (−1.61 s/h). Neither gets within 0.5 s/h, so gapless takes can't skip conversion by changing the frame rate (user decision: keep converting). Copying at the native rate remains an option once an editor is chosen.
- The DAW interface's clock may differ from real time by about 30–60 ppm. Clap at the end of long takes, or use the Phase 4 scratch-mic sync.
- cam2 (−64 dBm) and cam4 (−61 dBm) are slightly below the −60 dBm Wi-Fi target at their final spots.
- Network share and Mac auto-pull are deferred (notes in the plan).
