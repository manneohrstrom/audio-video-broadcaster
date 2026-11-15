# node/: the camera nodes

Everything that runs on each camera (`cam1`–`cam4`, Raspberry Pi Zero 2 W + Camera Module 3 Wide).

A node does one thing at a time: **live** (streaming to OBS) or **recording a take**. It never does both. It does no heavy processing: the Zero 2 W has a slow CPU and only 2.4 GHz Wi-Fi, so it captures and stores, and the hub does the rest.

## What's here

| Path | What it is |
|---|---|
| `camnode/camnode.py` | **The camera agent.** A small HTTP service the hub talks to. It owns the camera and switches between live and take modes. |
| `camnode/tests/` | Unit tests, using a fake `rpicam-vid` (`fake_rpicam_vid.py`) that can drop frames, crash or hang, so the tests run on the Mac. |
| `mediamtx/mediamtx.yml.j2` | MediaMTX config template for live mode: SRT on port 8890, RTSP over TCP on 8554, WebRTC preview on 8889. Ansible fills in each camera's image settings. |
| `systemd/` | `camnode.service`, which runs at boot, and `mediamtx.service`, which camnode starts and stops; it doesn't run at boot. |
| `chrony/studio-node.conf` | Clock sync: the hub is the camera's only time source. |
| `networkmanager/wifi-powersave-off.conf` | Turns Wi-Fi power saving off. With it on, camera sync messages arrive late and frames get dropped. |
| `udev/99-camnode-led.rules` | Lets camnode (group `video`) drive the board's green LED as a recording light. |
| `spike/` | Phase 1 test scripts, kept for reference: `synctest.sh` (long synced recordings), `diag.sh` (frame-drop diagnosis), `cutframes.py` (cut frames out of raw H.264 to check offsets). |

## How a take works on the node

1. The hub sends `POST /take/arm` with a take id and a role: **client** for every camera except one, which is the **server**. Clients are started first and wait; the server starts last.
2. camnode runs `rpicam-vid` with `--sync client` or `--sync server`. The cameras agree on a start time over the network, so their frames line up.
3. Settings: 1080p30 H.264 at about 16 Mbps, shutter 1/50 s, fixed gain, white balance and focus, and `--denoise cdn_off` (the default denoise drops frames on the Zero).
4. The take is written to `/var/lib/camnode/takes/<take_id>/`:
   - `video.h264`: the raw video. A raw stream survives a power cut better than an MP4.
   - `video.pts`: the exact time of every frame.
   - `metadata.json`: per-frame details from the camera, including its wall-clock time.
   - `manifest.json`: written at stop. It has the first-frame wall-clock time, frame count, dropped-frame gaps, sync result, exit code and a sha256 of the video.
5. `POST /take/stop` ends it and returns the manifest.

The status the hub polls includes a live dropped-frame count, so problems show up on the hub page during the take, not afterwards.

**Green LED on the board:** a short blip once a second = armed, waiting for sync; steady fast flashing (2 Hz) = frames are being recorded; normal power/SD-activity light = idle or live. A camera whose LED doesn't match the others during a take has a problem. Set `"led_path": ""` in `/etc/camnode/config.json` to turn it off (e.g. if it shows in a shot).

## API (port 8080)

| Call | What it does |
|---|---|
| `GET /status` | Mode (idle / live / recording), temperature, throttling, clock sync, Wi-Fi link, free space, and current-take progress |
| `GET /takes` | Takes on this camera, with their manifests and whether the hub has verified them |
| `POST /take/arm`, `POST /take/stop` | Start and stop a take (see above) |
| `POST /live/start`, `POST /live/stop` | Start and stop MediaMTX streaming |
| `POST /takes/<id>/verified` | The hub reports it has a verified copy, so this take may now be deleted |

## Freeing space

A camera deletes a take only after the hub has marked it verified, and only when free space falls below 10 GB. It deletes the oldest verified takes first, and never an unverified take or the one being recorded.

## Image settings

Each camera's shutter, gain, white balance gains and focus position are in `deploy/host_vars/camN.yml`. The same values feed both `rpicam-vid` and MediaMTX. They're placeholders until the grey-card calibration session (Phase 4).

## Tests

```sh
cd node/camnode && python3 -m unittest discover -s tests
```
