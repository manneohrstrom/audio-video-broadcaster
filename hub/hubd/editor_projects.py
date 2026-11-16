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

Where edits live: the working copy is saved on the hub's SD card while the user edits
(so the STUDIO drive doesn't have to be mounted read-write). Copying it to the drive comes
with the render. When there is no working copy yet, the editor starts from the quick mix's
cut if one was rendered, otherwise from the sync server camera alone.
"""
import array
import json
import os
import re
import subprocess
import time

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
                      "by": cut.get("by") if cut.get("by") in ("auto", "user") else "user"})
    clean[0]["at_ns"] = 0
    return clean


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


class EditorProjects:
    """Builds editor projects and saves edits.

    audio: hubd's Audio (songs, versions, the hub-side audio folder).
    read_take_document(take_id): the take's take.json from the STUDIO drive (dict).
    find_quick_edit(take_id, song_id, version): the newest quick-mix edit for it, or None.
    edits_dir: where working copies are saved on the hub.
    thumbs_dir: strip thumbnails made by hub-ingest (hub/ingest/thumbnails.py).
    request_thumbnails(take_id): ask hub-ingest to make missing thumbnails now.
    waveforms_dir: cached waveforms of bounces.
    """

    def __init__(self, audio, read_take_document, find_quick_edit, edits_dir, thumbs_dir=None,
                 request_thumbnails=None, waveforms_dir=None, clock=time.time, ffmpeg="ffmpeg"):
        self.audio = audio
        self.read_take_document = read_take_document
        self.find_quick_edit = find_quick_edit
        self.edits_dir = edits_dir
        self.thumbs_dir = thumbs_dir or os.path.join(edits_dir, "..", "thumbs")
        self.request_thumbnails = request_thumbnails or (lambda take_id: None)
        self.waveforms_dir = waveforms_dir or os.path.join(edits_dir, "..", "waveforms")
        self.clock = clock
        self.ffmpeg = ffmpeg

    # -- paths
    def _check_ids(self, take_id, song_id, version):
        if not TAKE_ID_PATTERN.match(take_id or "") or not SONG_ID_PATTERN.match(song_id or ""):
            raise ProjectError("bad take or song")
        if not isinstance(version, int) or version < 1:
            raise ProjectError("bad version")

    def working_copy_path(self, take_id, song_id, version):
        self._check_ids(take_id, song_id, version)
        return os.path.join(self.edits_dir, take_id, song_id, "v%d.json" % version)

    # -- the project
    def _song_and_version(self, take_id, song_id, version):
        songs = self.audio.list(take_id)["songs"]
        song = next((s for s in songs if s["id"] == song_id), None)
        if song is None:
            raise ProjectError("no song %r in take %s" % (song_id, take_id))
        version_info = next((v for v in song["versions"] if v["version"] == version), None)
        if version_info is None or not version_info.get("complete"):
            raise ProjectError("version %d isn't uploaded" % version)
        sync_path = os.path.join(self.audio.take_dir(take_id), song_id, "v%d" % version, "sync.json")
        try:
            with open(sync_path) as f:
                sync = json.load(f)
        except (OSError, ValueError):
            raise ProjectError("this song isn't synced to the take yet") from None
        return song, version_info, sync

    def project(self, take_id, song_id, version):
        """Everything the editor page needs for one song version."""
        self._check_ids(take_id, song_id, version)
        song, version_info, sync = self._song_and_version(take_id, song_id, version)
        cameras = describe_cameras(self.read_take_document(take_id))
        bounce_start_ns = int(sync["bounce_start_ns"])
        range_ns = song_range_ns(bounce_start_ns, version_info["duration_s"], cameras)
        edit, source = self._load_edit(take_id, song_id, version, cameras, range_ns)
        missing_thumbnails = False
        for camera in cameras:
            camera["file_url"] = "/media/%s/%s.mov" % (take_id, camera["id"])
            camera["thumbnails_url"] = "/media-thumbs/%s/%s/" % (take_id, camera["id"])
            camera["thumbnails_ready"] = os.path.exists(
                os.path.join(self.thumbs_dir, take_id, camera["id"], "index.json"))
            missing_thumbnails = missing_thumbnails or not camera["thumbnails_ready"]
        if missing_thumbnails:
            self.request_thumbnails(take_id)
        return {
            "take_id": take_id,
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
                "sync": {key: sync.get(key) for key in ("anchor", "confidence", "drift_ppm")},
            },
            "edit": edit,
            "edit_source": source,
        }

    def _load_edit(self, take_id, song_id, version, cameras, range_ns):
        """(edit, where it came from): the working copy, else the quick mix's cut, else one shot."""
        camera_ids = {camera["id"] for camera in cameras}
        length_ns = range_ns[1] - range_ns[0]
        try:
            with open(self.working_copy_path(take_id, song_id, version)) as f:
                saved = json.load(f)
            cuts = rebase_cuts(saved["cuts"], saved["range_ns"], range_ns)
            if cuts:
                saved.update(cuts=validate_cuts(cuts, camera_ids, length_ns), range_ns=range_ns)
                return saved, "saved"
        except (OSError, ValueError, KeyError, TypeError):
            pass
        quick = self.find_quick_edit(take_id, song_id, version)
        if quick and quick.get("range_ns") and quick.get("cuts"):
            cuts = rebase_cuts([dict(cut, by="auto") for cut in quick["cuts"]], quick["range_ns"], range_ns)
            try:
                if cuts:
                    cuts = validate_cuts(cuts, camera_ids, length_ns)
                    return self._new_edit(take_id, song_id, version, range_ns, cuts, "quick-v1"), "quick mix"
            except ProjectError:
                pass
        server = next((c["id"] for c in cameras if c["is_sync_server"]), cameras[0]["id"])
        cuts = [{"at_ns": 0, "camera": server, "by": "auto"}]
        return self._new_edit(take_id, song_id, version, range_ns, cuts, "start"), "new"

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
        _, version_info, _ = self._song_and_version(take_id, song_id, version)
        source = os.path.join(self.audio.take_dir(take_id), song_id, "v%d" % version, version_info["filename"])
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
        saved = self._new_edit(take_id, song_id, version, range_ns, cuts, "user")
        saved.update(look=edit.get("look"), dissolve_s=dissolve_s, saved_at=self.clock())
        path = self.working_copy_path(take_id, song_id, version)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + ".tmp"
        with open(temporary, "w") as f:
            json.dump(saved, f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        return saved
