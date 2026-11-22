# Version 1.0

2026-11-22. **The project is finished. There is no backlog and no roadmap.**

## What 1.0 is

- Synchronised takes from Raspberry Pi cameras, with Android phones as extra cameras, and LIVE to OBS.
- Verified ingest to a USB drive, every frame at its real time, one timecode per take.
- Calibration by showing a card, focus help, health monitoring and a recording light.
- Bounces per song with versions, synced automatically from the room mic or by hand.
- A browser editor: rule-based automatic cut, cut, look, title, start and end.
- A render on the hub, and optional private upload to YouTube.

## Closed without being built

These were ideas or backlog items during development. They were dropped at 1.0.

| Area | Dropped |
|---|---|
| Editing | The AI agent that listens, watches and cuts (automatic cut version 2); choosing the wide camera per take; a vertical story version |
| Picture | Automatic and per-camera colour matching; manual brightness and warmth trims; fixes to the phones' optional `locked` calibration mode; autofocus and focus checks during takes |
| Sync | Sync from claps alone; LTC timecode; the phones' own audio for sync |
| Getting files around | Network share; Mac auto-pull; pulling only the cameras that fit on the hub; salvaging takes cut short by a power loss |
| Control | Physical REC button; tally lights on the cameras; OBS scene export; voice control and notifications |
| Platform | HTTPS on the hub; iPhone support; live-latency tuning |

## Known issues

Faults in 1.0, listed so that anyone using it knows about them.

- **The hub and camera APIs have no login.** Requests from other websites are refused, but anything else on the network can send commands. Use a trusted network.
- **A Pixel 3 dropped to about 25 fps** on automatic exposure in dim light. Add light.
- **Occasional short frame stalls** on the Pi cameras, cause unknown. They are recorded and don't affect sync.

## Not verified

Built, but with no recorded end-to-end test.

- Automatic sync and the automatic cut on a full-band session. Sync was accurate on a sparse test take.
- A real upload to YouTube. It was tested against a fake YouTube, and the sign-in against Google.
- A phone losing Wi-Fi, or reloading, in the middle of a take.
- Deleting a take from a real exFAT drive.
- Moving a drive to a freshly installed hub.
- LIVE with a gain above 16.
- A fresh install on blank cards, more than four Pi cameras, and 60 Hz mains.
