# docs/: design and project history

Where to look, depending on what you want to know:

| File | Read it when you want to know… |
|---|---|
| [`design.md`](design.md) | **Why the system is built this way.** Hardware limits, live and offline modes, how sync works, ingest, storage, the risks, and the phase plan. Start here. |
| [`runbook.md`](runbook.md) | **How to run the rig.** Mounting a camera, OBS, the `STUDIO` drive, bounces and the quick mix, phones as cameras, the recording light. |
| [`progress.md`](progress.md) | **What happened and what's next.** A dated log, session by session, with measurements, problems found and fixes. The latest "Next" entries are the current to-do list. |
| [`versions.md`](versions.md) | Exactly which package versions are installed and pinned on the Pis, and why |
| [`phase1-results.md`](phase1-results.md) | Phase 1 feasibility: does software camera sync hold over an hour, and does Wi-Fi carry the bandwidth? (Yes, with five conditions.) |
| [`phase2-plan.md`](phase2-plan.md), [`phase2-results.md`](phase2-results.md) | Phase 2 control (REC/STOP/LIVE, camera agent, Ansible): plan and results |
| [`phase3-plan.md`](phase3-plan.md), [`phase3-results.md`](phase3-results.md) | Phase 3 ingest: the frame-timing problem (the sensor runs at 30.011 fps, plus dropped frames), the two ways to keep sync, the drive rules, and the results |
| [`phone-spike-plan.md`](phone-spike-plan.md), [`phone-spike-results.md`](phone-spike-results.md), [`phone-plan.md`](phone-plan.md) | Android phones as cameras from a web page: the feasibility spike and the plan that was built |
| [`phase4-plan.md`](phase4-plan.md), [`phase4-spikes.md`](phase4-spikes.md) | Phase 4: picture (calibration by card, snapshot and focus page), sound (bounce upload, hub mic, sync, the one-button quick mix) and polish (recording light, runbook), and the spikes behind them |
| [`editor-plan.md`](editor-plan.md), [`editor-spikes.md`](editor-spikes.md) | Our own editor on the hub: the edit format, automatic cut, timeline, colour and render, and the spikes (real-timing copy, browser playback, render cost) |

## Key facts in one place

- **Sync between cameras:** within about a frame over an hour, and typically tens of microseconds at the start (Phase 1 and 2 measurements).
- **Real frame rate:** about 30.011 fps, not 30.000. Ingest keeps every frame at its real time, and the render puts frames onto an exact 30 fps grid locked to the bounce, so the video stays in sync with the studio audio.
- **Dropped frames:** rare, mainly on sync-client cameras under Wi-Fi load. They're recorded in each take's manifest, marked as rough sections, and avoided by the automatic cut. Sync is never lost.
- **Wi-Fi is the limit, not CPU:** about 30 Mbps of camera traffic was tested without loss. Aim for −60 dBm or better at each camera.
- **Ingest speed:** copying at real timing is fast (no re-encode); pulling over Wi-Fi is the slow part.
- **Render speed:** about real time on the hub for a 1080p30 quick mix (the hub has 1 GB of RAM, so the render is tuned to stay well inside it).
