"""Editor projects: what the editor page needs to edit one song, and the edit it saves.

The editor (docs/editor-plan.md, M-E3) edits one synced song version at a time. For that
it needs, on the hub clock (nanoseconds, the clock the cameras stamp their frames with):

  - every camera of the take: its file, where the file's t=0 sits on the hub clock, when
    it recorded, and its rough sections (dropped frames);
  - the bounce: its file and where its t=0 sits on the hub clock (the sync);
  - the song's range: where the bounce and the cameras overlap;
  - the edit so far: a list of cuts, each "from here on, show this camera".

The edit format is the one in docs/editor-plan.md section 2 (shared with the quick mix and
the render): cut times are nanoseconds from the start of the song's range.

Where edits live: one working copy per song (not per bounce version: user, 2026-11-16, a
new mix re-renders the same cut and look), saved on the hub's SD card while the user
edits (so the STUDIO drive doesn't have to be mounted read-write); a copy goes to the
drive with every rendered video. Its times are on the hub clock, so it fits any synced
bounce of the song (rebased when the song's range moves). When there is no working copy
yet, the editor starts with the first Pi camera for the whole song: nothing is cut
automatically until the user asks (user, 2026-11-28: the AI cut button, ai_cut.py; the
rule-based cut and the quick mix's cut as a start were removed).

Each camera comes with what it shows in this take (camera_tags.py): the take's own tags,
else those copied at REC, else the camera's current ones.

Sync mode (section 4b, hand_sync.py): the editor can also open any uploaded bounce version,
synced or not, to place the bounce by hand. Its project spans the whole take (the Pi
cameras' recording), shows one camera at a time and starts the bounce where the sync put it
(or its best guess, or the take's start); saving writes sync.json "by hand".
"""
import array
import json
import os
import re
import subprocess
import time

import camera_tags
import hand_sync
from names import base_camera as names_base

NS_PER_SECOND = 1_000_000_000
FRAME_RATE = 30
TAKE_ID_PATTERN = re.compile(r"^\d{8}-\d{6}$")
SONG_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
CAMERA_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
EDIT_FORMAT_VERSION = 1


class ProjectError(ValueError):
    """The request can't be served: unknown take, song not synced, bad edit, ..."""


def grid_origin_ns(first_frame_ns):
    """Hub time of a camera file's t=0: the 30 fps time-of-day slot at or before its first
    frame. Ingest stamps the file's timecode there; true for real-timing and conformed files."""
    whole_seconds, fraction_ns = divmod(first_frame_ns, NS_PER_SECOND)
    slot = fraction_ns * FRAME_RATE // NS_PER_SECOND
    return whole_seconds * NS_PER_SECOND + slot * NS_PER_SECOND // FRAME_RATE


def describe_cameras(take_document):
    """One entry per camera file in the take, in a stable order (Pi cameras, then phones)."""
    manifests = take_document.get("manifests") or {}
    ingested = take_document.get("cameras") or {}
    cameras = []
    for camera_id, manifest in manifests.items():
        first_frame_ns = manifest.get("first_frame_wallclock_ns")
        if not first_frame_ns or camera_id not in ingested or not CAMERA_ID_PATTERN.match(camera_id):
            continue
        duration_ns = int((manifest.get("duration_s") or 0) * NS_PER_SECOND)
        last_frame_ns = manifest.get("last_frame_wallclock_ns") or first_frame_ns + duration_ns
        rough_sections = [[section["start_wallclock_ns"], section["end_wallclock_ns"]]
                          for section in ingested[camera_id].get("rough") or []
                          if "start_wallclock_ns" in section]
        is_phone = manifest.get("source") == "phone"
        cameras.append({
            "id": camera_id,
            "label": manifest.get("label") or camera_id,
            "kind": "phone" if is_phone else "pi",
            # Pis are fixed on the wall; phones as set on the hub page (takes before 2026-11-28
            # didn't record it: tripod, the hub's default).
            "mount": (manifest.get("mount") or "tripod") if is_phone else "fixed",
            "is_sync_server": manifest.get("sync_role") == "server",
            "file_zero_ns": grid_origin_ns(first_frame_ns),
            "first_frame_ns": first_frame_ns,
            "last_frame_ns": last_frame_ns,
            "frames": manifest.get("frames"),
            "rough_sections_ns": rough_sections,
            "timing": ingested[camera_id].get("timing"),
        })
    cameras.sort(key=lambda camera: (camera["kind"] != "pi", camera["id"]))
    return cameras


def song_range_ns(bounce_start_ns, bounce_duration_s, cameras):
    """Where the bounce and the Pi cameras overlap: the part of the song there is video for."""
    pi_cameras = [camera for camera in cameras if camera["kind"] == "pi"] or cameras
    if not pi_cameras:
        raise ProjectError("this take has no camera files")
    start = max(bounce_start_ns, min(camera["first_frame_ns"] for camera in pi_cameras))
    end = min(bounce_start_ns + int(bounce_duration_s * NS_PER_SECOND),
              max(camera["last_frame_ns"] for camera in pi_cameras))
    if end - start < NS_PER_SECOND:
        raise ProjectError("the bounce and the video overlap by less than a second")
    return [start, end]


def take_span_ns(cameras):
    """From the first Pi camera's first frame to the last one's last frame (Sync mode's range)."""
    pi_cameras = [camera for camera in cameras if camera["kind"] == "pi"] or cameras
    if not pi_cameras:
        raise ProjectError("this take has no camera files")
    return [min(camera["first_frame_ns"] for camera in pi_cameras),
            max(camera["last_frame_ns"] for camera in pi_cameras)]


TITLE_MAX_LINES = 8           # as hub/ingest/title_render.py
TITLE_MAX_CHARACTERS = 80


def validate_title(title, song_s):
    """The edit's title sequence (the title, its colour, the names on the strip, how long they
    stay: duration_s), or None. start_s is from the older title (no longer used; kept).
    Rendered by hub/ingest/title_render.py."""
    if title is None:
        return None
    if not isinstance(title, dict):
        raise ProjectError("title must be an object")

    def text(value, what):
        if not isinstance(value, str) or len(value) > TITLE_MAX_CHARACTERS or any(ord(c) < 32 for c in value):
            raise ProjectError("%s must be one line of at most %d characters" % (what, TITLE_MAX_CHARACTERS))
        return value

    on = title.get("on", False)
    if not isinstance(on, bool):
        raise ProjectError("title.on must be true or false")
    lines = title.get("lines", [])
    if not isinstance(lines, list) or len(lines) > TITLE_MAX_LINES:
        raise ProjectError("title.lines must be a list of at most %d lines" % TITLE_MAX_LINES)
    colour = title.get("colour", "#ffffff")
    if not isinstance(colour, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
        raise ProjectError("title.colour must be a colour like #ffffff")
    start_s, duration_s = title.get("start_s", 0), title.get("duration_s", 6)
    for value, what, low, high in ((start_s, "title.start_s", 0, max(0.0, song_s)), (duration_s, "title.duration_s", 1, 60)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high:
            raise ProjectError("%s must be %g-%g seconds" % (what, low, high))
    return {"on": on, "text": text(title.get("text", ""), "title.text"),
            "lines": [text(line, "a line under the title") for line in lines],
            "colour": colour.lower(), "start_s": round(float(start_s), 3), "duration_s": round(float(duration_s), 3)}


def read_json(path):
    """The file's JSON, or None if it's missing or unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def validate_cuts(cuts, camera_ids, range_length_ns):
    """The cuts as saved: sorted, the first at 0, known cameras, no repeats of the same camera."""
    if not isinstance(cuts, list) or not cuts:
        raise ProjectError("an edit needs at least one cut")
    clean = []
    for cut in sorted(cuts, key=lambda c: c.get("at_ns", -1) if isinstance(c, dict) else -1):
        if not isinstance(cut, dict) or not isinstance(cut.get("at_ns"), int):
            raise ProjectError("each cut needs an integer at_ns")
        if cut.get("camera") not in camera_ids:
            raise ProjectError("unknown camera %r" % cut.get("camera"))
        if not 0 <= cut["at_ns"] < range_length_ns:
            raise ProjectError("cut at %d ns is outside the song" % cut["at_ns"])
        if clean and clean[-1]["camera"] == cut["camera"]:
            continue                                   # same camera again: the earlier cut holds
        clean.append({"at_ns": cut["at_ns"], "camera": cut["camera"],
                      "by": cut.get("by") if cut.get("by") in ("auto", "user", "ai") else "user"})
        for key, longest in (("reason", 400), ("note", 300)):      # the AI cut's why, and the hub's change
            if isinstance(cut.get(key), str) and cut[key].strip():
                clean[-1][key] = cut[key].strip()[:longest]
        if cut.get("story_glide") is True:             # the story glides here instead of dissolving (story_render.py)
            clean[-1]["story_glide"] = True
        move = clean_move(cut.get("move"))
        if move:
            clean[-1]["move"] = move
    clean[0]["at_ns"] = 0
    return clean


# A shot's camera move (a slow zoom or drift made in the render; hub/ingest/camera_move.py,
# which hubd doesn't import: the two stay in step through the tests).
MOVE_KINDS = ("in", "out", "left", "right")
DEFAULT_MOVE_AMOUNT = 0.5


def clean_move(move):
    """A cut's move as saved ({"kind", "amount" 0.05..1}), or None for none (or one that isn't valid)."""
    if not isinstance(move, dict) or move.get("kind") not in MOVE_KINDS:
        return None
    amount = move.get("amount", DEFAULT_MOVE_AMOUNT)
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        amount = DEFAULT_MOVE_AMOUNT
    return {"kind": move["kind"], "amount": round(min(1.0, max(0.05, float(amount))), 3)}


def rebase_cuts(cuts, old_range_ns, new_range_ns):
    """Move cuts made against an older song range onto the current one (the bounce was
    synced again, so the range moved a little). Cut times stay the same on the hub clock;
    cuts that now fall before the start collapse into the first cut, ones after the end go.
    None if the ranges don't overlap at all (then the old edit belongs to something else)."""
    old_start, old_end = old_range_ns
    new_start, new_end = new_range_ns
    if old_end <= new_start or new_end <= old_start:
        return None
    shift = old_start - new_start
    moved, before_start = [], None
    for cut in sorted(cuts, key=lambda c: c["at_ns"]):
        at_ns = cut["at_ns"] + shift
        if at_ns <= 0:
            before_start = dict(cut, at_ns=0)              # the latest one before the start wins
        elif at_ns < new_end - new_start:
            moved.append(dict(cut, at_ns=at_ns))
    return ([before_start] if before_start else []) + moved


MIN_VIDEO_NS = 1_000_000_000          # the shortest video a start and end may leave


def validate_trim(trim_ns, range_length_ns):
    """The video's start and end, [start, end] in ns from the start of the range, or None
    for the whole song (user, 2026-11-21: the clap stays in the edit, not in the video)."""
    if trim_ns is None:
        return None
    if (not isinstance(trim_ns, list) or len(trim_ns) != 2
            or not all(isinstance(value, int) and not isinstance(value, bool) for value in trim_ns)):
        raise ProjectError("trim_ns must be [start, end] in integer nanoseconds")
    start, end = trim_ns
    if not 0 <= start < end <= range_length_ns:
        raise ProjectError("the video's start and end must be inside the song, start first")
    if end - start < MIN_VIDEO_NS:
        raise ProjectError("the video must be at least 1 s long")
    return [start, end]


def rebase_trim(trim_ns, old_range_ns, new_range_ns):
    """The start and end moved onto a new song range (as rebase_cuts), clamped into it;
    None when there's nothing left of it or there was none."""
    if not isinstance(trim_ns, list) or len(trim_ns) != 2:
        return None
    shift = old_range_ns[0] - new_range_ns[0]
    length = new_range_ns[1] - new_range_ns[0]
    start = max(0, int(trim_ns[0]) + shift)
    end = min(length, int(trim_ns[1]) + shift)
    if end - start < MIN_VIDEO_NS:
        return None
    return None if start == 0 and end == length else [start, end]


def validate_story(story, camera_ids, range_length_ns):
    """The Instagram story (hub/ingest/story_render.py), or None: range_ns, its start and end
    as trim_ns (or None: 30 s from the video's start), and framing, where the 9:16 slice sits
    in each camera's picture (0 left edge .. 1 right edge; cameras not in it are centred)."""
    if story is None:
        return None
    if not isinstance(story, dict):
        raise ProjectError("story must be an object")
    framing = story.get("framing")
    framing = {} if framing is None else framing
    if not isinstance(framing, dict):
        raise ProjectError("story.framing must be {camera: 0-1}")
    clean = {}
    for camera, position in framing.items():
        if camera not in camera_ids:
            continue                                   # a camera that isn't in this take any more
        if not isinstance(position, (int, float)) or isinstance(position, bool) or not 0 <= position <= 1:
            raise ProjectError("story.framing values must be 0-1")
        clean[camera] = round(float(position), 4)
    try:
        range_ns = validate_trim(story.get("range_ns"), range_length_ns)
    except ProjectError as error:
        raise ProjectError("story: %s" % str(error).replace("the video", "the story")) from None
    return {"range_ns": range_ns, "framing": clean}


def rebase_story(story, old_range_ns, new_range_ns):
    """The story's start and end moved onto a new song range (as rebase_trim)."""
    if not isinstance(story, dict):
        return None
    range_ns = story.get("range_ns")
    return dict(story, range_ns=rebase_trim(range_ns, old_range_ns, new_range_ns) if range_ns else None)


def first_camera_cut(cameras):
    """A new song's starting cut: the first Pi camera (cam1 normally) for the whole song."""
    first = next((camera for camera in cameras if camera["kind"] == "pi"), cameras[0])
    return [{"at_ns": 0, "camera": first["id"], "by": "user"}]


class EditorProjects:
    """Builds editor projects and saves edits.

    audio: hubd's Audio (songs, versions, the hub-side audio folder).
    read_take_document(take_id): the take's take.json from the STUDIO drive (dict).
    edits_dir: where working copies are saved on the hub.
    thumbs_dir: strip thumbnails made by hub-ingest (hub/ingest/thumbnails.py).
    proxies_dir: editing proxies made by hub-ingest (hub/ingest/proxies.py).
    request_thumbnails(take_id): ask hub-ingest to make missing thumbnails now.
    waveforms_dir: cached waveforms of bounces.
    read_drive_edit(take_id, song_id): the working cut kept in the take's folder on the drive
        (edits/<song>/working-edit.json, copied there by hub-ingest), or None.
    sync_job(take_id, song_id, version): hub-ingest's automatic sync job for it, or None.
    tags: camera_tags.CameraTags, what each camera shows.
    """

    def __init__(self, audio, read_take_document, edits_dir, thumbs_dir=None,
                 request_thumbnails=None, waveforms_dir=None, proxies_dir=None, clock=time.time, ffmpeg="ffmpeg",
                 read_drive_edit=None, sync_job=None, names=None, tags=None):
        self.audio = audio
        self.names = names                  # names.Names: the user's names for cameras and the take
        self.read_take_document = read_take_document
        self.tags = tags                    # camera_tags.CameraTags: what each camera shows
        self.edits_dir = edits_dir
        self.thumbs_dir = thumbs_dir or os.path.join(edits_dir, "..", "thumbs")
        self.request_thumbnails = request_thumbnails or (lambda take_id: None)
        self.waveforms_dir = waveforms_dir or os.path.join(edits_dir, "..", "waveforms")
        self.proxies_dir = proxies_dir or os.path.join(edits_dir, "..", "proxies")
        self.clock = clock
        self.ffmpeg = ffmpeg
        self.read_drive_edit = read_drive_edit or (lambda take_id, song_id: None)
        self.sync_job = sync_job or (lambda take_id, song_id, version: None)

    # -- paths
    def _check_ids(self, take_id, song_id, version):
        if not TAKE_ID_PATTERN.match(take_id or "") or not SONG_ID_PATTERN.match(song_id or ""):
            raise ProjectError("bad take or song")
        if not isinstance(version, int) or version < 1:
            raise ProjectError("bad version")

    def working_copy_path(self, take_id, song_id, version=1):
        """The song's edit (one per song; `version` is only checked, kept for the callers)."""
        self._check_ids(take_id, song_id, version)
        return os.path.join(self.edits_dir, take_id, song_id + ".json")

    def _older_per_version_copy(self, take_id, song_id):
        """Edits saved before 2026-11-17 were per bounce version (edits/<take>/<song>/v<N>.json):
        the newest of those is the song's edit until it's saved again."""
        folder = os.path.join(self.edits_dir, take_id, song_id)
        try:
            names = [n for n in os.listdir(folder) if re.match(r"^v\d+\.json$", n)]
        except OSError:
            return None
        if not names:
            return None
        return os.path.join(folder, max(names, key=lambda n: os.path.getmtime(os.path.join(folder, n))))

    def synced_versions(self, take_id, song):
        """Bounce versions of the song that are uploaded and synced, newest first."""
        out = []
        for version in sorted(song["versions"], key=lambda v: -v["version"]):
            path = os.path.join(self.audio.take_dir(take_id), song["id"], "v%d" % version["version"], "sync.json")
            if version.get("complete") and os.path.exists(path):
                out.append({"version": version["version"], "file_name": version["filename"]})
        return out

    # -- the project
    def _version_dir(self, take_id, song_id, version):
        return os.path.join(self.audio.take_dir(take_id), song_id, "v%d" % version)

    def _uploaded_version(self, take_id, song_id, version):
        """(song, version info) of an uploaded bounce version, synced or not."""
        songs = self.audio.list(take_id)["songs"]
        song = next((s for s in songs if s["id"] == song_id), None)
        if song is None:
            raise ProjectError("no song %r in take %s" % (song_id, take_id))
        version_info = next((v for v in song["versions"] if v["version"] == version), None)
        if version_info is None or not version_info.get("complete"):
            raise ProjectError("version %d isn't uploaded" % version)
        return song, version_info

    def _song_and_version(self, take_id, song_id, version):
        song, version_info = self._uploaded_version(take_id, song_id, version)
        sync_path = os.path.join(self._version_dir(take_id, song_id, version), "sync.json")
        try:
            with open(sync_path) as f:
                sync = json.load(f)
        except (OSError, ValueError):
            raise ProjectError("this song isn't synced to the take yet") from None
        return song, version_info, sync

    def bounce_files(self, take_id, song_id, version):
        """(the bounce's path, its beats.json read, or None) for a synced song version (ai_cut.py)."""
        self._check_ids(take_id, song_id, version)
        _, version_info, _ = self._song_and_version(take_id, song_id, version)
        folder = self._version_dir(take_id, song_id, version)
        return os.path.join(folder, version_info["filename"]), read_json(os.path.join(folder, "beats.json"))

    def project(self, take_id, song_id, version, mode="edit"):
        """Everything the editor page needs for one song version. mode "sync": Sync mode
        (place the bounce by hand), for any uploaded version, synced or not."""
        self._check_ids(take_id, song_id, version)
        if mode == "sync":
            return self._sync_project(take_id, song_id, version)
        song, version_info, sync = self._song_and_version(take_id, song_id, version)
        take_document = self.read_take_document(take_id)
        cameras = describe_cameras(take_document)
        self._attach_tags(take_id, cameras, take_document)
        bounce_start_ns = int(sync["bounce_start_ns"])
        range_ns = song_range_ns(bounce_start_ns, version_info["duration_s"], cameras)
        edit, source = self._load_edit(take_id, song_id, version, cameras, range_ns)
        return self._project_document(take_id, song, version, version_info, cameras, range_ns, bounce_start_ns,
                                      {key: sync.get(key) for key in ("anchor", "confidence", "drift_ppm")},
                                      edit, source)

    def _sync_project(self, take_id, song_id, version):
        """Sync mode's project: the whole take, one camera showing, the bounce at its starting point."""
        song, version_info = self._uploaded_version(take_id, song_id, version)
        take_document = self.read_take_document(take_id)
        cameras = describe_cameras(take_document)
        self._attach_tags(take_id, cameras, take_document)
        range_ns = take_span_ns(cameras)
        start = hand_sync.starting_point(self._version_dir(take_id, song_id, version),
                                         self.audio.take_dir(take_id), range_ns[0])
        server = next((camera for camera in cameras if camera["is_sync_server"]), cameras[0])
        edit = self._new_edit(take_id, song_id, version, range_ns,
                              [{"at_ns": 0, "camera": server["id"], "by": "user"}], "sync")
        document = self._project_document(take_id, song, version, version_info, cameras, range_ns,
                                          start["bounce_start_ns"], {"anchor": start["anchor"],
                                                                     "confidence": start["confidence"]},
                                          edit, "sync")
        document["mode"] = "sync"
        document["sync_start"] = start
        return document

    def _attach_tags(self, take_id, cameras, take_document):
        """camera["tags"] and camera["tags_from"] ("take", "recorded", "current" or None) for
        this take (camera_tags.CameraTags.for_take)."""
        recorded = (take_document.get("recording") or {}).get("camera_tags")
        by_camera = self.tags.for_take(take_id, recorded) if self.tags else {}
        for camera in cameras:
            tags, source = by_camera.get(names_base(camera["id"]), (camera_tags.empty_tags(), None))
            camera["tags"], camera["tags_from"] = tags, source

    def _project_document(self, take_id, song, version, version_info, cameras, range_ns, bounce_start_ns,
                          sync_summary, edit, source):
        song_id = song["id"]
        missing_thumbnails = False
        for camera in cameras:
            camera["label"] = (self.names and self.names.camera(camera["id"])) or camera["label"]
            camera["file_url"] = "/media/%s/%s.mov" % (take_id, camera["id"])
            camera["thumbnails_url"] = "/media-thumbs/%s/%s/" % (take_id, camera["id"])
            camera["thumbnails_ready"] = os.path.exists(
                os.path.join(self.thumbs_dir, take_id, camera["id"], "index.json"))
            # The editor's video window uses the small editing proxy when there is one.
            has_proxy = os.path.exists(os.path.join(self.proxies_dir, take_id, camera["id"] + ".mp4"))
            camera["proxy_url"] = "/media-proxy/%s/%s.mp4" % (take_id, camera["id"]) if has_proxy else None
            missing_thumbnails = missing_thumbnails or not camera["thumbnails_ready"] or not has_proxy
        if missing_thumbnails:
            self.request_thumbnails(take_id)
        # The bounce's bars (bounce seconds), for the Beat pulse effect's preview.
        beats = read_json(os.path.join(self._version_dir(take_id, song_id, version), "beats.json"))
        downbeats_s = (beats.get("downbeats_s") if isinstance(beats, dict) else None) or []
        return {
            "take_id": take_id,
            "take_name": self.names.take(take_id) if self.names else None,
            "song": {"id": song_id, "name": song["name"]},
            "version": version,
            "range_ns": range_ns,
            "cameras": cameras,
            "bounce": {
                "url": "/media-audio/%s/%s/v%d" % (take_id, song_id, version),
                "waveform_url": "/api/editor/%s/%s/v%d/waveform" % (take_id, song_id, version),
                "start_ns": bounce_start_ns,
                "duration_s": version_info["duration_s"],
                "file_name": version_info["filename"],
                "sync": sync_summary,
                "downbeats_s": downbeats_s,
            },
            "mode": "edit",
            "edit": edit,
            "edit_source": source,
            "bounce_versions": self.synced_versions(take_id, song),
            "tag_vocabulary": self.tags.vocabulary() if self.tags else None,
        }

    def _load_edit(self, take_id, song_id, version, cameras, range_ns):
        """(edit, where it came from): the working copy ("saved"), else the first camera for
        the whole song ("new")."""
        camera_ids = {camera["id"] for camera in cameras}
        length_ns = range_ns[1] - range_ns[0]
        saved = self._newest_working_copy(take_id, song_id, version)
        try:
            cuts = rebase_cuts(saved["cuts"], saved["range_ns"], range_ns)
            if cuts:
                trim_ns = rebase_trim(saved.get("trim_ns"), saved["range_ns"], range_ns)
                story = rebase_story(saved.get("story"), saved["range_ns"], range_ns)
                saved.update(cuts=validate_cuts(cuts, camera_ids, length_ns), range_ns=range_ns, trim_ns=trim_ns,
                             story=story)
                return saved, "saved"
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return self._new_edit(take_id, song_id, version, range_ns, first_camera_cut(cameras), "first-camera"), "new"

    def _newest_working_copy(self, take_id, song_id, version):
        """The newer (by saved_at) of the hub's working copy and the drive's, or None.
        A drive copy that wins is saved on the hub too: the render reads the hub's copy."""
        path = self.working_copy_path(take_id, song_id, version)
        if not os.path.exists(path):
            path = self._older_per_version_copy(take_id, song_id) or path
        try:
            with open(path) as f:
                on_hub = json.load(f)
        except (OSError, ValueError):
            on_hub = None
        on_drive = self.read_drive_edit(take_id, song_id)
        if not isinstance(on_drive, dict) or (
                isinstance(on_hub, dict) and (on_hub.get("saved_at") or 0) >= (on_drive.get("saved_at") or 0)):
            return on_hub
        self._write_working_copy(take_id, song_id, version, on_drive)
        return on_drive

    def _write_working_copy(self, take_id, song_id, version, edit):
        path = self.working_copy_path(take_id, song_id, version)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w") as f:
            json.dump(edit, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)

    def _new_edit(self, take_id, song_id, version, range_ns, cuts, made_by):
        return {"version": EDIT_FORMAT_VERSION, "take": take_id, "song": song_id, "song_version": version,
                "range_ns": range_ns, "cuts": cuts, "look": None, "dissolve_s": 0.3,
                "outputs": ["youtube"], "made_by": made_by, "notes": []}

    # -- the bounce's waveform
    WAVEFORM_RATE = 100           # peaks per second
    DECODE_RATE = 4000            # enough to see the shape of the music

    def waveform(self, take_id, song_id, version):
        """{"rate": 100, "peaks": [0..255, ...]}: the bounce's loudest sample per 10 ms,
        made once with ffmpeg and cached (a 1-hour bounce takes a few seconds)."""
        self._check_ids(take_id, song_id, version)
        cache = os.path.join(self.waveforms_dir, take_id, "%s-v%d.json" % (song_id, version))
        try:
            with open(cache) as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
        _, version_info = self._uploaded_version(take_id, song_id, version)     # synced or not (Sync mode)
        source = os.path.join(self._version_dir(take_id, song_id, version), version_info["filename"])
        raw = subprocess.run([self.ffmpeg, "-nostdin", "-v", "error", "-i", source, "-ac", "1",
                              "-ar", str(self.DECODE_RATE), "-f", "s16le", "-"], capture_output=True, check=True).stdout
        samples = array.array("h")
        samples.frombytes(raw[:len(raw) // 2 * 2])
        per_peak = self.DECODE_RATE // self.WAVEFORM_RATE
        peaks = []
        for start in range(0, len(samples), per_peak):
            chunk = samples[start:start + per_peak]
            loudest = max(max(chunk), -min(chunk)) if chunk else 0
            peaks.append(min(255, loudest * 255 // 32767))
        result = {"rate": self.WAVEFORM_RATE, "peaks": peaks}
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache + ".tmp", "w") as f:
            json.dump(result, f, separators=(",", ":"))
        os.replace(cache + ".tmp", cache)
        return result

    # -- sync by hand
    def save_sync(self, take_id, song_id, version, bounce_start_ns):
        """Sync mode's Save: the bounce starts at bounce_start_ns on the hub clock (by hand)."""
        self._check_ids(take_id, song_id, version)
        _, version_info = self._uploaded_version(take_id, song_id, version)
        if not isinstance(bounce_start_ns, int) or isinstance(bounce_start_ns, bool):
            raise ProjectError("bounce_start_ns must be an integer (nanoseconds on the hub clock)")
        job = self.sync_job(take_id, song_id, version) or {}
        if job.get("status") in ("queued", "syncing"):
            raise ProjectError("the automatic sync is running for this bounce: wait for it, then sync by hand")
        cameras = describe_cameras(self.read_take_document(take_id))
        song_range_ns(bounce_start_ns, version_info["duration_s"], cameras)    # refuses: no overlap with the video
        return hand_sync.save(self._version_dir(take_id, song_id, version), bounce_start_ns, self.clock)

    # -- saving
    def save_edit(self, take_id, song_id, version, edit):
        """Save the working copy (atomically). Returns what was saved."""
        project = self.project(take_id, song_id, version)
        range_ns = project["range_ns"]
        camera_ids = {camera["id"] for camera in project["cameras"]}
        cuts = validate_cuts(edit.get("cuts"), camera_ids, range_ns[1] - range_ns[0])
        dissolve_s = edit.get("dissolve_s", 0.3)
        if not isinstance(dissolve_s, (int, float)) or not 0 <= dissolve_s <= 2:
            raise ProjectError("dissolve_s must be 0-2 seconds")
        branding = edit.get("branding", False)     # the border and logo on top (brand-border.png, brand-logo.png)
        if not isinstance(branding, bool):
            raise ProjectError("branding must be true or false")
        branding_colour = edit.get("branding_colour", "#ffffff")
        if not isinstance(branding_colour, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", branding_colour):
            raise ProjectError("branding_colour must be a colour like #ffffff")
        title = validate_title(edit.get("title"), (range_ns[1] - range_ns[0]) / 1e9)
        trim_ns = validate_trim(edit.get("trim_ns"), range_ns[1] - range_ns[0])
        story = validate_story(edit.get("story"), camera_ids, range_ns[1] - range_ns[0])
        ai_run = edit.get("ai_run")                # the AI cut run the cuts came from (ai_cut.py), or None
        if ai_run is not None and not (isinstance(ai_run, str) and re.fullmatch(r"\d{8}-\d{6}(-\d+)?", ai_run)):
            raise ProjectError("ai_run must be a run id")
        saved = self._new_edit(take_id, song_id, version, range_ns, cuts, "user")
        saved.update(look=edit.get("look"), dissolve_s=dissolve_s, branding=branding,
                     branding_colour=branding_colour.lower(), trim_ns=trim_ns, saved_at=self.clock())
        if title is not None:
            saved["title"] = title
        if story is not None:
            saved["story"] = story
        if ai_run:
            saved["ai_run"] = ai_run
        self._write_working_copy(take_id, song_id, version, saved)
        return saved
