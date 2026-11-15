# Runbook

Short how-tos for running the rig. The Phase 4 polish track (`docs/phase4-plan.md`, C2) adds the rest: mounting checklist, Wi-Fi per spot, OBS, drive repair.

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
4. Point it at the scene and tap **Auto, then lock** (about 20 s). It sets the shutter to the rig's 1/50 s and picks ISO, white balance and focus to match what auto chose, then keeps them fixed. If it says the room is too dark, add light.
5. Leave the page in front with the screen on. Press REC/STOP on the hub page as usual; ready phones record with the cameras. A phone that opens late, reloads or loses Wi-Fi joins in by itself.

**On the hub page** each phone has a card: recording, frames and drops, upload backlog, picture locked, battery. Warnings (not charging, page not in front, not locked, slow Wi-Fi, low space) never stop REC.

**Afterwards** the phone finishes uploading by itself (keep the page open until its card says up to date). Ingest turns it into `<name>.mov` (e.g. `pauls-pixel.mov`) next to `cam1.mov` on `STUDIO`, on the same timecode. If the phone rejoined mid-take, the extra piece is `<name>-part2.mov`.

**Troubleshooting**
- "Camera blocked": the Chrome flag (step 2) is missing or lists the wrong address.
- "already in use by another phone": two phones have the same name; change one.
- Hub space: each phone uses ~7 GB per hour on the hub's SD until ingest has run. The phone card warns when space is short.
- Dropped frames are kept and marked: the take list shows "rough sections" per camera, to avoid in the edit.
