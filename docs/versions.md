# Pinned versions

These are the versions verified working in Phase 1 (2026-11-14). Provisioning (Ansible, Phase 2) should install exactly these and hold them (`apt-mark hold`), so an OS update can't change camera behaviour mid-project.

## Camera nodes (cam1–cam4: identical package sets)

| Component | Version |
|---|---|
| OS | Raspberry Pi OS, Debian 13 (trixie), arm64 |
| Kernel | 6.18.60+rpt-rpi-v8 |
| raspi-firmware | 1:1.20260915-1 |
| rpicam-apps-core / rpicam-apps-lite | 1.15.0-1 |
| libcamera0.8 / libcamera-ipa | 0.8.0+rpt20261104-1 |
| MediaMTX | v1.21.3, linux_arm64 release tarball (sha256 `27f03823…4fe2`), in `/opt/mediamtx/` on all nodes via Ansible; bundles libcamera v0.7.2-mediamtx. `moq: false` is required (see known issues) |
| chrony | 4.6.1-3+deb13u2 |
| iperf3 | 3.18-2+deb13u2 |
| iw | 6.9-1+b1 |
| network-manager | 1.52.1-1+rpt4 |
| python3 | 3.13.5-1 |
| rsync | 3.5.0+ds1-0+deb13u1 (includes `rrsync`, used to restrict the hub's ingest key) |

## Hub (studio-hub, Pi 5)

| Component | Version |
|---|---|
| OS | Raspberry Pi OS, Debian 13 (trixie), arm64 |
| Kernel | 6.18.60+rpt-rpi-2712 |
| raspi-firmware | 1:1.20260915-1 |
| ffmpeg | 8:7.1.5-0+deb13u1+rpt2 |
| mkvtoolnix | 92.0-1+deb13u1 (`mkvmerge --timestamps` for ingest) |
| exfatprogs | 1.2.9-1+deb13u1 (`fsck.exfat` for the STUDIO drive) |
| rsync | 3.5.0+ds1-0+deb13u1 |
| alsa-utils | 1.2.14-1+rpt1 (`arecord` for the hub mic scratch recording) |
| chrony | 4.6.1-3+deb13u2 |
| iperf3 | 3.18-2+deb13u2 |
| python3 | 3.13.5-1 |
| python3-opencv | 4.10.0+dfsg-5 (ArUco detection of the calibration card, Track B) |
| python3-numpy | 1:2.2.4+ds-1 (the editor's render, the shared grade maths, `hub/tools/add_overlay.py`; pinned and held 2026-11-17) |
| python3-pil | 11.1.0-5+deb13u4 (the title sequence's cards, `hub/ingest/title_render.py`; pinned 2026-11-21) |
| cloud-guest-utils | 0.33-1 (`growpart`, used once to grow the root partition after the move to the 128 GB card, 2026-11-16) |
| SD card | 128 GB (116.5 GB raw), cloned from the original 32 GB card 2026-11-16; root partition 115 GB |

## Dev machine (Mac, Ansible controller)

| Component | Version |
|---|---|
| Python | 3.14.8 (Homebrew), in `deploy/.venv` |
| ansible-core | 2.20.11 (`deploy/requirements.txt`) |

Setup: `cd deploy && /opt/homebrew/bin/python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

Checked 2026-11-16 (Phase 4 C2): every pinned package on the hub and on cam1 is installed at the version above and held (`apt-mark showhold`), MediaMTX v1.21.3, kernels 6.18.60.

## Holds

`deploy/roles/common` installs the node and hub versions above exactly and holds them with `apt-mark hold`, including the kernel metapackages `linux-image-rpi-v8` / `linux-image-rpi-2712` and their headers. To upgrade deliberately: change `deploy/group_vars/all.yml` and this file together, then `apt-mark unhold` on the hosts.

## Known issues in these versions

- `rpicam-vid` 1.15.0 sometimes aborts at exit with `double free or corruption (fasttop)` (exit 134). This happened in 3 of about 40 short runs and in none of the long runs. The video survives, but `--save-pts` can be lost, because it's only written at exit.
- MediaMTX v1.21.3 enables MoQ by default, which writes `auto.key`/`auto.crt` to the working directory. Under systemd that's `/` (not writable by `studio`), and shutdown then hangs in `moq.(*Server).Close` until systemd kills it. Fixed with `moq: false` in the template.
- `rpicam-vid --sync client` can report `Sync achieved! Difference -183870us`: it starts recording even though it's that far behind the server. Seen on cam4 only, 6 of 150 takes, up to 969 ms. camnode records the value and hubd warns.
- The default video denoise (`cdn_fast`) can't sustain 1080p30 on the Zero 2 W at normal clocks. Use `--denoise cdn_off`.

## Phones (browser cameras, 2026-11-15)

No software installed; the page runs in Chrome. Tested: Pixel 3 (Chrome 157) and Pixel 9a (Chrome, user test 2026-11-15). Needs WebCodecs (`VideoEncoder`), `MediaStreamTrackProcessor` and OPFS; the page says so if Chrome is too old.


## Hub page libraries (vendored, no build step)

| Library | Version | Where |
|---|---|---|
| qrcode-generator (Kazuhiko Arase, MIT) | 1.4.4, sha256 `18ae399f…1780` (from jsdelivr), unmodified | `hub/hubd/static/qrcode.js`: the phone-page QR code on the hub page |
| mp4box.js (GPAC, BSD-3-Clause) | 0.5.2, sha256 `07ccf31d…1391219` (from jsdelivr), unmodified | `hub/spike/e2/vendor/mp4box.all.min.js`: editor spike E2 (MP4/MOV sample tables for WebCodecs) |

## Editor (browser, served by the hub; no build step)

| Component | Version |
|---|---|
| Preact (`preact.module.js`, `hooks.module.js`) | 10.19.3, from jsDelivr, in `hub/hubd/static/shared/vendor/` (sha256 `bfac992a…134e`, `d952f1d8…7d7e`) |
| Jost (`jost-latin-{400,500,600}-normal.woff2`, SIL OFL) | @fontsource/jost 5.0.17, from jsDelivr, in `hub/hubd/static/shared/vendor/fonts/` (sha256 `03aaf59e…`, `b2cf900a…`, `1762fc2b…`; licence `OFL-jost.txt`) |
| Figtree (`Figtree-Regular.ttf`, `Figtree-SemiBold.ttf`, SIL OFL) | v2.0.3 from github.com/erikdkennedy/figtree (`fonts/ttf/`), in `hub/hubd/static/shared/vendor/fonts/` (sha256 `e6f1cae1…`, `a63306f1…`; licence `OFL-figtree.txt`). Was the video's title until 2026-11-21; still the hub page's theme font. |
| Outfit Bold (`Outfit-Bold.ttf`, SIL OFL) | github.com/Outfitio/Outfit-Fonts `fonts/ttf/` at commit `902773808e` (2023-03-26), in `hub/hubd/static/shared/vendor/fonts/` (sha256 `f620b695…`; licence `OFL-outfit.txt`). The title sequence: the editor's preview and the render's cards use the same file. |
| DSEG7 Classic Bold (`DSEG7Classic-Bold.woff2`, SIL OFL) | dseg 0.46.0, from jsDelivr, same folder (sha256 `ec2e7499…`; licence `OFL-dseg.txt`): the editor's LCD readouts |
| DSEG14 Classic Bold (`DSEG14Classic-Bold.woff2`, SIL OFL) | dseg 0.46.0, from jsDelivr, same folder (sha256 `a73df6e6…`): the hub page's 14-segment rig display |
| Hub page theme fonts (SIL OFL; `hub/styles/themes.css`) | @fontsource 5.3.0, from jsDelivr, in `hub/hubd/static/shared/vendor/fonts/`, licences `OFL-<name>.txt`: Righteous (`righteous-latin-400-normal.woff2`, sha256 `17055c53…`), Nixie One (`nixie-one-latin-400-normal.woff2`, `bad30060…`), Shrikhand (`shrikhand-latin-400-normal.woff2`, `ae958e0d…`), Bungee (`bungee-latin-400-normal.woff2`, `126eec70…`), Orbitron 700 (`orbitron-latin-700-normal.woff2`, `ee6acc5a…`), Oswald 600 (`oswald-latin-600-normal.woff2`, `f6cb541f…`), Archivo Black (`archivo-black-latin-400-normal.woff2`, `25f33e61…`), VT323 (`vt323-latin-400-normal.woff2`, `8ddbebcc…`) |
| htm (`htm.module.js`) | 3.1.1 (sha256 `ab33dd3f…e9e4`) |
| mp4box.js (`mp4box.all.min.js`) | 0.5.2 (same file as spike E2) |
| uPlot (`uPlot.iife.min.js`, `uPlot.min.css`) | 1.6.30, from jsDelivr, in `hub/hubd/static/` (hub page "Last hour" charts; sha256 `7f29cd14…`, `df630c6a…`) |
