# Finished videos to YouTube

*User decision, 2026-11-18: "let's go with YT", private uploads, to the band's existing channel; usually no more than 5 videos a day.*

## The story

A long take of a jam; upload 5 bounces; make 5 edits; press **Make video** 5 times. The hub renders them one after another (hub-ingest's render queue, as before). Each finished video is written to the STUDIO drive and checked there, and then, as it becomes ready, uploaded to the channel as a **private** video. You check it in YouTube Studio and publish it yourself.

## How it works

- **Editor:** next to Make video, a **YouTube** switch (only shown when the hub is signed in; this browser remembers it; on by default). After the render, the same place shows "YouTube: uploading 43 %", "paused while recording", "trying again…", then an **On YouTube** button that opens the video in YouTube Studio.
- **Video details:** the name is the editor's title line (Title step, when it's on) or the song's name; the description is the lines below the title and "Recorded <date of the take>." Category Music, not made for kids, **private**.
- **hub-ingest:** after it has written the video to the drive and checked it, it moves its own copy on the hub's SD card into `/var/lib/hubd/youtube/uploads/` with a record (`<take>_<song>_video<N>.json`). The drive's copy is the master. Nothing is ever deleted from the drive.
- **hub-upload** (a new service, `hub/ingest/youtube_upload.py`): one upload at a time, oldest first, so a long upload never holds up the next render. Google's resumable upload in 8 MB pieces: a dropped connection, a restart or a take in between carries on from the last byte YouTube has. It pauses while a take or live session runs. When YouTube has the whole video, it deletes its SD copy. Problems:
  - network or YouTube trouble: tries again after 30 s, 1, 2, 5, then 10 min;
  - the daily limit: tries again in an hour;
  - the login stopped working: says "sign in again with mac/youtube_login.py";
  - YouTube refuses the video: shown in the editor and left alone (the file stays).
- **Python standard library only**, no new packages. Memory capped at 120 MB; runs at a lower priority than renders.
- **The login:** an OAuth refresh token for the `youtube.upload` scope only: the hub can upload videos, nothing else (it can't delete, edit or read your other videos). It's made once on the Mac, kept in `~/.config/studio-multicam/youtube.json` (never in git) and copied by the deploy to `/var/lib/hubd/youtube/credentials.json` (readable only by the hub's user).

## Limits

- **Private only:** YouTube locks videos uploaded through an API project it hasn't audited to private. That's what we want.
- **Quota:** the default allowance of 10,000 units a day is about 6 uploads (1,600 units each). Usually 5 a day here. More than that waits for the next day by itself.
- **Upload speed:** the internet upload speed decides it: at 20 Mbit/s a 1.5 GB video takes about 10 minutes.
- **SD card:** the videos waiting to upload are on the hub's SD card (about 1–2 GB each) until YouTube has them.

## Setting it up (once, about 15 minutes)

1. **A Google Cloud project:** go to console.cloud.google.com, signed in with the Google account that owns the channel (or manages it). Create a project, e.g. "Studio Multicam".
2. **Turn on the API:** APIs & Services → Library → *YouTube Data API v3* → Enable.
3. **The consent screen** (Google Auth Platform / OAuth consent screen): type *External*; app name e.g. "Studio Multicam hub"; your email as the support and developer contact. Under **Audience**, press **Publish app** so it's *In production*: in *Testing*, Google signs the hub out every 7 days. You don't need Google's verification for your own use.
4. **A client:** Credentials → Create credentials → OAuth client ID → *Desktop app*. Download the JSON file.
5. **Sign in:** on the Mac, `python3 mac/youtube_login.py ~/Downloads/client_secret_….json`. In the browser choose the account, then **the band's channel**, and allow it. Google shows "Google hasn't verified this app": choose *Advanced* → *Go to Studio Multicam hub*. (It's your own app.)
6. **Copy it to the hub:** `cd deploy && .venv/bin/ansible-playbook site.yml --limit hub`. The editor then shows the YouTube switch.

To sign the hub out: remove the app at myaccount.google.com → Security → Third-party access, and delete `~/.config/studio-multicam/youtube.json` and the hub's `/var/lib/hubd/youtube/credentials.json`.

## Checked

- `hub/ingest/tests/test_youtube_upload.py` against a fake YouTube following Google's resumable protocol: in pieces and byte for byte, private and Music; after a dropped connection (same session); after a restart (the record on disk is enough); pausing for a take; an expired session; the daily limit; a refused video; an expired token renewed once; a revoked login; not signed in. Also that Python's HTTP client returns YouTube's 308 instead of following it.
- `test_ingest.py`: a finished video with YouTube goes to the upload folder with its details, without it nothing is queued.
- `test_hubd.py`: only when signed in; the upload's progress comes back with the render's, without YouTube's session address.
- Not yet: a real upload to the channel (needs the setup above).
