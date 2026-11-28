# The hub's SD card as transit only (plan, 2026-11-28)

**User, 2026-11-28:** the SD card on the hub is only temporary storage, meaning *in transit*.
Thumbnails, preview copies and the rest are part of the take's data, not caches. Once a take has
settled (nothing recording, syncing or being copied), everything about it, originals or
derived, is on the STUDIO drive and nowhere else.

This replaces "The hub's SD card keeps only caches and work in flight" (`docs/design.md`,
"Ingest and cleanup").

## What the SD card holds per take today

| Data | On the SD card | On the drive | After this plan |
|---|---|---|---|
| Pi originals, phone recordings | in transit (ingest) | `.mov` | unchanged |
| Hub mic (`scratch.wav`), bounces, `sync.json`, `beats.json` | staged and **kept** | copied | removed from the SD once on the drive |
| Thumbnails | **only here** (`thumbs/`, ~40 MB) | — | made on the drive |
| Preview copies + movement | **only here** (`proxies/`, 2.2 GB) | — | made on the drive |
| Bounce waveforms | **only here** (`waveforms/`) | — | made on the drive |
| Working edit | hub, every change | copied ~2 min later | hub copy removed once the drive has it |
| Rig state (`takes.json`, names, settings) | here | take-log record in `take.json` | unchanged: hub state, not take data |

## How it would work

1. **Ingest writes the derived files into the take's folder** on the drive:
   `<take>/preview/<camera>.mp4` and `.motion.json`, `<take>/thumbs/<camera>/`,
   `<take>/audio/<song>/v<N>/waveform.json`. Written via `.part` and renamed, while the drive is
   mounted read-write for ingest, as now. Made when the rig is idle, newest take first.
2. **hubd serves them from the drive.** The editor and the archive's thumbnails use the existing
   read-only playback mount (unmounted 5 min after the last request).
3. **Audio is staged on the SD card only until it's on the drive.** The sync and the beat
   analysis then read it from the drive. A bounce uploaded while the drive is out waits on the
   SD card, as now.
4. **Working edits:** saved on the hub as you edit (as now). The hub copy is deleted once the
   drive's copy is the same and the song hasn't been edited for an hour.
5. **Existing takes:** their thumbnails and preview copies are moved onto the drive once, when
   it's plugged in and the rig is idle. Nothing is remade.
6. **The record-time budget** gains the space the SD card no longer keeps.

## What changes for you

- **With the drive out, the hub has nothing to show for a take** beyond its name and recording
  details: no thumbnails in the archive, and the editor won't open. Rendering already needs the
  drive.
- **The drive does more reading while you edit:** up to 9 preview copies at once (about
  20 Mbit/s) and thumbnails, all from the USB stick. That's fine for a stick, and the planned
  1 TB drive does better. To be checked once on the real stick.
- **The drive is the only copy of everything,** derived data included. A drive lost means the
  take is gone, as is already the case for the originals.

## Decisions for the user

1. With the drive out: no thumbnails in the archive and no editor. OK?
2. Working edits: is "delete the hub copy an hour after the last edit, once the drive has it"
   right, or keep the hub copy until the take is deleted (a few KB each)?
3. Existing takes: move their caches onto the drive (proposed), or let them be remade there?
