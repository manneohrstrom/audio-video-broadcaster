# Runbook

Short how-tos for running the rig.

## Mounting a Pi camera (checklist)

For a new camera spot, or after moving one. Everything except step 6 shows on the camera's card on the hub page.
1. **Power:** its own proper supply. The card must not show throttling (`throttled` `0x0`, or `0x50000` = happened once since boot).
2. **Wi-Fi:** −60 dBm or better at the spot (red on the card below −65), and a link rate of tens of Mbit/s. A weak spot (cam1 at −72 dBm once) gives multi-second pings and dropped live frames. Move the camera or the access point before anything else. Power save must show **off**.
3. **Clock:** "synced", offset well under 1 ms (chrony; takes a minute after boot).
4. **Temperature:** under 70 °C at rest. Throttling starts at 80 °C.
5. **Nothing in front of the lens:** check the picture on the snapshot page (`/snap`). A mic or stand at the edge of the wide lens is easy to miss (cam2 and cam4 had one).
6. **Focus:** on `/snap`, "Find sharpest" on the spot where the players will be, then "Save focus". The Pis have one fixed focus each.
7. **Colour:** show the calibration card (`hub/calib/calibration-card.pdf`, printed A4 landscape at 100%) to the camera while a hub page is open; it locks on its own ("✓ calibrated"). Hold the card where the performers will be, in the session's light.
8. A short test take: no dropped frames on the card, and the take ingests.

## OBS (LIVE)

- Add each camera as a Media Source with its `srt://camN.local:8890?streamid=read:cam` URL (copy it from the camera's card). RTSP over TCP is the fallback.
- **Untick "Close file when inactive"** in every camera's Media Source. Otherwise OBS closes and reopens the stream on every scene switch: one camera at a time and slow switching.
- Use the Multiview to watch all cameras. Each extra viewer of a camera (the card's preview link, a second OBS) costs that camera a full stream on its Wi-Fi.

## STUDIO drive

- **Eject before unplugging:** "Eject drive" on the hub page, then wait for "Drive ejected: safe to unplug". The hub only mounts the drive while it writes, plays back or renders, but Eject also flushes and powers the port down.
- **On the Mac:** eject it in Finder before unplugging.
- **"drive needs repair (fsck)" on the hub page** (after it was pulled out mid-write, or not ejected on the Mac): plug it into the Mac, Disk Utility → select the STUDIO volume → **First Aid**, then eject and plug it back into the hub. Ingest redoes whatever it was writing; a leftover `*.part` file is an unfinished copy and can be ignored.
- The hub never deletes anything on the drive. Free space is shown on the hub page (last reading while plugged in) with how many hours of takes still fit.

## Bounces and the quick mix

- **Add a bounce:** "♪ Add audio" on the take, pick a song (or "New song…"), choose the file (WAV, AIFF, FLAC, MP3 or M4A). Uploading the same song again adds a version; every version is kept, the newest is used unless you pick another ("use this"). It waits during takes and LIVE and carries on by itself. Closed the page mid-upload? Pick the same file for the same song again and it continues.
- The hub copies bounces (and the hub mic recording) to the drive with the next ingest: `takes/<take>/audio/<song>/v<N>/`.
- **Make video:** appears once a song version is synced to the take. The hub renders a YouTube-ready file (1080p30, the bounce as soundtrack, a new camera every 10 s) when the rig is idle; a take or LIVE pauses it. It lands in `takes/<take>/edits/<song>/`, and "▶ Watch video" plays it from the drive. Making it again gives a new file next to the old one.

## Phone as a camera (offline takes)

Any Android phone with an up-to-date Chrome works (tested: Pixel 3, Pixel 9a). iPhones don't: every iOS browser lacks the capture feature the page uses.

**Once per phone**
1. Update Chrome.
2. In Chrome, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, enter `http://studio-hub.local`, set it to **Enabled**, tap **Relaunch**. (Chrome only gives web pages the camera over HTTPS or on listed addresses.)
3. If the hub page won't load: Android Settings → Apps → Chrome → Permissions → allow **Nearby devices / local network**.
4. Join the 5 GHz Wi-Fi (not the cameras' 2.4 GHz network).

**Each session**
1. Plug the phone in and mount it in landscape.
2. Open `http://studio-hub.local/phone` (or tap **📱 Use a phone as a camera** on the hub page).
3. Type a name ("Paul's Pixel"); the phone remembers it. Tap **Start camera**.
4. Mark it **tripod** (focus locked) or **handheld** (continuous autofocus). Then show it the calibration card while a hub page is open: it sets exposure and the nearest white balance from the card and locks them, like the Pis. Without the card, **Auto, then lock** (about 20 s) sets the shutter to the rig's 1/50 s and fixes what auto chose. If it says the room is too dark, add light.
5. Leave the page in front with the screen on. Press REC/STOP on the hub page as usual; ready phones record with the cameras. A phone that opens late, reloads or loses Wi-Fi joins in by itself.

**On the hub page** each phone has a card: recording, frames and drops, upload backlog, picture locked, battery. Warnings (not charging, page not in front, not locked, slow Wi-Fi, low space) never stop REC.

**Afterwards** the phone finishes uploading by itself (keep the page open until its card says up to date). Ingest turns it into `<name>.mov` (e.g. `pauls-pixel.mov`) next to `cam1.mov` on `STUDIO`, on the same timecode. If the phone rejoined mid-take, the extra piece is `<name>-part2.mov`.

**Troubleshooting**
- "Camera blocked": the Chrome flag (step 2) is missing or lists the wrong address.
- "already in use by another phone": two phones have the same name; change one.
- Hub space: each phone uses ~7 GB per hour on the hub's SD until ingest has run. The phone card warns when space is short.
- Dropped frames are kept and marked: the take list shows "rough sections" per camera, to avoid in the edit.

## Recording light (smart bulb)

hubd drives a LocalBytes RGB bulb from the rig state (`hub/hubd/lamp.py`, Phase 4 C1): dim green ready, amber arming, red recording, blue live, pulsing cyan ingest running, purple ingest done (safe to eject), flashing red error (camera offline, REC failed, new dropped frames, drive full or needing repair). The bulb is only ever told what to show; if it's off or unreachable, hubd logs it once and carries on.

Setting it up (once):
1. Join the bulb to the studio Wi-Fi (its own setup AP: Tasmota `tasmota-XXXX`, ESPHome a captive portal), then open `http://<bulb-ip>/` to see which firmware it runs.
2. Give it a DHCP reservation on the router.
3. In `deploy/group_vars/hub.yml` set `hubd_lamp`: `type: tasmota` (or `esphome` plus `light_id`, the light's id as shown on its web page) and `address: <ip>`. Deploy the hub.
4. Check each look: `curl -X POST studio-hub.local/api/lamp/preview -d '{"look":"recording","seconds":5}'` (looks: ready, arming, recording, live, ingest, ingest_done, error, off). `curl studio-hub.local/api/lamp` shows the current look, why, and whether the bulb answers.
5. Keep the lamp out of shot or dim: coloured light spills onto the scene, and white balance is fixed per take. Brightness per look can be changed under `hubd_lamp.looks` (e.g. `ready: {dim: 5}`).

Dry run without a bulb: `type: log` writes what it would send to the hubd journal.
