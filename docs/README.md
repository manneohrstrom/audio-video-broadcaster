# docs/: design and project history

New here? Start with the [main README](../README.md). The project is finished at [version 1.0](release-1.0.md); these are its working documents. **The plans, spikes and results are records of their date** and can describe things that changed later. Where they disagree with the code, the code wins.

## Using and understanding the rig

| File | Read it when you want to know… |
|---|---|
| [`release-1.0.md`](release-1.0.md) | What 1.0 is, its known issues, and what was left out |
| [`runbook.md`](runbook.md) | How to run the rig: mounting a camera, OBS, the drive, phones, the editor's browser setting, the recording light |
| [`design.md`](design.md) | Why the system is built this way: hardware limits, sync, ingest, storage, risks. It began as the proposal of 2026-11-07, so some details (shutter, ingest method, number of cameras) have since moved on. |
| [`../CLAUDE.md`](../CLAUDE.md) | The decisions that are settled, with dates. It is also the instruction file for the AI assistant used on the project. |
| [`versions.md`](versions.md) | The pinned package versions and bundled libraries |
| [`progress.md`](progress.md) | What happened during development: a dated log, newest at the bottom |

## Project history

| Files | Topic |
|---|---|
| [`phase1-results.md`](phase1-results.md) | Feasibility: does camera sync hold for an hour, and does Wi-Fi carry the bandwidth? |
| [`phase2-plan.md`](phase2-plan.md), [`phase2-results.md`](phase2-results.md) | Control: REC, STOP, LIVE, the camera agent, Ansible |
| [`phase3-plan.md`](phase3-plan.md), [`phase3-results.md`](phase3-results.md) | Ingest, the frame-timing problem and the drive rules |
| [`phone-spike-plan.md`](phone-spike-plan.md), [`phone-spike-results.md`](phone-spike-results.md), [`phone-plan.md`](phone-plan.md) | Android phones as cameras |
| [`phase4-plan.md`](phase4-plan.md), [`phase4-spikes.md`](phase4-spikes.md) | Calibration, audio sync, recording light |
| [`editor-plan.md`](editor-plan.md), [`editor-spikes.md`](editor-spikes.md) | The editor, automatic cut, looks, titles and render |
| [`stick-db-plan.md`](stick-db-plan.md) | The USB drive as the record of every take |
| [`youtube-plan.md`](youtube-plan.md) | YouTube uploads, including how to set them up |

## Key facts in one place

- **Sync between cameras:** within about a frame over an hour, and typically tens of microseconds at the start. A start that is more than a frame out is restarted automatically, up to twice; then it records with a warning.
- **Real frame rate:** about 30.011 fps, not 30.000. Ingest keeps every frame at its real time, and the render puts frames on an exact 30 fps grid locked to the mix.
- **Dropped frames:** recorded in each take's manifest, marked as rough sections and avoided by the automatic cut. They don't shift later frames.
- **Wi-Fi is the limit, not CPU:** about 30 Mbps of camera traffic was tested without loss. Aim for −60 dBm or better at each camera.
- **Ingest speed:** there's no re-encode, so pulling over Wi-Fi is the slow part.
- **Render speed:** depends on the look and effects, and can be several times the song's length on the Pi 5.
