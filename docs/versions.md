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
| chrony | 4.6.1-3+deb13u2 |
| iperf3 | 3.18-2+deb13u2 |
| python3 | 3.13.5-1 |
| python3-opencv | 4.10.0+dfsg-5 (ArUco detection of the calibration card, Track B) |
| cloud-guest-utils | 0.33-1 (`growpart`, used once to grow the root partition after the move to the 128 GB card, 2026-11-16) |
| SD card | 128 GB (116.5 GB raw), cloned from the original 32 GB card 2026-11-16; root partition 115 GB |

## Dev machine (Mac, Ansible controller)

| Component | Version |
|---|---|
| Python | 3.14.8 (Homebrew), in `deploy/.venv` |
| ansible-core | 2.20.11 (`deploy/requirements.txt`) |

Setup: `cd deploy && /opt/homebrew/bin/python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

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
