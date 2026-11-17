# Studio Multicam — Design Proposal

Nov 7, 2026 · @Manne

## Summary

Build it as 2 camera nodes to start, scaling to 6 (Pi Zero 2 W + Camera Module 3 Wide) run by a Pi 5 as a studio hub. (This proposal originally said Pi 4; the hub turned out to be a Pi 5 Model B.) Each node has two pipelines that never run at the same time: MediaMTX for live feeds into OBS, and `rpicam-vid` with Raspberry Pi's software camera sync for offline takes.

- **Live:** each camera serves 1080p30 H.264 on the LAN; OBS adds each one once, by a fixed URL (`cam1.local`, `cam2.local` …).
- **Offline:** one REC / STOP button on the hub's web page (optionally a physical button too). All cameras stay frame-locked for the whole take ([Raspberry Pi docs](https://github.com/raspberrypi/documentation/pull/4049/files)). *Phase 1 finding:* they don't always start on the same frame. In the 4-camera run one node began one frame early, so each node's start time has to be recorded rather than assumed (see Synchronisation).
- **Ingest:** after the session, overnight if need be, the hub pulls every file onto its external drive, rewraps it to `.mov` with a shared start timecode (no re-encode), verifies it, deletes it from the camera, and leaves the take on a drive you plug into the Mac. The shared timecode lines the angles up in the editor with no manual work.
- **Resolution ceiling is 1080p.** The Pi's hardware H.264 encoder tops out at 1920×1080, so "highest sensible" means 1080p at a high bitrate ([Raspberry Pi](https://www.raspberrypi.com/news/raspberry-pi-camera-module-more-on-video-capture/)).
- **The real constraint is the Zero 2 W's 2.4 GHz-only Wi-Fi, not compute.** It caps live bitrate and makes moving an hour of footage take hours, not minutes. That's acceptable here: takes move to the hub overnight, so it only limits live bitrate.

## Requirements and assumptions

Requirements, from the brief:

1. **Live mode:** every camera into OBS on the Mac over Wi-Fi at the highest sensible resolution; sources easy to find and wire up once.
2. **Offline mode:** one start/stop control; all cameras start together and stay in sync; best quality to local storage.
3. **After a take:** files onto the Mac in as few steps as possible, then deleted from the cameras.
4. **A Pi as the central point** is acceptable. The brief said Pi 4; the hub is a Pi 5.

Assumptions (correct any of these in review):

- 2 camera nodes to start, scaling to 4 and possibly 6; sessions up to about 2 hours.
- 30 fps throughout, matching the phones and OBS, with the shutter locked at 1/50 s; any whole multiple of 1/100 s avoids flicker from UK mains lighting.
- Studio audio is captured separately on your interface/DAW. The Pi cameras have no microphone.
- Pi 5 hub on wired Ethernet; takes land on a portable exFAT drive that moves between the hub and the Mac; the Mac is wired or on 5 GHz during live sessions.
- Everything stays on the home LAN: no cloud, no remote access, no YouTube from the hub in v1.

## Hardware reality

The Zero 2 W can do 1080p30 comfortably because encoding runs in hardware; everything above 1080p, and everything network-heavy, is where it runs out.

| Part | Limit | What it means for the design |
| --- | --- | --- |
| H.264 encoder | 1920×1080 max, in hardware ([Raspberry Pi](https://www.raspberrypi.com/news/raspberry-pi-camera-module-more-on-video-capture/)) | 1080p30 is the ceiling for both modes; 50 or 60 fps is only possible at 720p. Software encoding above that is unusable on this CPU. |
| Camera Module 3 sensor modes | 4608×2592 @ 14 fps, 2304×1296 @ 56 fps, 1536×864 @ 120 fps ([spec](https://electronics.semaf.at/Raspberry-Pi-Camera-Module-3-Standard-76)) | 1080p is scaled down from the binned 2304×1296 mode, so you keep the full field of view. 56 fps max leaves headroom over 30 fps, which the sync algorithm needs. |
| Wide lens | 120° diagonal, 102° horizontal, f/2.2, 2.75 mm, PDAF autofocus ([spec](https://littlebirdelectronics.com.au/products/raspberry-pi-camera-module-3-wide-2)) | Very deep depth of field: lock focus once per position rather than letting it hunt. |
| Wi-Fi | 2.4 GHz 802.11 b/g/n only, onboard antenna ([DigiKey](https://www.digikey.com/en/product-highlight/r/raspberry-pi/raspberry-pi-zero-2-w)) | All cameras share one 2.4 GHz channel's airtime. This sets live bitrate and offload time. |
| CPU / RAM | 4× Cortex-A53 @ 1 GHz, 512 MB | Fine for hardware encode plus a small agent. No CPU-heavy denoise or transcoding on the node. *Phase 1:* even the default colour denoise (`cdn_fast`) drops about 6% of frames at 1080p30 at normal clocks, so takes use `--denoise cdn_off` (0 drops in 62 min on four nodes). |
| USB | One USB 2.0 OTG port | The only route to wired Ethernet or a microphone. |
| Storage | 32 GB microSD, \~25 GB free after the OS | About 3.5 hours of recording at 16 Mbps (7.2 GB/hour). 16 Mbps is a cap: in Phase 1 a dim, static scene averaged about 8 Mbps (3.7 GB in 62 min), so real capacity is often higher. |

The sensor-mode and lens figures come from reseller pages that reproduce Raspberry Pi's spec sheet; I have not checked them against the official datasheet.

## Architecture

&#91;embedded content: studio multicam architecture · cameras, hub, Mac\]

The nodes only capture; the hub owns orchestration, time and storage; the Mac only consumes. Live video never passes through the hub, so a hub crash can't take down a live show. The control API is plain HTTP, so a physical button, a Stream Deck or a phone shortcut can drive it later.

## Mode A: live to OBS

Each node runs [MediaMTX](https://github.com/bluenviron/mediamtx) with its built-in Raspberry Pi camera source, and OBS pulls each camera as a Media Source. This exact pairing (Zero 2 W + Camera Module 3 + MediaMTX) is reported working in the community ([Core Electronics forum](https://forum.core-electronics.com.au/t/pi-zero-w-2-camera-3-cctv/22982)).

- **Finding sources:** fixed hostnames `cam1.local` … `cam6.local` (mDNS) plus DHCP reservations. You add each camera to OBS once and the scene collection keeps working across sessions. The hub page lists every URL with a copy button.
- **Transport:** SRT by default, because its packet recovery within a fixed latency budget suits Wi-Fi; RTSP over TCP as fallback. MediaMTX serves both.
- **Bitrate:** 1080p30 at 4–5 Mbps per camera. Six cameras at that rate is 24–30 Mbps of 2.4 GHz airtime, at or past what one channel carries in practice (my estimate), so with six the live bitrate may drop to 3–4 Mbps each. Phase 1 measures it. OBS re-encodes the programme anyway, so higher camera bitrates buy little.
- **Latency and audio:** expect roughly 0.3–1 s of video delay (estimate). The cameras share similar delay, so one Sync Offset on the studio audio source in OBS lines it up.
- **ISOs while live:** recorded on the OBS machine, which already captures each camera feed. The Pis don't record in live mode, which keeps their cards free for offline takes.

## Mode B: offline synchronised recording

REC starts a frame-locked take on every camera and STOP ends it. Underneath, the hub drives the software camera synchronisation built into `rpicam-vid`: one camera is the sync server, the rest are clients, and they can be separate Pis on the same network ([Raspberry Pi docs](https://github.com/raspberrypi/documentation/pull/4049/files)).

What happens on REC:

1. The hub checks every node is online, clock-synced, cool enough and has free space. If any check fails it refuses to arm and says which camera and why.
2. It stops live mode on any node running it, since only one process can own the camera.
3. It starts `rpicam-vid --sync client` on all nodes but one. Clients wait; Raspberry Pi recommends starting clients first for exactly this reason ([docs](https://github.com/raspberrypi/documentation/pull/4049.diff)).
4. It starts `rpicam-vid --sync server` on the last node. The server runs a fixed number of frames (100 by default, about 3.3 s at 30 fps), then every camera starts recording at the sync point. *Phase 1 finding:* this isn't exact to the frame. One of four nodes started one frame early (one extra frame, every frame shifted by one), so each node logs the wall-clock time of its own first frame.
5. The UI shows each camera recording, with elapsed time, recording time left and dropped frames.

On STOP the hub signals all nodes; each closes its file and writes a manifest (camera, wall-clock time of its first frame, frame count, checksum). Stop points differ by a few milliseconds, which the edit trims anyway.

**Format:** raw H.264 at 1080p30, about 16 Mbps, plus per-frame timestamps (`--save-pts`, [docs](https://www.raspberrypi.com/documentation/accessories/camera-software.html)). A raw stream survives a crash or power cut as a truncated but playable file; an MP4 written on the node can be unreadable if it isn't closed cleanly. The hub rewraps to `.mov` later without re-encoding.

The server role is assigned per take, so any node can lead if one is missing.

## Synchronisation

Four things have to line up, and each gets its own mechanism; only the last one needs anything from you.

| What must line up | Mechanism | Expected accuracy |
| --- | --- | --- |
| Frames across cameras | libcamera software sync. Clients keep adjusting frame timing to match the server for the whole take, so long takes don't drift apart ([libcamera](https://patchwork.libcamera.org/patch/22684/raw/)). | *Measured:* time-aligned within about 17 ms (the stopwatch's resolution) on all four nodes, with no drift from 25 to 61.5 min |
| Node clocks | chrony on every node, served by the hub. The sync algorithm passes wall-clock time between devices ([libcamera](https://patchwork.libcamera.org/patch/21512/)), so I inferred NTP quality bounds sync quality. *Phase 1:* one node's chrony reported offsets up to 41 ms during a Wi-Fi dropout, and its frames didn't shift, so frame sync seems less sensitive to NTP than inferred. Clocks still matter for timecode. | *Measured:* ±0.1–2.5 ms with the hub on Wi-Fi; 13–144 µs with the hub wired |
| Files on the editor's timeline | The hub stamps each `.mov` with a start timecode from that node's first-frame wall-clock time, rounded to the take's common frame grid. A single shared timecode isn't enough, because a node can start one frame early (Phase 1). | Identical across cameras once start offsets are applied |
| Video against studio audio | Default: a clap or slate at the start of each take. Planned (Phase 4): a scratch mic on the hub, matched against the DAW bounce. Optional: LTC timecode from the hub into a spare interface input, recorded by the DAW. | Clap: about a frame, by hand. Scratch mic: automatic when the room and the bounce share sound. LTC: automatic. |

The editor lines the take's clips up by **timecode**. Audio-waveform sync is not an option for these cameras because they record no sound.

**LTC in practice.** The hub plays 30 fps LTC, derived from its NTP clock, through a small USB audio adapter into one spare line input on the Behringer; the DAW records it on its own always-armed track. After a session you export the mix and the LTC track from the same start point and drop both into the take folder. The hub decodes the LTC with x42's [ltc-tools](https://github.com/x42/ltc-tools) and writes the start timecode into the mix's Broadcast-WAV header, so the editor places the audio with the cameras by timecode.

**Why not just NTP and a scheduled start:** that aligns the first frame, but each camera module runs off its own clock and the angles slide apart over an hour. I haven't measured the drift for Camera Module 3; the software sync makes it moot. This is reasoning, not a measurement.

## Ingest and cleanup

After STOP the hub does everything; your only step is pressing Eject and moving the drive to the Mac.

1. **Pull:** `rsync` over SSH from each node into `takes/<date>_<take>/`, resumable after any drop. It runs only when no take or live session is active, typically overnight, so it never competes with live video or sync traffic for airtime.
2. **Rewrap:** `ffmpeg -c copy` from raw H.264 to `.mov` at a fixed 30 fps, adding the shared start timecode and camera name. No re-encode, so it is quick on the Pi 5 (which has no hardware H.264 encoder, so it couldn't transcode cheaply anyway).
3. **Verify:** the `.mov` frame count must match the node's manifest; dropped frames (gaps in `--save-pts`) are flagged in the UI. Counts can differ between cameras by a frame at the start, which is expected and not an error.
4. **Delete from the node** only after verify passes, and lazily: each camera keeps a take until it needs the space, so a second copy survives as long as possible. A take that fails verify stays on the camera and the UI says so.
5. **Publish:** the take appears on the external drive as one folder with one `.mov` per camera. The hub never deletes from the drive; clearing old takes is your call, since you may be editing from it.

| Getting it to the Mac | Your steps | Notes |
| --- | --- | --- |
| Swap the drive (default) | Press Eject on the hub page, move the drive to the Mac | exFAT, so the Mac reads it directly; edit from it or copy off |
| Network share | Drag the take folder across | No swap; slower; the drive must be on the hub |
| Mac auto-pull (optional) | None | Copies finished takes over the network when the Mac is on the studio network |

**Swapping safely:**

- The hub mounts the drive read-write only while it is writing, so whenever the hub page shows it idle, it's safe to pull. Eject forces that state.
- Playback on the hub page (added 2026-11-15) mounts it read-only, and it's unmounted 5 minutes after the last playback. A read-only mount writes nothing and never marks exFAT dirty, so pulling the drive mid-playback is harmless.
- The hub writes only to a drive with its own label (for example `STUDIO`), never to a random USB stick, and resumes by itself when the drive comes back.
- While the drive is on the Mac, takes wait on the cameras (about 3.5 hours each at 16 Mbps), and REC warns before space runs out.
- **Record-time limit** (hubd 0.3.0, user request 2026-11-16): the hub page shows how long the next take can run, from every Pi camera's free space and the hub's SD card (phone uploads during the take, plus ingest's peak afterwards: every Pi camera pulled, and two working copies of the largest camera; 15 GB kept in reserve). During a take it counts down (amber in the last 5 min), and at zero hubd presses STOP itself. REC is refused below 2 min. The STUDIO drive is not a limit: ingest just waits. Code: `hub/hubd/budget.py`; settings under `budget` in the hub config.
- **The drive is the record of every take** (user, 2026-11-17, `docs/stick-db-plan.md`). Everything lasting about a take is in its folder: the cameras, `take.json` (with the take-log record under `recording`), `audio/` (bounces, syncs, the scratch mic), and `edits/<song>/` (videos, the edit each was made from, and `working-edit.json`, the editor's working cut, copied about 2 minutes after the last change). Deleting the folder deletes the take; the hub then forgets it and drops its caches. The hub page lists the takes on the drive that's plugged in, plus takes still in flight. Takes on another drive are counted in one line, and while the drive is out its takes stay listed, greyed. A drive moved to a freshly set-up hub brings its takes along: they're adopted as ingested, their audio is staged again, and their thumbnails and proxies are made when the editor first opens them. The hub's SD card keeps only caches and work in flight.
- exFAT has no journal, so a power cut mid-write can damage it. The hub writes to temporary names and renames when done, and checks the drive each time it's plugged in. Always eject on the Mac too.
- A portable SSD now suits better than a hard drive: it travels well, and should handle multicam scrubbing far better than a spinning disk (my inference). 2 TB holds about 55 hours of 5-camera footage.
- Power the drive separately. On a 3 A supply the Pi 5 limits all USB ports to 600 mA combined, too little for many bus-powered drives. The official 27 W supply raises this to 1.6 A. The current drive has its own supply.

**Offload time doesn't matter: it runs overnight.** For reference, a 1-hour take on 5 cameras at 16 Mbps (36 GB), with about 25 Mbps of usable 2.4 GHz throughput (my estimate):

| Setup | Data | Time until all files reach the hub |
| --- | --- | --- |
| Wi-Fi, 16 Mbps recordings | 36 GB | About 3.2 hours |
| Wi-Fi, 10 Mbps recordings | 22.5 GB | About 2 hours |
| USB Ethernet per node (\~90 Mbps each, in parallel) | 36 GB | About 11 minutes |

**Overnight routine.** Leave the cameras powered after a session. The hub pulls, rewraps and verifies each take onto its external drive, marks each camera as collected, and can then shut the cameras down.

- Masters stay at 16 Mbps, or up to about 20 Mbps if quality matters more than hours per card; transfer time no longer limits the bitrate.
- Each 32 GB card holds about 3.5 hours at 16 Mbps. On longer days the hub also pulls between sessions whenever it's idle.
- A 2 TB drive holds about 55 hours of 5-camera footage at 16 Mbps, or 46 hours with six. With six cameras each hour of takes needs roughly 4 hours to offload, so a long day can finish the next morning; the cards' 3.5 hours of space covers that.
- Until a take is backed up elsewhere, the drive is its main copy, and it now travels; the cameras' lazily deleted copies are only a short-term backup. Back up to the Mac or a second drive promptly.
- Recording no longer needs proxies, a dedicated access point, a board swap or PoE. A clean 2.4 GHz channel still helps live mode.

## Image consistency

Mismatched colour and brightness between angles is the most visible flaw in a multicam cut, and per-camera auto modes guarantee it. So every camera runs locked settings during a take, set once from the hub.

- **White balance:** fixed gains per camera (`--awbgains`), set by calibration with a card for each setup (2026-11-16: show every device the card while idle and it locks on its own; phones too; card readings are kept for the editor's colour match). See `docs/phase4-plan.md`, Track B.
- **Exposure:** fixed shutter and gain (`--shutter`, `--gain`). 1/50 s (the default) lets in twice the light, which helps the small sensor, and gives softer motion blur; 1/100 s is crisper. Both are whole multiples of the 100 Hz flicker from UK mains lighting, so no banding.
- **Focus:** a manual lens position per camera, set from a snapshot in the hub UI. Autofocus hunting mid-take is worse than slight softness, and the wide lens has deep depth of field. The Pis are fixed on the wall, so this is set once and kept; handheld phones use continuous autofocus, tripod phones lock focus (2026-11-16).
- **Profiles:** settings live on the hub per camera and are pushed when arming, so a moved camera keeps its look.
- **Left out of v1:** HDR mode (limited to 30 fps and untested with sync) and lens-distortion correction (the editor can add it if needed).

Option names `--awbgains`, `--shutter` and `--gain` are from the [Raspberry Pi docs](https://www.raspberrypi.com/documentation/accessories/camera-software.html); the manual-focus options (`--autofocus-mode manual --lens-position <dioptres>`) were confirmed in Phase 1.

## Network, power and thermals

Most failures in a rig like this are radio and power, not software, so the hub watches both and blocks REC when either looks bad.

- **Wi-Fi:** give the cameras the quietest 2.4 GHz channel (1, 6 or 11), turn off Wi-Fi power saving on every node (a common cause of latency spikes, from experience rather than a source), and keep the Mac and hub off 2.4 GHz.
- **Sync traffic:** the sync server broadcasts timing to clients over the network ([libcamera](https://patchwork.libcamera.org/patch/22503/raw/)). Some routers throttle or drop broadcast/multicast on Wi-Fi, so Phase 1 tests this on your router first. *Phase 1:* the home router passes it. All four nodes reached "Sync achieved" on 2.4 GHz channel 7 of the home network, which isn't yet a dedicated channel.
- **Wired upgrade:** a USB-Ethernet adapter per Zero, or a PoE splitter that supplies power and Ethernet over the one port, makes each camera a single cable. It fixes live reliability, offload time and broadcast filtering at once. I haven't picked products yet.
- **Power:** a proper 5 V 2.5 A supply per node. Under-voltage throttles the chip and drops frames; the agent reports it.
- **Heat:** a heatsink on each Zero. The agent reports temperature and throttling, and the hub warns before arming. *Phase 1:* the nodes peaked at 40–42 °C over 62 min of recording with no throttling. The hub's fan runs from the Pi 5 fan header under stock kernel control (on at 50 °C).
- **SD cards:** high-endurance cards. The write rate (2 MB/s at 16 Mbps) is easy; hours of sustained writing is what wears consumer cards.

## Options considered

Option A is the recommendation; the others are either fallbacks if Phase 1 disappoints or deliberately out of scope.

| Option | Verdict | Why |
| --- | --- | --- |
| A. `rpicam-vid` software sync for takes, MediaMTX for live | **Recommended** | Frame-locked takes using Raspberry Pi's own maintained tooling; MediaMTX is the simplest robust live server. Frame-locked capture is also what any later multi-view or virtual-camera work would need. |
| B. MediaMTX for both modes, toggling recording via its API | Rejected | One pipeline is simpler, but starts snap to the next keyframe and nothing keeps the cameras frame-locked. |
| C. NTP plus a scheduled start, no camera sync | Fallback | Aligns the first frame, then the angles drift apart over long takes. |
| D. Custom Picamera2 agent driving the camera directly | Plan B | More telemetry and control, but more code to own. Only if the CLI route falls short in Phase 1. |
| E. Pi hub switches and streams to YouTube itself | Out of scope for v1 | Decoding 4–5 1080p feeds, compositing and re-encoding is beyond the hub. The Pi 5 has no hardware H.264 encoder at all. OBS on the Mac is the right switcher. |
| F. Wi-Fi vs wired cameras | Start on Wi-Fi, design for wired | Wi-Fi keeps placement free; wired is a drop-in upgrade with no software change. |

## Risks and mitigations

The top risk is that software sync behaves worse on Zero 2 W over Wi-Fi than the docs suggest; Phase 1 exists to find that out before the rest is built. Likelihoods are my estimates.

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| Software sync misbehaves on Zero 2 W + Camera Module 3 over Wi-Fi | Medium | High | Measured first in Phase 1; fallback C |
| Router drops or throttles sync broadcasts | Low–medium | High | Tested in Phase 1; dedicated access point or wired |
| 2.4 GHz congestion drops live frames | Medium | Medium | Bitrate budget, clean channel, wired upgrade |
| OS updates change camera behaviour | Medium | Medium | Pin package versions; provision every node from one Ansible playbook |
| Thermal throttling in long takes | Low–medium | Medium | Heatsinks, telemetry, warning before arming |
| SD card fills mid-take | Low | High | Hub refuses to arm below a floor and shows time left |
| Crash or power cut mid-take | Low | Medium | Raw H.264 survives truncated; hub rebuilds the manifest |
| Files deleted before they're safe | Low | High | Delete only after the hub has verified its copy |

## Implementation plan

Four phases, each ending in something usable. Phase 1 is a go/no-go on the two riskiest assumptions before most of the code is written.

1. **Phase 1, spike (about half a day, two cameras).** Run `rpicam-vid` software sync on two nodes over Wi-Fi, film a phone stopwatch, and measure the offset at the start and after 60 minutes. Then stream from all nodes at once and measure throughput with `iperf3`.
   - Gate: under one frame of offset after an hour, and 30 Mbps of combined live traffic without drops (two nodes at 15 Mbps each, standing in for six at 5 Mbps).
2. **Phase 2, control.** Node agent (Python, systemd), hub API and web UI (REC/STOP, LIVE, status), chrony, mDNS, Ansible provisioning for nodes and hub. You get synced takes on SD and live feeds in OBS.
3. **Phase 3, ingest.** Pull, rewrap with timecode, verify, lazy delete, exFAT swap-drive handling (mount on demand, Eject), optional network share and Mac auto-pull. You get the full loop from the REC button to files ready to edit.
4. **Phase 4, polish (optional).** Per-camera image profiles with snapshot preview, tally LEDs, a physical REC button on the hub's GPIO, OBS scene-collection export, LTC audio timecode.
   - **Snapshot overview page for setup** (added 2026-11-15). One page on the hub shows a still from every camera side by side, with a "Take snapshots" button (optionally refreshing every few seconds). It's for placing, framing, focusing and matching cameras, and for a quick look at the whole rig. Each tile has the camera's status: temperature, clock, free space, settings.
     - Not a live stream, and not kept: snapshots live in memory only and are never saved to the cameras' SD cards or the drive.
     - When idle, each camera grabs one frame with its normal locked settings (a short `rpicam-still`, light on the Zero). In live mode, the frame is taken from the existing stream instead. Never during a take.
     - Fits with the planned per-camera image controls and focus help (snapshot preview, sharpness readout).
   - **Hub scratch mic for audio sync** (added 2026-11-15; the user has bought a USB mic for the hub). The hub records a low-gain scratch WAV during every take, timestamped against its own clock, which is the clock the cameras' frames are stamped against. The user uploads the DAW bounce, and optionally one stem the room can hear, such as the vocal mic or a drum overhead. The hub matches scratch against bounce with GCC-PHAT at several points in the take, which gives both the offset and the audio clock drift, and stamps the bounce with the take's timecode.
     - The result doesn't have to be perfect. If the match points disagree or confidence is low, the take is marked "needs sync" and falls back to a clap marked by hand, or to LTC. It never guesses silently.
     - Known weak case: mostly-DI sessions in a quiet room. The stem upload or LTC covers it.
     - Needs the bounce upload (see the next item).
     - **Multi-room sessions (user requirement, 2026-11-15).** Sessions sometimes use several rooms in the house. Cameras and players in other rooms can't be heard by the hub mic.
       - Pi cameras in any room still share the hub-clock timecode (if on the rig's Wi-Fi), so syncing the bounce once to the timeline syncs all of them. The hub mic only needs to hear *something* of the performance.
       - When the hub mic hears nothing usable, the anchor is a clap that's in the bounce (through the instrument mics) and visible on a camera in that room. Sync must then work from claps alone: find the clap in the bounce and the motion peak on that camera (as in `hub/tools/clap_check.py`).
       - Video not on the shared clock (phones, handhelds) always needs its own anchor: a clap it can see that's in the bounce, or its own audio matched against the bounce.
       - Per take, the hub picks the anchor (hub mic match, or claps) and says which one it used and how confident it is. "Needs sync" if neither works.
       - Cameras in other rooms need good Wi-Fi there (−60 dBm or better); check before a session.
     - **Acceptance test (user, 2026-11-15):** a real session with claps at regular intervals (say every 5 minutes), visible to all cameras and audible in the bounce. The claps are ground truth: the automatic scratch-mic match must agree with them within one frame at every clap, start to end. This also measures the audio interface's clock drift over a long take. Use the clap finder from the Phase 3 sync check (audio onsets, per-camera motion peaks), kept in the repo for this.
   - **Add media to a take from the web page** (added 2026-11-15): phone or handheld video, the DAW bounce, and stems. An "Add media" button on the hub page works from a phone or the DAW computer. Files land in the take's folder on `STUDIO` (`cameras/`, `handheld/`, `audio/`), so each take keeps all its media in one place.
     - The take is picked automatically from the file's recording time; the user confirms.
     - Uploads go in resumable chunks (phone browsers drop uploads when the screen locks) and stream straight to disk (the hub has 1 GB of RAM). They pause during takes and live sessions, like ingest. The hub never deletes from the drive.
     - Sync: handheld video with sound is matched by audio against the bounce (or the hub scratch mic), the same way as above, and gets the take's timecode. Phone clocks (about ±1 s) are used only to pick the take. Optionally conform variable-frame-rate phone video to 30 fps.
     - Same fallback: "needs sync" if the match isn't confident. Video without sound can't be synced automatically.
   - **Editing: our own AI-assisted editor** (user direction 2026-11-15).
     - An agent listens to the music (the synced bounce, per song) and analyses the video from every camera, and proposes the cut: which camera, from when to when, cut on musical boundaries.
     - A basic UI: a timeline per take/song with a lane per camera; pick the camera for any point in time, move cut points, accept or change the agent's proposal, render.
     - Inputs it relies on, all produced by the rig already or planned: every camera on one clock and timecode (`first_frame_wallclock_ns`, `.mov` timecode), per-camera `gaps` and rough sections (never cut to a rough camera), the bounce synced to the take (Track A), song segments within a take, motion and framing per camera.
     - The "automatic edit on the hub" idea below is the first version of the agent; its switching rules are a starting point. It writes no EDL/XML for other editing software.
     - Plan: `docs/editor-plan.md` (accepted 2026-11-16). The UI runs in the browser, the hub stores, cuts and renders. Ingest moves to `-c copy` at real frame timing once spike E1 passes (passed 2026-11-16; ingest 0.4.0 does it); the render does the 30 fps conform for the final cut only.
   - **Future idea: automatic edit on the hub** (added 2026-11-15). This would give a finished video with no editing step, aimed mainly at sessions using only the Pi cameras. It depends on the scratch mic and the bounce upload above.
     - **Many partial bounces per take.** A long session is often bounced one song at a time, because each song is mastered separately. The bounces arrive at different times (song 1, then 2, then 3), and each covers only part of the take. The hub searches each bounce against the whole take's scratch audio (coarse to fine, so a 4-min bounce against a 2-hour take stays quick), finds where it sits, and shows it as a segment on the take's timeline: "Song 2: 00:41:12–00:45:30, synced (confidence high)".
     - **Edits and mastering.** EQ, compression, limiting and fades are fine. If a bounce was edited in time (sections moved or cut, tempo changed), the match points won't agree. The hub then flags it "doesn't match the take as one piece" rather than producing a bad edit.
     - **Auto edit button** on each synced segment: the hub cuts the conformed camera files (already on one exact 30 fps grid, so cuts are frame-accurate) into one video with the bounce as its soundtrack.
     - Switching rules, as a start: hold shots for 4–10 s, cut on the beat (tempo and onsets detected from the bounce), come back to a wide shot regularly, favour cameras with movement, and never cut to a camera with dropped frames at that moment (from the manifest `gaps`).
     - Output goes to the take folder on `STUDIO` under `edits/`: an `.mp4` per song, plus the cut as data (cameras and cut points) that the editor's timeline opens for changes.
     - Cost: cutting means one encode at a time (libx264, about real time on the Pi 5, Phase 3 S2), so a 4-min song takes about 4–5 min. It runs when the rig is idle, like ingest.
     - Later options: split screen or picture-in-picture, a per-song choice of style, and a preview in the hub UI.
   - **Future idea: phones as offline camera units** (added 2026-11-15). The user has several old Pixel 3s, already confirmed to handle long recording sessions with good quality. Live mode stays on DroidCam; this is for offline takes only.
     - A web page on the hub (`https://studio-hub.local/phone`), not a native app. The phone takes a name (`phone1`, …) and joins the rig like a Pi camera: status on the hub page (battery, temperature, free space), and REC/STOP from the hub along with the Pi cameras.
     - Preview on the phone's own screen, plus a still sent to the hub page every few seconds, for placing the camera.
     - Records 1080p30 in the browser (hardware encoder) in small pieces saved as it goes, so a Wi-Fi drop or crash loses seconds, not the take.
     - Uploads to the hub over 5 GHz, so the phones never touch the Pi cameras' 2.4 GHz channel. Files land in the take's `handheld/` folder and go through the same conform and timecode as the Pi cameras.
     - Sync: per-frame capture timestamps from the browser, offset against the hub's clock (measured by the page), and cross-checked by matching the phone's audio against the hub scratch mic. "Needs sync" if they disagree.
     - Camera access in the browser needs a secure context: a one-off Chrome flag per phone (`unsafely-treat-insecure-origin-as-secure`), the user's choice (2026-11-15); no HTTPS or local CA.
     - The page keeps the screen awake and must stay in the foreground during a take. Keep the phones plugged in. The hub shows temperature and warns, since old Pixel 3 batteries can swell.
     - First step: test the page on one Pixel 3. Can Chrome lock exposure, white balance and focus? Are its frame timestamps accurate? Does browser-recorded video match the quality the user already confirmed? The browser doesn't get the Google Camera app's processing.
     - If the browser falls short: a native Android app, possibly starting from Google Research's open-source RecSync (synchronised multi-phone capture).
     - **Dropped frames are tolerated: marked, not fixed** (user decision 2026-11-15, after the phone spike). Phone footage is one angle in a cut, so a rough stretch just means using another angle there. Every frame keeps its own hub-clock time, so drops never shift sync. The hub marks rough sections per camera (e.g. any 2 s window missing more than a few frames) in `take.json` and on the hub page, and the editor and its agent avoid them. Bad chunks are not blanked or discarded. Applies to the Pi cameras' `gaps` too.
     - Step one: record a scratch WAV during one real take and test the matching offline before building anything.

One repository:

- `node/`: camnode agent, MediaMTX config template, systemd units
- `hub/`: control API and UI, ingest worker, chrony and Samba config
- `mac/`: optional auto-pull script and launchd job
- `deploy/`: Ansible inventory and playbooks
- `docs/`: runbook for setup, daily use and troubleshooting

## Open questions

Settled on 7 November: the cameras stay wireless; a clap is the default audio sync, with LTC as the upgrade; the hub (Pi 5, originally planned as a Pi 4) goes on Ethernet with an external USB drive, transfers can run overnight, the drive (exFAT) moves to the Mac for editing, everything runs at 1080p30 with the shutter at 1/50 s, and the rig starts with two nodes, scaling to four and possibly six, on their own 2.4 GHz channel; per-camera ISOs are recorded on the OBS machine, not the Pis. What's left only changes settings.

- [ ] How long is a typical session?
- [ ] Is the Mac wired or on 5 GHz for live sessions?
- [ ] Before Phase 3: portable SSD or hard drive for the swap drive, and what size?
- [ ] Which router or access point will carry the cameras' channel? Phase 1: the home router passes the sync broadcasts, but the cameras currently share its channel 7 with the household.
