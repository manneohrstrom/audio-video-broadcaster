# Plan: the stick is the database

> **Closed at 1.0 (2026-11-22).** This is a record of the work as planned, kept for reference. Nothing in it is still to do: see [release-1.0.md](release-1.0.md).

Status: accepted 2026-11-17 (user). Agreed in outline on 2026-11-16 (`docs/progress.md`).

## Goal

Everything lasting about a take lives in its folder on the `STUDIO` drive. Deleting the folder (on the Mac, say) deletes the take. Plugging the drive into a freshly set-up hub shows all its takes, with their songs, syncs, edits and videos. The hub's SD card keeps only caches (thumbnails, proxies, waveforms) and work in flight (takes not ingested yet, uploads, renders).

Unchanged: the hub never deletes from the drive; it mounts the drive read-write only while writing, read-only while reading; uploads, ingest and drive writes wait while a take or live session runs.

## What is on the SD card only, today

| What | Where now | After |
|---|---|---|
| The take-log record (REC/STOP, cameras, sync warnings, errors, calibration, phones) | `/var/lib/hubd/takes.json` | also in `take.json` on the drive, as `recording` |
| The editor's working cut | `/var/lib/hubd/edits/<take>/<song>.json` | also `edits/<song>/working-edit.json` on the drive |
| Which takes exist, their songs and videos | take log + ingest's `state.json` | an index of the drive that's plugged in |

Bounces, `songs.json`, `sync.json` and the scratch mic are already copied to the take's `audio/`; videos and their edits are in `edits/<song>/`.

## Steps

**S1. `take.json` gets the recording record.** Ingest copies the take-log entry into `take.json` (`recording`) when it writes `take.json`. Takes already on the drive get it the next time the drive is mounted read-write. Tests: written, merged, backfilled.

**S2. A drive index.** On plug-in (after the filesystem check) and every few minutes while idle (the same read-only look that already finds deleted takes), ingest reads every `takes/*/take.json`, `audio/songs.json`, the `sync.json` files and `edits/` into `drive_index.json` on the SD card: one small file, keyed by the drive's filesystem id. Only small JSON files are read; no video is touched.

**S3. The hub page lists the drive index plus what's in flight.** Takes still being recorded, pulled or ingested come from the take log and ingest's state, as now. Ingested takes come from the drive index. A take ingested to another drive isn't listed while that drive is out (see question 1).

**S4. The working cut goes to the drive.** The editor keeps saving to the SD card (fast, every change). Ingest copies a changed working cut to `edits/<song>/working-edit.json` about two minutes after the last change, when the rig is idle (read-write mount, temporary name, rename). On open, the editor uses the newer of the two; with no SD copy (a new hub, or the take came in on the drive), it uses the drive's.

**S5. Takes that arrive on the drive.** For a take in the index that the hub doesn't know (drive moved from another hub, or the SD card re-flashed): ingest copies its `audio/` back to the SD staging area (small: bounces and JSON), so sync and render work unchanged, and makes the caches (thumbnails, proxies, waveforms) the first time the take is opened in the editor, at idle, one at a time.

**S6. Docs.** `docs/design.md` (storage), the runbook, `docs/progress.md`; versions bumped (hubd, hub-ingest).

CPU: the index reads small files; S5's proxies are the same work ingest does for every take now. Nothing here adds load during a take.

## Decisions (user, 2026-11-17)

1. A take ingested to a drive that isn't plugged in: one line on the page ("3 takes on another drive").
2. No drive plugged in at all: the last drive's takes, greyed out ("drive not plugged in": no playback, no editing).
