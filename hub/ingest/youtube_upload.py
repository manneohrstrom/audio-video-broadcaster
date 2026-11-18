"""Upload the finished videos to YouTube, as private videos (docs/youtube-plan.md).

The user's story (2026-11-18): render several songs one after the other; each finished video
goes to the STUDIO drive and, as it becomes ready, to the band's YouTube channel, private,
for the user to look at and publish.

How it fits:
  - hub-ingest renders a video, writes it to the drive and checks it there, then (when the
    editor asked for YouTube) moves its own copy on the hub's SD card into the upload folder
    with a record next to it (spool_video). The drive's copy is the master; this one is
    deleted once YouTube has it.
  - This file also runs as its own service (hub-upload): one upload at a time, oldest first,
    so a long upload never holds up the next render.
  - Resumable uploads (Google's protocol): a dropped connection, a restart or a take in the
    middle carries on from the last byte YouTube has, not from the start.
  - It pauses while a take or live session runs, like ingest.
  - Python's standard library only. The login is an OAuth refresh token for the
    youtube.upload scope, made once on the Mac with mac/youtube_login.py.

A record (<key>.json next to <key>.mp4) is the upload's whole state:
  status    queued | uploading | paused | retrying | waiting | done | error
  progress  0..1; bytes; session_uri (YouTube's resumable session, valid about a week)
  video_id, url, studio_url when done; error / note for the person at the editor.
"""
import http.client
import json
import os
import shutil
import socket
import sys
import time
import urllib.parse

DEFAULT_CONFIG = {
    "credentials_path": "/var/lib/hubd/youtube/credentials.json",
    "spool_dir": "/var/lib/hubd/youtube/uploads",
    "hub_api": "http://127.0.0.1",
    "token_url": "https://oauth2.googleapis.com/token",
    "upload_url": "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
    "chunk_bytes": 8 * 1024 * 1024,      # a multiple of 256 KiB, as the protocol asks
    "idle_poll_s": 10,
}
CATEGORY_MUSIC = "10"
TITLE_MAX = 100                          # YouTube's limits; neither may contain < or >
DESCRIPTION_MAX_BYTES = 5000
RETRY_BACKOFF_S = (30, 60, 120, 300, 600)
QUOTA_WAIT_S = 3600                      # quota or upload limit: try again in an hour
WAITING = ("queued", "uploading", "paused", "retrying", "waiting")


class Retry(Exception):
    """A network problem or a server error: try again later, from where YouTube got to."""


class LoginProblem(Exception):
    """The login doesn't work any more: sign in again with mac/youtube_login.py."""


# ------------------------------------------------------------------ the video's details

def clean_text(text):
    return " ".join(str(text or "").replace("<", "").replace(">", "").split())


def video_metadata(song_name, title, take_id):
    """YouTube's snippet and status for a finished video: the editor's title line (or the
    song's name) as the name, the lines below it and the take's date as the description."""
    title = title or {}
    heading = clean_text(title.get("text")) if title.get("on") else ""
    name = (heading or clean_text(song_name) or "Untitled")[:TITLE_MAX]
    lines = [clean_text(line) for line in (title.get("lines") or []) if clean_text(line)] if title.get("on") else []
    day = "%s-%s-%s" % (take_id[0:4], take_id[4:6], take_id[6:8])
    description = "\n".join(lines + ([""] if lines else []) + ["Recorded %s." % day])
    description = description.encode("utf-8")[:DESCRIPTION_MAX_BYTES].decode("utf-8", "ignore")
    return {"snippet": {"title": name, "description": description, "categoryId": CATEGORY_MUSIC},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}


# ------------------------------------------------------------------ the upload folder

def record_path(spool_dir, key):
    return os.path.join(spool_dir, key + ".json")


def read_record(spool_dir, key):
    try:
        with open(record_path(spool_dir, key)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_record(spool_dir, record):
    path = record_path(spool_dir, record["key"])
    with open(path + ".tmp", "w") as f:
        json.dump(record, f, indent=1)
    os.replace(path + ".tmp", path)


def spool_video(spool_dir, video_path, key, metadata, details, clock=time.time):
    """Called by hub-ingest: move its finished copy into the upload folder and queue it.
    details: what the editor shows (take, song, video_number, file on the drive)."""
    os.makedirs(spool_dir, exist_ok=True)
    destination = os.path.join(spool_dir, key + ".mp4")
    shutil.move(video_path, destination)               # a rename: the same SD card
    record = dict(details, key=key, status="queued", progress=0.0, bytes=os.path.getsize(destination),
                  metadata=metadata, queued_at=clock(), session_uri=None, error=None, note=None)
    write_record(spool_dir, record)
    return record


def waiting_records(spool_dir, now):
    """Records still to upload, oldest first, skipping those told to wait until later."""
    records = []
    for name in sorted(os.listdir(spool_dir)) if os.path.isdir(spool_dir) else []:
        if name.endswith(".json"):
            record = read_record(spool_dir, name[:-len(".json")])
            if record and record.get("status") in WAITING and (record.get("retry_at") or 0) <= now:
                records.append(record)
    return sorted(records, key=lambda r: r.get("queued_at") or 0)


# ------------------------------------------------------------------ HTTP

class Http:
    """Just enough HTTP(S) for OAuth and the resumable upload (urllib would follow YouTube's
    308 "resume incomplete" as a redirect)."""

    def __init__(self, timeout_s=120):
        self.timeout_s = timeout_s

    def request(self, method, url, headers=None, body=b""):
        parts = urllib.parse.urlsplit(url)
        connection_class = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(parts.netloc, timeout=self.timeout_s)
        path = parts.path + ("?" + parts.query if parts.query else "")
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
        except (OSError, http.client.HTTPException, socket.timeout) as error:
            raise Retry("network: %s" % error)
        finally:
            connection.close()


def error_reason(body):
    """Google's error reason (e.g. quotaExceeded) and message from an error response."""
    try:
        error = json.loads(body or b"{}").get("error") or {}
    except ValueError:
        return "", (body or b"")[:200].decode("utf-8", "replace")
    if isinstance(error, str):                         # the token endpoint: {"error": "invalid_grant"}
        return error, error
    reasons = [e.get("reason") for e in error.get("errors") or [] if e.get("reason")]
    return (reasons[0] if reasons else ""), error.get("message") or ""


# ------------------------------------------------------------------ the uploader

class Uploader:
    def __init__(self, cfg, http_client=None, clock=time.time, rig_busy=lambda: False):
        self.cfg = dict(DEFAULT_CONFIG, **(cfg or {}))
        self.http = http_client or Http()
        self.clock = clock
        self.rig_busy = rig_busy
        self.token, self.token_expires = None, 0

    # -- login
    def credentials(self):
        """{client_id, client_secret, refresh_token} from mac/youtube_login.py, or None."""
        try:
            with open(self.cfg["credentials_path"]) as f:
                found = json.load(f)
        except (OSError, ValueError):
            return None
        return found if all(found.get(k) for k in ("client_id", "client_secret", "refresh_token")) else None

    def access_token(self, renew=False):
        if self.token and not renew and self.clock() < self.token_expires - 60:
            return self.token
        login = self.credentials()
        if not login:
            raise LoginProblem("not signed in to YouTube: run mac/youtube_login.py")
        body = urllib.parse.urlencode({"client_id": login["client_id"], "client_secret": login["client_secret"],
                                       "refresh_token": login["refresh_token"], "grant_type": "refresh_token"}).encode()
        status, _, answer = self.http.request("POST", self.cfg["token_url"],
                                              {"Content-Type": "application/x-www-form-urlencoded"}, body)
        if status >= 500:
            raise Retry("Google's login server: %d" % status)
        if status != 200:
            reason, message = error_reason(answer)
            raise LoginProblem("YouTube login refused (%s): sign in again with mac/youtube_login.py" % (reason or message))
        found = json.loads(answer)
        self.token, self.token_expires = found["access_token"], self.clock() + int(found.get("expires_in", 3600))
        return self.token

    def call(self, method, url, headers, body=b""):
        """One request with the login; renews the token once if YouTube says it's expired."""
        for renew in (False, True):
            status, answer_headers, answer = self.http.request(
                method, url, dict(headers, Authorization="Bearer " + self.access_token(renew)), body)
            if status != 401:
                return status, answer_headers, answer
        raise LoginProblem("YouTube refused the login: sign in again with mac/youtube_login.py")

    # -- one upload
    def save(self, record, **changes):
        record.update(changes)
        write_record(self.cfg["spool_dir"], record)

    def video_path(self, record):
        return os.path.join(self.cfg["spool_dir"], record["key"] + ".mp4")

    def start_session(self, record):
        size = os.path.getsize(self.video_path(record))
        status, headers, answer = self.call("POST", self.cfg["upload_url"], {
            "Content-Type": "application/json; charset=UTF-8", "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4"}, json.dumps(record["metadata"]).encode())
        self.check(status, answer)
        if not headers.get("location"):
            raise Retry("YouTube gave no upload address")
        self.save(record, session_uri=headers["location"])

    def uploaded_so_far(self, record, size):
        """Ask YouTube how much of the file it has: (bytes, finished video or None)."""
        status, headers, answer = self.call("PUT", record["session_uri"], {
            "Content-Length": "0", "Content-Range": "bytes */%d" % size})
        if status in (200, 201):
            return size, json.loads(answer)
        if status in (404, 410):                       # the session ran out: start again
            self.save(record, session_uri=None)
            return 0, None
        if status != 308:
            self.check(status, answer)
        return next_byte(headers), None

    def check(self, status, answer):
        """Raise for an error answer: Retry for server trouble, else a plain error."""
        if status < 400:
            return
        reason, message = error_reason(answer)
        if status >= 500 or status == 429:
            raise Retry("YouTube: %d %s" % (status, reason or message))
        if reason in ("quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"):
            raise QuotaWait("YouTube's daily limit (%s): tries again in an hour" % reason)
        raise UploadRefused("YouTube refused the video: %s %s" % (reason or status, message))

    def upload(self, record):
        """Upload (or carry on uploading) one video. Returns "done" or "paused"."""
        path = self.video_path(record)
        size = os.path.getsize(path)
        if not record.get("session_uri"):
            self.start_session(record)
        offset, video = self.uploaded_so_far(record, size)
        if record.get("session_uri") is None:          # it had run out
            self.start_session(record)
            offset, video = 0, None
        self.save(record, status="uploading", progress=round(offset / size, 3), note=None, error=None)
        with open(path, "rb") as f:
            while video is None:
                if self.rig_busy():
                    self.save(record, status="paused", note="paused while recording")
                    return "paused"
                f.seek(offset)
                chunk = f.read(self.cfg["chunk_bytes"])
                end = offset + len(chunk) - 1
                status, headers, answer = self.call("PUT", record["session_uri"], {
                    "Content-Length": str(len(chunk)), "Content-Range": "bytes %d-%d/%d" % (offset, end, size)}, chunk)
                if status in (200, 201):
                    video = json.loads(answer)
                elif status == 308:
                    offset = next_byte(headers)
                    self.save(record, progress=round(offset / size, 3))
                elif status in (404, 410):
                    self.save(record, session_uri=None)
                    raise Retry("the upload session ran out; starting it again")
                else:
                    self.check(status, answer)
                    raise Retry("YouTube: unexpected %d" % status)
        video_id = video["id"]
        self.save(record, status="done", progress=1.0, video_id=video_id, url="https://youtu.be/" + video_id,
                  studio_url="https://studio.youtube.com/video/%s/edit" % video_id, finished_at=self.clock(),
                  session_uri=None, retry_at=None, attempts=0)
        os.remove(path)                                # the drive keeps the master
        return "done"

    # -- the service
    def run_once(self):
        """Work on the oldest waiting upload. Returns what happened, for the loop and tests."""
        if not self.credentials():
            return "not signed in"
        records = waiting_records(self.cfg["spool_dir"], self.clock())
        if not records:
            return "nothing to upload"
        if self.rig_busy():
            for record in records:
                if record["status"] == "uploading":
                    self.save(record, status="paused", note="paused while recording")
            return "rig busy"
        record = records[0]
        try:
            return self.upload(record)
        except Retry as problem:
            attempts = (record.get("attempts") or 0) + 1
            wait = RETRY_BACKOFF_S[min(attempts, len(RETRY_BACKOFF_S)) - 1]
            self.save(record, status="retrying", attempts=attempts, retry_at=self.clock() + wait,
                      note="%s; trying again in %d s" % (problem, wait))
            return "retrying"
        except QuotaWait as problem:
            self.save(record, status="waiting", retry_at=self.clock() + QUOTA_WAIT_S, note=str(problem))
            return "waiting"
        except LoginProblem as problem:
            self.save(record, status="waiting", retry_at=self.clock() + 600, note=str(problem))
            return "login problem"
        except UploadRefused as problem:
            self.save(record, status="error", error=str(problem)[-500:], session_uri=None)
            return "error"


class QuotaWait(Exception):
    """YouTube's daily quota or upload limit: wait and try again."""


class UploadRefused(Exception):
    """YouTube won't take this video (bad details, the channel can't upload): needs a person."""


def next_byte(headers):
    """The first byte YouTube still needs, from a 308's Range header ("bytes=0-1234")."""
    value = headers.get("range")
    return int(value.rsplit("-", 1)[1]) + 1 if value else 0


def hub_is_busy(http_client, hub_api):
    """A take or live session (or hubd unreachable, to be safe), as hub-ingest decides it."""
    try:
        status, _, body = http_client.request("GET", hub_api + "/api/status")
        state = json.loads(body) if status == 200 else None
    except (Retry, ValueError):
        state = None
    if state is None:
        return True
    return state.get("state") != "idle" or any(
        (node.get("status") or {}).get("mode") not in (None, "idle", "snapshot") for node in (state.get("nodes") or {}).values())


def main():
    path = os.environ.get("UPLOAD_CONFIG", "/etc/hubd/upload.json")
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(path):
        with open(path) as f:
            cfg.update(json.load(f))
    client = Http()
    uploader = Uploader(cfg, client, rig_busy=lambda: hub_is_busy(client, cfg["hub_api"]))
    while True:
        try:
            outcome = uploader.run_once()
        except Exception as error:  # noqa: BLE001 - keep the service up; the next round tries again
            print("upload problem: %s" % error, file=sys.stderr)
            outcome = "problem"
        if outcome in ("done",):
            continue
        time.sleep(cfg["idle_poll_s"] if outcome != "rig busy" else 15)


if __name__ == "__main__":
    main()
