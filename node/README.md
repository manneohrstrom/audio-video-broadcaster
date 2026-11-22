# node/: the camera nodes

Everything that runs on each camera (`cam1`, `cam2` and so on: Raspberry Pi Zero 2 W + Camera Module 3 Wide). For parts and assembly, see the [main README](../README.md#what-you-need).

A node does one thing at a time: **live** (streaming to OBS), **recording a take**, or taking a **still** for the hub's cameras view and calibration. It never does two at once. It does no heavy processing: the Zero 2 W has a slow CPU and only 2.4 GHz Wi-Fi, so it captures and stores, and the hub does the rest.

## What's here

| Path | What it is |
|---|---|
| `camnode/camnode.py` | **The camera agent.** A small HTTP service the hub talks to. It owns the camera and switches between live and take modes. |
| `camnode/tests/` | Unit tests, using a fake `rpicam-vid` (`fake_rpicam_vid.py`) that can drop frames, crash or hang, so the tests run on the Mac. |
| `mediamtx/mediamtx.yml.j2` | MediaMTX config template for live mode: SRT on port 8890, RTSP over TCP on 8554, WebRTC preview on 8889. Ansible fills in the shared shutter and each camera's starting image settings. |
| `systemd/` | `camnode.service`, which runs at boot, and `mediamtx.service`, which camnode starts and stops; it doesn't run at boot. |
| `chrony/studio-node.conf` | Clock sync: the hub is the camera's only time source. |
| `networkmanager/wifi-powersave-off.conf` | Turns Wi-Fi power saving off. With it on, camera sync messages arrive late and frames get dropped. |
| `udev/99-camnode-led.rules` | Lets camnode (group `video`) drive the board's green LED as a recording light. |
| `spike/` | Phase 1 test scripts, kept for reference: `synctest.sh` (long synced recordings), `diag.sh` (frame-drop diagnosis), `cutframes.py` (cut frames out of raw H.264 to check offsets). |

## How a take works on the node

1. The hub sends `POST /take/arm` with a take id and a role: **client** for every camera except one, which is the **server**. Clients are started first and wait; the server starts last.
2. camnode runs `rpicam-vid` with `--sync client` or `--sync server`. The cameras agree on a start time over the network, so their frames line up.
3. Settings: 1080p30 H.264 at about 16 Mbps, the rig's one shutter (30 ms, `cam_shutter_us` in `deploy/group_vars/nodes.yml`, the same in LIVE), and `--denoise cdn_off` (the default denoise drops frames on the Zero). Gain, white balance gains and focus come with the arm request: the hub's settings for this camera, set by calibration or, for a camera never calibrated, the defaults. They stay fixed for the whole take.
4. The take is written to `/var/lib/camnode/takes/<take_id>/`:
   - `video.h264`: the raw video. A raw stream survives a power cut better than an MP4.
   - `video.pts`: the exact time of every frame.
   - `metadata.json`: per-frame details from the camera, including its wall-clock time.
   - `manifest.json`: written at stop. It has the first-frame wall-clock time, frame count, dropped-frame gaps, sync result, exit code and a sha256 of the video.
5. `POST /take/stop` ends it and returns the manifest.

The status the hub polls includes a live dropped-frame count, so problems show up on the hub page during the take, not afterwards.

**Green LED on the board:** a short blip once a second = armed, waiting for sync; steady fast flashing (2 Hz) = frames are being recorded; normal power/SD-activity light = idle or live. A camera whose LED doesn't match the others during a take has a problem. Set `"led_path": ""` in `/etc/camnode/config.json` to turn it off (e.g. if it shows in a shot).

## API (port 8080)

For the hub and tools only: a request with an `Origin` header (one from a web page) is refused.

| Call | What it does |
|---|---|
| `GET /status` | Mode (idle / live / recording / snapshot, or "stuck" if the camera driver froze), temperature, throttling, clock sync, Wi-Fi signal and link rate, free space, and current-take progress with dropped frames so far |
| `GET /takes` | Takes on this camera, with their manifests and whether the hub has verified them |
| `POST /take/arm`, `POST /take/stop` | Start and stop a take (see above) |
| `POST /live/start`, `POST /live/stop` | Start and stop MediaMTX streaming |
| `POST /live/settings` | Change gain, white balance gains or focus on the running live stream, without a gap (MediaMTX API) |
| `POST /snapshot` | A still with given settings, or auto-metered (`{"auto": true}`) for calibration; returns the JPEG and the camera's metadata |
| `POST /takes/<id>/verified` | The hub reports it has a verified copy on the drive: the take is deleted |
| `POST /takes/<id>/delete` | The archive's "delete take": delete this take, verified or not (never the one being recorded) |

## Freeing space

A camera deletes a take as soon as the hub has marked it verified on the `STUDIO` drive (while recording, it waits until it's idle again). Otherwise it deletes a take only when the hub asks, after someone pressed **delete take** in the archive: that works on unverified takes too. It never deletes the take being recorded. Deletions are logged in `/var/lib/camnode/deleted.jsonl`.

## Image settings

The hub keeps each camera's settings (`/var/lib/hubd/camera_settings.json`), set by calibration with the card and by "save focus" on the cameras view, and sends them with every arm, still and live start. The gain, white balance and focus in `deploy/host_vars/camN.yml` are only the starting point before a camera's first calibration (they also go into the MediaMTX config, which the hub overrides when LIVE starts). The shutter is not per camera and the hub doesn't set it: it comes from `deploy/group_vars/nodes.yml`.

## Tests

```sh
cd node/camnode && python3 -m unittest discover -s tests
```
