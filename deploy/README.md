# deploy/: provisioning with Ansible

All installation and configuration of the hub and cameras goes through these playbooks, run from the Mac. Nothing is set up by hand on the Pis, so any Pi can be rebuilt from a fresh SD card.

## One-time setup on the Mac

```sh
cd deploy
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The Pis must be reachable as `studio-hub.local` and `cam1.local`, `cam2.local` and so on, with the SSH user `studio` and your SSH key.

## Day-to-day commands

```sh
cd deploy
.venv/bin/ansible-playbook site.yml                 # everything
.venv/bin/ansible-playbook site.yml --limit hub     # hub only
.venv/bin/ansible-playbook site.yml --limit cam2    # one camera
```

- **A second run should report `changed=0`.** The playbooks are idempotent.
- **There's a safety guard:** the first step asks the hub whether a take or live session is running, and refuses to deploy if one is. Override with `-e force_deploy=true` only if you're sure. The guard runs whatever `--limit` you use.
- **Offline cameras are fine:** the hub's host-key scan skips cameras that don't answer (with a warning), and a single camera can be deployed without the hub in the run.
- **Brand-new Pi:** run `bootstrap.yml` once with the `studio` password (`-K`). It sets up passwordless sudo, so later runs are unattended.

Known snag: with some cameras switched off, a hub-only run fails at the SSH-key step (`ingest_key_hub`) before services are restarted. Either run with all cameras on, or restart the service by hand.

## What's here

| Path | What it is |
|---|---|
| `site.yml` | The full playbook: guard → all hosts → hub → cameras → checks |
| `bootstrap.yml` | One-time passwordless sudo for a new Pi |
| `inventory.yml` | The hosts. `cam5` and `cam6` are ready to uncomment. |
| `group_vars/all.yml` | Settings for every host, including **pinned package versions** (they must match `docs/versions.md`) |
| `group_vars/hub.yml`, `group_vars/nodes.yml` | Hub and camera settings |
| `host_vars/camN.yml` | Each camera's image settings: shutter, gain, white balance gains, focus. Placeholders until grey-card calibration. |
| `roles/` | The building blocks, listed below |

### Roles

| Role | Runs on | What it sets up |
|---|---|---|
| `common` | all | Installs pinned package versions and holds them; timezone; a persistent log so logs survive a crash |
| `chrony_server` | hub | The hub as the NTP time server |
| `chrony_client` | cameras | The cameras take time only from the hub |
| `hubd` | hub | The control service and web UI |
| `hub_ingest` | hub | The overnight ingest worker |
| `ingest_key_hub` | hub | The hub's SSH key for pulling takes, and the cameras' host keys |
| `ingest_key_node` | cameras | Lets that key in, but only for *read-only* rsync of the takes folder (no shell, no writes) |
| `node_net` | cameras | Wi-Fi power saving off |
| `mediamtx` | cameras | MediaMTX for live mode (a pinned release, sha256-checked). It doesn't start at boot; camnode controls it. |
| `camnode` | cameras | The camera agent and its per-camera config |

## Adding a camera

1. Flash Raspberry Pi OS Lite, hostname `camN`, user `studio`, Wi-Fi on the camera network.
2. Add it to `inventory.yml`, and create `host_vars/camN.yml` by copying another camera's. The hub's camera list is built from the inventory, so there's nothing else to edit.
3. Run `bootstrap.yml -K --limit camN`, then `site.yml`. Run the whole playbook, not just the new camera, because the hub needs the new camera's key and an updated camera list.
