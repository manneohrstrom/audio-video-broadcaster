"""The STUDIO drive as the record of every take (docs/stick-db-plan.md).

Everything lasting about a take lives in its folder on the drive, so that deleting the
folder deletes the take and a drive plugged into a fresh hub brings all its takes along:

    takes/<YYYY-MM-DD>_<take>/
        take.json                      cameras, manifests and, under "recording", the take-log
                                       record (REC/STOP, sync warnings, errors, calibration)
        audio/songs.json, audio/<song>/v<N>/...    bounces and their syncs (copied by ingest)
        edits/<song>/<song>-video<N>.mp4 + .json   finished videos and the edit each was made from
        edits/<song>/working-edit.json             the editor's working cut

This module only reads and describes; ingest.py does the mounting and the writing.
"""
import hashlib
import json
import os
import re

TAKE_FOLDER_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_(\d{8}-\d{6})$")
VIDEO_RE = re.compile(r"^(?P<song>.+)-video(?P<number>\d+)\.mp4$")
WORKING_EDIT = "working-edit.json"


def take_folder_name(take_id):
    return "%s-%s-%s_%s" % (take_id[:4], take_id[4:6], take_id[6:8], take_id)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def digest(value):
    """A short fingerprint of a JSON value, to tell whether it changed."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


# -- the take-log record

def read_take_log(path):
    """hubd's take log (a list of records, one per REC/STOP), or [] if there is none."""
    log = read_json(path, [])
    return log if isinstance(log, list) else []


def recording_for(log, take_id):
    """The take-log record of one take (the last one wins, as on the hub page), or None.
    Aborted takes have none: they never reach the drive."""
    found = None
    for entry in log:
        if entry.get("take_id") == take_id and not entry.get("aborted"):
            found = entry
    return found


# -- what is on the drive

def videos_in(take_folder):
    """{song: [{"number", "file", "bounce_version", "bytes"}]}: the finished videos in edits/."""
    edits = os.path.join(take_folder, "edits")
    out = {}
    try:
        songs = sorted(os.listdir(edits))
    except OSError:
        return out
    for song in songs:
        try:
            names = sorted(os.listdir(os.path.join(edits, song)))
        except OSError:
            continue
        for name in names:
            match = VIDEO_RE.match(name)
            if not match or match.group("song") != song:
                continue
            path = os.path.join(edits, song, name)
            edit = read_json(path[:-len(".mp4")] + ".json", {})
            out.setdefault(song, []).append({
                "number": int(match.group("number")),
                "file": os.path.join("edits", song, name),
                "bounce_version": edit.get("song_version"),
                "bytes": os.path.getsize(path),
            })
        if song in out:
            out[song].sort(key=lambda video: video["number"])
    return out


def working_edits_in(take_folder):
    """{song: working cut} for every edits/<song>/working-edit.json."""
    edits = os.path.join(take_folder, "edits")
    out = {}
    try:
        songs = sorted(os.listdir(edits))
    except OSError:
        return out
    for song in songs:
        edit = read_json(os.path.join(edits, song, WORKING_EDIT))
        if isinstance(edit, dict):
            out[song] = edit
    return out


def describe_take(take_folder):
    """What the hub needs to list one take from its folder, or None if it isn't a finished
    take (no take.json yet: ingest writes it last)."""
    doc = read_json(os.path.join(take_folder, "take.json"))
    if not isinstance(doc, dict) or not doc.get("take_id"):
        return None
    working = working_edits_in(take_folder)
    return {
        "take_id": doc["take_id"],
        "recording": doc.get("recording"),
        "cameras": doc.get("cameras") or {},
        "videos": videos_in(take_folder),
        "working_edits": {song: digest(edit) for song, edit in working.items()},
    }


def index_drive(mount_point, drive_id, now):
    """Every finished take on the drive: {"drive_id", "indexed_at", "takes": {take_id: ...}}.
    Reads only small JSON files and directory listings, never video."""
    takes = {}
    root = os.path.join(mount_point, "takes")
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for name in names:
        match = TAKE_FOLDER_RE.match(name)
        if not match:
            continue
        described = describe_take(os.path.join(root, name))
        if described and described["take_id"] == match.group(1):
            takes[described["take_id"]] = described
    return {"drive_id": drive_id, "indexed_at": now, "takes": takes}


# -- a take that came in on the drive

INGEST_STEPS = ["pulled", "checked", "conformed", "verified", "written", "marked"]


def adopted_take_state(described, drive_id):
    """Ingest's state for a take found on the drive but unknown to this hub: every camera
    done (it's verified and written), so nothing is pulled again."""
    state = {"complete": True, "drive_id": drive_id, "adopted": True}
    for camera_id, camera in sorted(described["cameras"].items()):
        state[camera_id] = dict(camera, done=list(INGEST_STEPS), step=None, progress=None, error=None)
    return state
