# Runbook

Short how-tos for running the rig.

## Mounting a Pi camera (checklist)

For a new camera spot, or after moving one. Everything except step 6 shows on the camera's card on the hub page.
1. **Power:** its own proper supply. The card must not show throttling (`throttled` `0x0`, or `0x50000` = happened once since boot).
2. **Wi-Fi:** −60 dBm or better at the spot (red on the card below −65), and a link rate of tens of Mbit/s. A weak spot (cam1 at −72 dBm once) gives multi-second pings and dropped live frames. Move the camera or the access point before anything else. Power save must show **off**.
3. **Clock:** "synced", offset well under 1 ms (chrony; takes a minute after boot).
4. **Temperature:** under 70 °C at rest. Throttling starts at 80 °C.
5. **Nothing in front of the lens:** check the picture on the cameras view (`/snap`). A mic or stand at the edge of the wide lens is easy to miss (cam2 and cam4 had one).
6. **Focus:** on `/snap`, "find sharpest" on the spot where the players will be, then "save focus". The Pis have one fixed focus each.
7. **Colour:** show the calibration card (`hub/calib/calibration-card.pdf`, printed A4 landscape at 100%) to the camera while a hub page is open; it locks on its own ("✓ calibrated"). The hub page sounds it out (click the page once first so the browser allows sound): a short blip when the card is found (hold still), a rising chime when done, a low falling buzz on a problem (the reason shows on the camera's card). To calibrate again (other light, other spot), lower the card for a few seconds and show it again: a camera recalibrates only after it has seen the card leave. Hold the card where the performers will be, in the session's light.
8. A short test take: no dropped frames on the card, and the take ingests.

## OBS (LIVE)

- Add each camera as a Media Source with its `srt://camN.local:8890?streamid=read:cam` URL (copy it from the camera's card). RTSP over TCP is the fallback.
- **Untick "Close file when inactive"** in every camera's Media Source. Otherwise OBS closes and reopens the stream on every scene switch: one camera at a time and slow switching.
- Use the Multiview to watch all cameras. Each extra viewer of a camera (the card's preview link, a second OBS) costs that camera a full stream on its Wi-Fi.

## STUDIO drive

- **Eject before unplugging:** "eject drive" in the archive, then wait for "Drive ejected: safe to unplug". The hub only mounts the drive while it reads or writes, but Eject also flushes and powers the port down.
- **On the Mac:** eject it in Finder before unplugging.
- **"drive needs repair (fsck)" on the hub page** (after it was pulled out mid-write, or not ejected on the Mac): plug it into the Mac, Disk Utility → select the STUDIO volume → **First Aid**, then eject and plug it back into the hub. Ingest redoes whatever it was writing; a leftover `*.part` file is an unfinished copy and can be ignored.
- **The drive is the record** (`docs/stick-db-plan.md`): to delete a take, press **delete take** in the archive, or delete its folder on the drive on the Mac. Within about 10 minutes of the drive being back in the hub, the take is gone from the page and the hub's caches. The hub page lists what's on the plugged-in drive; "N takes on another drive" counts takes ingested to a different drive; with the drive on the Mac, its takes are greyed out. The editor's working cut is saved to `edits/<song>/working-edit.json` about 2 minutes after the last change.
- The hub deletes from the drive only on **delete take**. Free space is shown on the hub page (last reading while plugged in) with how many hours of takes still fit.

## Bounces and videos

- **Add a bounce:** "add a bounce" on the take in the archive, pick a song (or "New song…"), choose the file (WAV, AIFF, FLAC, MP3 or M4A). Uploading the same song again adds a version; every version is kept, the newest is used unless you pick another ("use this"). It waits during takes and LIVE and carries on by itself. Closed the page mid-upload? Pick the same file for the same song again and it continues.
- **Sync** runs by itself once the upload is in. "needs sync" means it wasn't sure: sync by hand in the editor, on a clap or a drum hit.
- The hub copies bounces (and the hub mic recording) to the drive with the next ingest: `takes/<take>/audio/<song>/v<N>/`.
- **Make video** is in the editor. The hub renders as soon as the rig is quiet, with the take's drive plugged in; a take or LIVE pauses it. The video lands in `takes/<take>/edits/<song>/`; watch or download it from the archive. Making it again gives a new video next to the old one.
- **Instagram story:** the editor's **Story** step. Drag on the video to place the 9:16 slice for the camera that's showing (or use the sliders), set the story's start and end with <kbd>I</kbd> and <kbd>O</kbd>, then **Make story**. It shows in the archive as "story N · 9:16 story": open the hub page on the phone, download it, post it from the phone's files. A 1080x1920 slice of a 1080p camera is scaled up about 1.8 times, so it's softer than the YouTube video.

## Phone as a camera (offline takes)

Any Android phone with an up-to-date Chrome works (tested: Pixel 3, Pixel 9a). iPhones don't: every iOS browser lacks the capture feature the page uses.

**Once per phone**
1. Update Chrome.
2. In Chrome, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`, enter `http://studio-hub.local`, set it to **Enabled**, tap **Relaunch**. (Chrome only gives web pages the camera over HTTPS or on listed addresses.)
3. If the hub page won't load: Android Settings → Apps → Chrome → Permissions → allow **Nearby devices / local network**.
4. Join the 5 GHz Wi-Fi (not the cameras' 2.4 GHz network).

Each phone makes itself an id once (`phone-k7q2`, kept by Chrome) and shows up on the hub page by that id. Its files are `<id>.mov`; the name people see is given with the pencil on the hub page (or typed on the handheld page) and is kept. Clearing Chrome's site data for the hub gives the phone a new id.

**Tripod phones (in a bracket, on the power, studio only)**

Once per phone, on top of the steps above:
1. Android Settings → Developer options → **Stay awake** (screen never sleeps while charging). Developer options: Settings → About phone → tap Build number 7 times.
2. Pixel: Settings → Battery → **Adaptive charging / charging optimisation** on (keeps it from sitting at 100 % for hours).
3. Scan the **Tripod** QR code on the hub page (📱 Use a phone as a camera), or open `http://studio-hub.local/phone/tripod`. Tap the screen and allow the camera (**Allow while using the app**, not only this time).
4. Chrome menu ⋮ → **Add to Home screen** / **Install app** (works, user 2026-11-21). Opened from that icon, the page is full screen and landscape by itself: no touch at all.

Then on the hub page, Monitor: give it a name with the pencil ("Left wide"). If you can't tell the phones apart, **identify** flashes that phone's screen white with its name for a few seconds.

Each session: open the home-screen icon. It starts its camera by itself. The screen stays dark: the name and a saying when idle (the AIM key shows the picture with guides and a level), a cassette and counter while recording, a green DONE when the hub has the take. Turning the phone sideways (or back) reopens the camera the right way round by itself, between takes,. Focus is locked (the tripod page sets **focus locked**). If a phone misbehaves, **reload page** on its Monitor card reloads it between takes; it comes back and starts its camera by itself (tap once more for full screen unless it was opened from the home-screen icon). To get the full page on the phone, hold the screen for 3 s (between takes).

**Handheld phones, each session**
1. Plug the phone in and mount it in landscape.
2. Open `http://studio-hub.local/phone` (or the **Handheld** QR code under **📱 Use a phone as a camera** on the hub page).
3. Optionally type a name ("Paul's Pixel"); it's the same name as on the hub page. Tap **Start camera**.
4. Set its focus switch: **focus locked** (on a tripod: measured once, then locked) or **focus follows** (handheld: continuous autofocus). Exposure and white balance are automatic, also during a take, so phones don't need the card. (With `hubd_phone_picture: locked` in `deploy/group_vars/hub.yml` they calibrate from the card instead, or lock at REC.)
5. Leave the page in front with the screen on. Press REC/STOP on the hub page as usual; ready phones record with the cameras. A phone that opens late, reloads or loses Wi-Fi joins in by itself.

**On the hub page** each phone has a card: recording, frames and drops, upload queue, picture, battery. Warnings (not charging, page not in front, slow Wi-Fi, low space) never stop REC.

**Afterwards** the phone finishes uploading by itself (keep the page open until its card says up to date). Ingest turns it into `<id>.mov` (e.g. `phone-k7q2.mov`) next to `cam1.mov` on `STUDIO`, on the same timecode. If the phone rejoined mid-take, the extra piece is `<id>-part2.mov`.

**Troubleshooting**
- "Camera blocked": the Chrome flag (step 2) is missing or lists the wrong address.
- "already open in another tab": the phone page is open twice on that phone; close one.
- Tripod page says "tap to start the camera" after a reload: the camera permission was only allowed once; tap, and choose **Allow while using the app**.
- Hub space: each phone uses ~7 GB per hour on the hub's SD until ingest has run. The phone card warns when space is short.
- Dropped frames are kept and marked: the take list shows "rough sections" per camera, to avoid in the edit.

## Editor (full video)

The editor is at "edit video" on a synced song in the archive. Its timeline works in any browser; the **full-quality video** uses WebCodecs, which browsers only allow on secure pages (HTTPS or localhost). The hub is plain `http://studio-hub.local`, so on each computer that edits, once:

1. In Chrome, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.
2. Add `http://studio-hub.local`, set it to **Enabled**, relaunch Chrome.

Without it the video window shows enlarged strip thumbnails and says so.

## Recording light (smart bulb)

hubd drives a LocalBytes RGB bulb from the rig state (`hub/hubd/lamp.py`, Phase 4 C1): off when nothing is happening (ready; user, 2026-11-16), amber arming, red recording (at 85 %, breathing slowly; user, 2026-11-20), blue live, pulsing purple ingest running (user, 2026-11-16), off again when ingest is done (the purple "safe to eject" look is switched off; user, 2026-11-16), flashing red error (camera offline, REC failed, new dropped frames, drive full or needing repair). The bulb is only ever told what to show; if it's off or unreachable, hubd logs it once and carries on.

Setting it up (once):
1. Join the bulb to the studio Wi-Fi (its own setup AP: Tasmota `tasmota-XXXX`, ESPHome a captive portal), then open `http://<bulb-ip>/` to see which firmware it runs.
2. Give it a DHCP reservation on the router.
3. In `deploy/group_vars/hub.yml` set `hubd_lamp`: `type: tasmota` (or `esphome` plus `light_id`, the light's id as shown on its web page) and `address: <ip>`. Deploy the hub.
4. Check each look: `curl -X POST studio-hub.local/api/lamp/preview -d '{"look":"recording","seconds":5}'` (looks: ready, arming, recording, live, ingest, ingest_done, error, off). `curl studio-hub.local/api/lamp` shows the current look, why, and whether the bulb answers.
5. Keep the lamp out of shot or dim: coloured light spills onto the scene, and white balance is fixed per take. Brightness per look can be changed under `hubd_lamp.looks` (e.g. `ready: {dim: 5}`).

Dry run without a bulb: `type: log` writes what it would send to the hubd journal.
