"""What each camera shows (user, 2026-11-28, docs/ai-cut-plan.md): the camera's role, as the
input the AI cut describes the cameras with. Standard library only.

Every camera, Pi or phone, has the same three things:
  shows   one or more instruments, or "whole band"
  shot    "wide", "medium" or "close" (or None: not said)
  note    free text, at most NOTE_LONGEST characters ("from the back", "bass hardly visible")

The camera's current tags are set on the hub page. They are copied into each take at REC
(the take record's "camera_tags", hubd.py), and can be changed for one take afterwards (the
editor), without changing the camera's current ones. The instruments to pick from are a list kept here too, not in the config (user, 2026-11-28:
who comes in changes from take to take): it starts with STANDARD_INSTRUMENTS, and any new
name used in a tag (a cello brought in) joins it. Standard names help the AI cut; an
unusual one only makes its cut less good. Kept in <state_dir>/camera_tags.json:
  {"instruments": [...], "cameras": {camera id: tags}, "takes": {take id: {camera id: tags}}}
Phones are known by their id, without "-partN" (a phone that rejoined mid-take, names.py).
"""
import json
import os
import re
import threading

from names import CAMERA, TAKE, base_camera, clean

WHOLE_BAND = "whole band"
SHOTS = ("wide", "medium", "close")
NOTE_LONGEST = 200
STANDARD_INSTRUMENTS = ("keys", "piano", "organ", "synth", "guitar", "bass", "double bass", "drums", "percussion",
                        "tenor sax", "alto sax", "soprano sax", "baritone sax", "trumpet", "trombone", "flute",
                        "clarinet", "violin", "cello", "vocals")
INSTRUMENT = re.compile(r"^[a-z][a-z0-9 '&-]{0,39}$")          # "double bass", "rhodes", "kora"


def empty_tags():
    return {"shows": [], "shot": None, "note": ""}


def is_empty(tags):
    return not tags["shows"] and not tags["shot"] and not tags["note"]


class CameraTags:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path) as f:
                stored = json.load(f)
        except (OSError, ValueError):
            stored = {}
        stored = stored if isinstance(stored, dict) else {}
        self.cameras = dict(stored.get("cameras") or {})
        self.takes = {take: dict(tags) for take, tags in (stored.get("takes") or {}).items() if isinstance(tags, dict)}
        known = stored.get("instruments")
        self.instruments = [name for name in known if isinstance(name, str)] if isinstance(known, list) \
            else list(STANDARD_INSTRUMENTS)

    # -- reading

    def vocabulary(self):
        """What the page offers: the instruments so far (plus whole band) and the shot sizes."""
        with self.lock:
            return {"instruments": list(self.instruments), "whole_band": WHOLE_BAND, "shots": list(SHOTS)}

    def current(self):
        """Every camera's current tags: {camera id: tags}."""
        with self.lock:
            return {camera: dict(tags) for camera, tags in self.cameras.items()}

    def for_take(self, take_id, recorded=None):
        """{camera id: (tags, source)} for a take: its own copy where the editor changed one
        ("take"), else what was copied at REC (recorded, from the take record: "recorded"),
        else the camera's current tags ("current"). Cameras with none are left out."""
        recorded = recorded if isinstance(recorded, dict) else {}
        with self.lock:
            own = dict(self.takes.get(take_id) or {})
            current = dict(self.cameras)
        out = {}
        for source, tags_by_camera in (("current", current), ("recorded", recorded), ("take", own)):
            for camera, tags in tags_by_camera.items():
                try:
                    out[base_camera(camera)] = (self.validated(tags), source)
                except ValueError:
                    continue                   # an old record with a value the line-up dropped
        return out

    # -- changing

    def validated(self, tags):
        """The tags cleaned, or ValueError saying what is wrong."""
        if not isinstance(tags, dict):
            raise ValueError("tags must be an object")
        shows = tags.get("shows") or []
        if not isinstance(shows, list):
            raise ValueError("shows must be a list")
        picked = []
        for name in shows:
            name = clean(name).lower()
            if name != WHOLE_BAND and not INSTRUMENT.match(name):
                raise ValueError("%r isn't an instrument name (letters, numbers, spaces; at most 40)" % name)
            if name not in picked:
                picked.append(name)
        shot = tags.get("shot") or None
        if shot is not None and shot not in SHOTS:
            raise ValueError("shot must be one of %s" % ", ".join(SHOTS))
        note = clean(tags.get("note"))
        if len(note) > NOTE_LONGEST:
            raise ValueError("a note can be at most %d characters" % NOTE_LONGEST)
        return {"shows": picked, "shot": shot, "note": note}

    def set_camera(self, camera_id, tags):
        """The camera's current tags (empty ones forget it). Returns every camera's."""
        camera_id = self._camera_id(camera_id)
        tags = self.validated(tags)
        with self.lock:
            if is_empty(tags):
                self.cameras.pop(camera_id, None)
            else:
                self.cameras[camera_id] = tags
            self._learn(tags)
            self._save()
        return self.current()

    def set_for_take(self, take_id, camera_id, tags):
        """One take's tags for a camera (the editor), leaving the camera's current ones alone.
        Stored even when empty: "this camera shows nothing in particular in this take"."""
        if not isinstance(take_id, str) or not TAKE.match(take_id):
            raise ValueError("bad take id %r" % (take_id,))
        camera_id = self._camera_id(camera_id)
        tags = self.validated(tags)
        with self.lock:
            self.takes.setdefault(take_id, {})[camera_id] = tags
            self._learn(tags)
            self._save()
        return tags

    def forget_take(self, take_id):
        """A deleted take's copy goes with it."""
        with self.lock:
            if self.takes.pop(take_id, None) is not None:
                self._save()

    def _camera_id(self, camera_id):
        camera_id = base_camera(camera_id)
        if not isinstance(camera_id, str) or not CAMERA.match(camera_id):
            raise ValueError("bad camera id %r" % (camera_id,))
        return camera_id

    def add_instrument(self, name):
        """A new instrument to pick from (the "+ instrument" on the page). Returns the vocabulary."""
        name = clean(name).lower()
        if not INSTRUMENT.match(name) or name == WHOLE_BAND:
            raise ValueError("an instrument name is letters, numbers and spaces, at most 40")
        with self.lock:
            self._learn({"shows": [name]})
            self._save()
        return self.vocabulary()

    def _learn(self, tags):
        """New instrument names in tags join the list to pick from (called with the lock held)."""
        for name in tags["shows"]:
            if name != WHOLE_BAND and name not in self.instruments:
                self.instruments.append(name)

    def _save(self):
        temporary = self.path + ".tmp"
        with open(temporary, "w") as f:
            json.dump({"instruments": self.instruments, "cameras": self.cameras, "takes": self.takes}, f, indent=1)
        os.replace(temporary, self.path)


def describe(tags):
    """The tags in words for the camera table: "tenor sax, trumpet · close · from the side"."""
    parts = [", ".join(tags["shows"]) or "not said", tags["shot"] or "shot size not said"]
    if tags["note"]:
        parts.append(tags["note"])
    return " · ".join(parts)

