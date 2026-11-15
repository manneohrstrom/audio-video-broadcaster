# docs/: design and project history

Where to look, depending on what you want to know:

| File | Read it when you want to know… |
|---|---|
| [`design.md`](design.md) | **Why the system is built this way.** Hardware limits, live and offline modes, how sync works, ingest, storage, the risks, and the phase plan (including the planned Phase 4 features). Start here. |
| [`progress.md`](progress.md) | **What happened and what's next.** A dated log, session by session, with measurements, problems found and fixes. The "Next" sections are the current to-do list. |
| [`versions.md`](versions.md) | Exactly which package versions are installed and pinned on the Pis, and why |
| [`phase1-results.md`](phase1-results.md) | Phase 1 feasibility: does software camera sync hold over an hour, and does Wi-Fi carry the bandwidth? (Yes, with five conditions.) |
| [`phase2-plan.md`](phase2-plan.md), [`phase2-results.md`](phase2-results.md) | Phase 2 control (REC/STOP/LIVE, camera agent, Ansible): plan and results |
| [`phase3-plan.md`](phase3-plan.md) | Phase 3 ingest: the frame-timing problem (the sensor runs at 30.011 fps, plus dropped frames), the chosen fix (conform to an exact 30 fps grid by timestamp), and milestones |

## Key facts in one place

- **Sync between cameras:** within about a frame over an hour, and typically tens of microseconds at the start (Phase 1 and 2 measurements).
- **Real frame rate:** about 30.011 fps, not 30.000. Ingest corrects for this, so the video stays in sync with the studio audio.
- **Dropped frames:** rare, mainly on sync-client cameras under Wi-Fi load. They're recorded in each take's manifest and handled at ingest without losing sync.
- **Wi-Fi is the limit, not CPU:** about 30 Mbps of camera traffic was tested without loss, and offloading runs at about 4.5 Mbps per camera.
- **Ingest speed:** conforming runs at about real time per camera on the Pi 5, so it's done overnight.
