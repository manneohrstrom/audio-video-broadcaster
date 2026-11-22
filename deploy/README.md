# deploy/: installing and updating the rig

Ansible playbooks, run from your computer, install and configure the hub and every camera. For what to buy and how to put it together, see the [main README](../README.md).

> [!NOTE]
> These playbooks grew with one working rig. They have not been run end to end on blank cards, and have only been run from a Mac.

## First install

### 1. Flash the SD cards

Use [Raspberry Pi Imager](https://www.raspberrypi.com/software/) with **Raspberry Pi OS Lite (64-bit)**, Debian 13 "trixie". In the customisation settings:

| Setting | Hub | Cameras |
|---|---|---|
| Hostname | `studio-hub` | `cam1`, `cam2`, … (must be `cam` and a number) |
| User | `studio` | `studio` |
| Password | Use the same one on every Pi for the install | The same |
| SSH | On, with your public key | On, with your public key |
| Wi-Fi | None, it uses Ethernet | Your 2.4 GHz network, with the right country |

Boot them all. **Don't run `apt upgrade`:** the playbook installs exact package versions and won't downgrade newer ones.

### 2. Format the drive

Format the USB drive as **exFAT** with the name **`STUDIO`** (on a Mac: Disk Utility → Erase). This erases it. The hub ignores drives with any other name.

### 3. Get the code and Ansible

Clone this repository (the **Code** button on GitHub has the address), then from its top folder:

```sh
cd deploy
python3 -m venv .venv          # Python 3.12 or newer; tested with 3.14
.venv/bin/pip install -r requirements.txt
```

All the commands below are run from `deploy/`.

### 4. Change the settings for your studio

The committed values are the original studio's.

| File | Setting | Change it to |
|---|---|---|
| `inventory.yml` | `nodes` | The cameras you have. Remove or comment out the others, or uncomment `cam5` and `cam6`. |
| `host_vars/camN.yml` | | One file per camera. For a new one, copy another camera's. |
| `../hub/chrony/studio-hub.conf` | `allow` | Your network's range, for example `192.168.1.0/24`. **Without this the cameras' clocks don't sync and REC is refused.** |
| `group_vars/all.yml` | `timezone` | Yours. Timecode and take names use it. |
| `group_vars/nodes.yml` | `cam_shutter_us` | `30000` suits 50 Hz mains. For 60 Hz use `25000`, which avoids flicker (not tested on this rig). |
| `group_vars/hub.yml` | `hubd_lamp` | `{type: null}` if you have no bulb, replacing the whole block. Otherwise your bulb's type and address. |
| `group_vars/hub.yml` | `hubd_scratch` | The mixer names and level suit a C-Media "USB PnP Sound Device". Adjust for your mic. |
| `group_vars/hub.yml` | `hubd_sync_server` | The camera that leads the sync, normally `cam1`. |

### 5. Install

Connect to each Pi once, to check that it answers and to accept its host key:

```sh
ssh studio@studio-hub.local exit
ssh studio@cam1.local exit     # and so on for each camera
```

Then, with every Pi switched on:

```sh
.venv/bin/ansible-playbook bootstrap.yml -K                      # once; asks for the password
.venv/bin/ansible all -b -m ansible.builtin.apt -a update_cache=true    # refresh the package lists
.venv/bin/ansible-playbook site.yml
```

`bootstrap.yml` gives `studio` passwordless sudo, which later runs and the hub's drive handling both need.

If a pinned package can't be found or is older than what the image came with, change the pin in `group_vars/all.yml` or `group_vars/hub.yml`, and in [`docs/versions.md`](../docs/versions.md). Test sync again afterwards, because camera behaviour depends on these versions.

### 6. Check it

1. Open http://studio-hub.local/. Every camera should be on the *monitor* view with its clock synced (allow a minute after boot).
2. Check each picture on the *cameras* view.
3. Record a short take, press **ingest now** in the archive, and play it back.

Then do the one-off steps for phones, the editor, OBS and YouTube listed in the [main README](../README.md#installation).

## Updating

```sh
.venv/bin/ansible-playbook site.yml                 # everything
.venv/bin/ansible-playbook site.yml --limit hub     # hub only
.venv/bin/ansible-playbook site.yml --limit cam2    # one camera
```

- **A second run should report `changed=0`.**
- **There's a safety guard:** the first step asks the hub whether a take or live session is running and refuses to deploy if so. It runs whatever `--limit` you use, so the hub must be on. `-e force_deploy=true` overrides it; use that only if you're sure.
- **Cameras can be off for a hub deploy:** with `--limit hub`, cameras that don't answer are skipped with a warning.
- **Deleted files stay on the Pis:** the copy tasks don't remove files that have left the repo.

## What's here

| Path | What it is |
|---|---|
| `site.yml` | The full playbook: guard → all hosts → hub → cameras → checks |
| `bootstrap.yml` | One-time passwordless sudo for a new Pi |
| `inventory.yml` | The hosts |
| `group_vars/all.yml` | Settings for every host, including pinned package versions |
| `group_vars/hub.yml` | Hub packages and settings: ingest, phones' picture, mic, recording light, where the YouTube login is |
| `group_vars/nodes.yml` | Camera settings shared by all: shutter (takes and LIVE alike), bitrates, MediaMTX version |
| `host_vars/camN.yml` | Each camera's starting gain, white balance and focus. The hub's calibrated settings replace them at every take and LIVE. |
| `roles/` | The building blocks, listed below |

### Roles

| Role | Runs on | What it sets up |
|---|---|---|
| `common` | all | Pinned packages, held; timezone; a persistent log |
| `chrony_server` | hub | The hub as the time server |
| `chrony_client` | cameras | The hub as the cameras' only time source |
| `hubd` | hub | The control service and web pages (hub page, phone pages, editor) |
| `hub_ingest` | hub | `hub-ingest` (ingest, sync, analysis, render) and `hub-upload` (YouTube), plus the YouTube login if your computer has one |
| `ingest_key_hub` | hub | The hub's SSH key for pulling takes, and the cameras' host keys |
| `ingest_key_node` | cameras | Lets that key do read-only rsync of the takes folder and nothing else |
| `node_net` | cameras | Wi-Fi power saving off |
| `mediamtx` | cameras | MediaMTX for LIVE (pinned, sha256-checked). camnode starts and stops it. |
| `camnode` | cameras | The camera agent and its config |

A new Python module in `hub/hubd/` or `hub/ingest/` must also be added to its role's copy list, or the service won't start.

## Adding a camera

1. Flash and boot it as in step 1, and SSH to it once.
2. Add it to `inventory.yml` and create `host_vars/camN.yml` by copying another camera's. The hub takes its camera list from the inventory.
3. Run, from `deploy/`:
   ```sh
   .venv/bin/ansible-playbook bootstrap.yml -K --limit camN
   .venv/bin/ansible camN -b -m ansible.builtin.apt -a update_cache=true
   .venv/bin/ansible-playbook site.yml
   ```
   The last one covers the whole rig, because the hub needs the new camera's key and camera list.
