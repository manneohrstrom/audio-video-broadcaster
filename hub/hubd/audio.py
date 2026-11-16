"""Bounce uploads: songs and versions per take (Phase 4 Track A, docs/phase4-plan.md).

A take can have several songs; each song several versions (mixes). Every version
is kept; the newest is the default unless the user picks another.

Uploads land on the hub's SD card first, under <audio_dir>/<take>/, in the same
layout they get on the drive (STUDIO/takes/<take folder>/audio/):

    songs.json                      songs, their versions and the chosen one
    <song>/v<N>/<file as sent>      the upload (.part until finished)
    <song>/v<N>/version.json        name, size, sha256, duration, format
    scratch/...                     the hub mic recording (scratch.py)

hub-ingest copies every finished file to the drive (never .part files) like video:
*.part, fsync, checksum, rename; it never deletes from the drive. Staging on the hub
means uploading works without the drive plugged in, and the hub stays the only
thing that mounts it.

Upload protocol (resumable; the page sends 8 MB pieces):
    POST /api/audio/<take>/songs           {"name"}            -> new song
    POST /api/audio/<take>/<song>/versions {"filename","bytes"} -> {"version": N}
    PUT  /api/audio/<take>/<song>/v<N>?offset=X   (body: bytes at X; X must equal what
                                                   the hub already has)
    POST /api/audio/<take>/<song>/v<N>/finish      -> checks size, sha256, ffprobe
    GET  /api/audio/<take>                         -> songs, versions, bytes received
    POST /api/audio/<take>/<song>/choose  {"version"}
Pieces are refused (409) while a take or live session runs; the page waits and goes on.
"""
import hashlib
import json
import os
import re
import subprocess
import threading
import time

TAKE = re.compile(r"^\d{8}-\d{6}$")
SONG = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
AUDIO_EXT = (".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".ogg")
MAX_BYTES = 8 << 30          # 8 GB: hours of 24-bit multichannel
MAX_PIECE = 16 << 20


class AudioError(ValueError):
    pass


class Busy(Exception):
    pass


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "track"


def safe_filename(name):
    base = os.path.basename(name or "")
    stem, ext = os.path.splitext(base)
    if ext.lower() not in AUDIO_EXT:
        raise AudioError("not an audio file (%s): use %s" % (ext or "no extension", ", ".join(AUDIO_EXT)))
    stem = re.sub(r"[^A-Za-z0-9 ._()-]+", "_", stem).strip(" .") or "bounce"
    return stem[:80] + ext.lower()


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def probe(path, ffprobe="ffprobe"):
    """Duration, sample rate, channels and codec, or AudioError if it isn't audio."""
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "stream=codec_name,sample_rate,channels:format=duration", "-of", "json", path],
                           capture_output=True, text=True, timeout=60)
        d = json.loads(r.stdout or "{}")
        s = (d.get("streams") or [{}])[0]
        dur = float((d.get("format") or {}).get("duration") or 0)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        raise AudioError("can't read the file: %s" % e) from None
    if r.returncode or not s.get("codec_name") or dur <= 0:
        raise AudioError("not a readable audio file")
    return {"duration_s": round(dur, 3), "sample_rate": int(s.get("sample_rate") or 0),
            "channels": int(s.get("channels") or 0), "codec": s["codec_name"]}


class Audio:
    def __init__(self, root, takes, busy, clock=time.time, ffprobe="ffprobe", free_bytes=None):
        """takes(): take ids that exist; busy(): True while a take or live session runs."""
        self.root, self.takes, self.busy, self.clock, self.ffprobe = root, takes, busy, clock, ffprobe
        self.free_bytes = free_bytes or (lambda: os.statvfs(root).f_bavail * os.statvfs(root).f_frsize)
        self.lock = threading.Lock()
        os.makedirs(root, exist_ok=True)

    # -- paths and the song list
    def take_dir(self, take):
        if not TAKE.match(take or ""):
            raise AudioError("bad take id")
        if take not in self.takes():
            raise AudioError("no take %s" % take)
        return os.path.join(self.root, take)

    def _songs_path(self, take):
        return os.path.join(self.take_dir(take), "songs.json")

    def _load(self, take):
        try:
            with open(self._songs_path(take)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"take_id": take, "songs": []}

    def _save(self, take, doc):
        os.makedirs(self.take_dir(take), exist_ok=True)
        write_json(self._songs_path(take), doc)

    def _song(self, doc, song):
        for s in doc["songs"]:
            if s["id"] == song:
                return s
        raise AudioError("no song %s" % song)

    def _vdir(self, take, song, v):
        if not SONG.match(song or "") or not isinstance(v, int) or v < 1:
            raise AudioError("bad song or version")
        return os.path.join(self.take_dir(take), song, "v%d" % v)

    # -- API
    def list(self, take):
        """Songs with their versions, each with what the hub has received so far."""
        doc = self._load(take)
        for s in doc["songs"]:
            for v in s["versions"]:
                d = self._vdir(take, s["id"], v["version"])
                if not v.get("complete"):
                    try:
                        v["received"] = os.path.getsize(os.path.join(d, v["filename"] + ".part"))
                    except OSError:
                        v["received"] = 0
            done = [v["version"] for v in s["versions"] if v.get("complete")]
            s["current"] = s.get("chosen") if s.get("chosen") in done else (max(done) if done else None)
        return doc

    def new_song(self, take, name=None):
        with self.lock:
            doc = self._load(take)
            name = (name or "").strip()[:60] or "Track %d" % (len(doc["songs"]) + 1)
            if any(s["name"].lower() == name.lower() for s in doc["songs"]):
                raise AudioError("there is already a song called %r" % name)
            base, n, ids = slug(name), 1, {s["id"] for s in doc["songs"]}
            sid = base
            while sid in ids:
                n += 1
                sid = "%s-%d" % (base, n)
            s = {"id": sid, "name": name, "created": self.clock(), "versions": [], "chosen": None}
            doc["songs"].append(s)
            self._save(take, doc)
            return s

    def new_version(self, take, song, filename, size):
        if not isinstance(size, int) or not 0 < size <= MAX_BYTES:
            raise AudioError("file size missing or over %d GB" % (MAX_BYTES >> 30))
        fn = safe_filename(filename)
        if size > self.free_bytes() - (15 << 30):
            raise AudioError("not enough space on the hub for %.1f GB" % (size / 1e9))
        with self.lock:
            doc = self._load(take)
            s = self._song(doc, song)
            # The same file again, unfinished: resume it instead of starting a new version.
            for v in s["versions"]:
                if not v.get("complete") and v["filename"] == fn and v["bytes"] == size:
                    return v
            n = max([v["version"] for v in s["versions"]] or [0]) + 1
            v = {"version": n, "filename": fn, "original_name": os.path.basename(filename or "")[:200],
                 "bytes": size, "started": self.clock(), "complete": False}
            os.makedirs(self._vdir(take, song, n), exist_ok=True)
            s["versions"].append(v)
            self._save(take, doc)
            return v

    def _version(self, doc, song, version):
        for v in self._song(doc, song)["versions"]:
            if v["version"] == version:
                return v
        raise AudioError("no version %d" % version)

    def put(self, take, song, version, offset, read, length):
        if self.busy():
            raise Busy("a take or live session is running: upload waits")
        if not 0 < length <= MAX_PIECE:
            raise AudioError("piece size")
        doc = self._load(take)
        v = self._version(doc, song, version)
        if v.get("complete"):
            return {"received": v["bytes"], "complete": True}
        part = os.path.join(self._vdir(take, song, version), v["filename"] + ".part")
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if offset != have:
            raise AudioError("offset %d, but the hub has %d bytes: resume from there" % (offset, have))
        if have + length > v["bytes"]:
            raise AudioError("more bytes than announced")
        with open(part, "ab") as f:
            left = length
            while left:
                b = read(min(1 << 20, left))
                if not b:
                    f.truncate(have)               # never keep half a piece
                    raise AudioError("short upload")
                f.write(b)
                left -= len(b)
        return {"received": have + length}

    def finish(self, take, song, version):
        d = self._vdir(take, song, version)
        with self.lock:
            doc = self._load(take)
            v = self._version(doc, song, version)
            if v.get("complete"):
                return v
            part = os.path.join(d, v["filename"] + ".part")
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have != v["bytes"]:
                raise AudioError("only %d of %d bytes received" % (have, v["bytes"]))
        info = probe(part, self.ffprobe)          # outside the lock: can take a while
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for b in iter(lambda: f.read(1 << 22), b""):
                h.update(b)
        with self.lock:
            doc = self._load(take)
            v = self._version(doc, song, version)
            v.update(info, sha256=h.hexdigest(), complete=True, finished=self.clock())
            write_json(os.path.join(d, "version.json"), dict(v, take_id=take, song=song))
            os.replace(part, os.path.join(d, v["filename"]))
            self._save(take, doc)
            return v

    def choose(self, take, song, version):
        with self.lock:
            doc = self._load(take)
            s = self._song(doc, song)
            if version is not None and not self._version(doc, song, version).get("complete"):
                raise AudioError("version %d isn't finished uploading" % version)
            s["chosen"] = version
            self._save(take, doc)
            return s

    def summary(self, take, on_drive=()):
        """Short form for the take list. on_drive: relative paths hub-ingest has copied."""
        out = {}
        if os.path.exists(os.path.join(self.root, take, "songs.json")):
            try:
                doc = self.list(take)
            except AudioError:
                doc = {"songs": []}
            out["songs"] = [{"id": s["id"], "name": s["name"], "current": s["current"], "versions": [
                {"version": v["version"], "filename": v["filename"], "complete": bool(v.get("complete")),
                 "bytes": v["bytes"], "received": v.get("received", v["bytes"]), "duration_s": v.get("duration_s"),
                 "on_drive": "%s/v%d/%s" % (s["id"], v["version"], v["filename"]) in on_drive}
                for v in s["versions"]]} for s in doc["songs"]]
        try:
            with open(os.path.join(self.root, take, "scratch", "scratch.json")) as f:
                m = json.load(f)
            out["scratch"] = {"seconds": round((m.get("samples") or 0) / (m.get("rate") or 48000), 1),
                              "problems": m.get("problems") or [], "on_drive": "scratch/scratch.wav" in on_drive}
        except (OSError, ValueError):
            pass
        return out
