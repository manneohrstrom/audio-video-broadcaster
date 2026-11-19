"""Names the user gives cameras and takes on the hub page (user, 2026-11-19): "Drum kit" for
cam2, "Sunday jam" for take 20261119-140512. Standard library only.

The id stays what everything else uses (files, folders, routes) and is shown next to the name,
small and in brackets. An empty name goes back to the default: the id, or a phone's own label.
Kept in <state_dir>/names.json: {"cameras": {id: name}, "takes": {take_id: name}}.
"""
import json
import os
import re
import threading

CAMERA = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,40}[a-z0-9])?$")    # camN or a phone's id
TAKE = re.compile(r"^\d{8}-\d{6}$")
KINDS = {"cameras": (CAMERA, 32), "takes": (TAKE, 80)}            # id pattern, longest name


def clean(name):
    """One line, single spaces, no control characters."""
    return " ".join("".join(c for c in str(name or "") if c.isprintable() or c.isspace()).split())


def base_camera(camera_id):
    """A phone that rejoined mid-take records "pauls-pixel-part2": it's still pauls-pixel."""
    return re.sub(r"-part\d{1,2}$", "", camera_id or "")


class Names:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            with open(path) as f:
                stored = json.load(f)
        except (OSError, ValueError):
            stored = {}
        self.names = {kind: dict(stored.get(kind) or {}) if isinstance(stored, dict) else {} for kind in KINDS}

    def all(self):
        with self.lock:
            return {kind: dict(names) for kind, names in self.names.items()}

    def camera(self, camera_id):
        """The camera's name, or None (then the page shows its id or label)."""
        with self.lock:
            return self.names["cameras"].get(base_camera(camera_id))

    def take(self, take_id):
        with self.lock:
            return self.names["takes"].get(take_id)

    def set(self, kind, key, name):
        """Name a camera or take; an empty name forgets it. Returns every name."""
        if kind not in KINDS:
            raise ValueError("kind must be cameras or takes")
        pattern, longest = KINDS[kind]
        key = base_camera(key) if kind == "cameras" else key
        if not isinstance(key, str) or not pattern.match(key):
            raise ValueError("bad %s id %r" % (kind[:-1], key))
        name = clean(name)
        if len(name) > longest:
            raise ValueError("a name can be at most %d characters" % longest)
        with self.lock:
            if name and name != key:
                self.names[kind][key] = name
            else:
                self.names[kind].pop(key, None)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.names, f, indent=1)
            os.replace(tmp, self.path)
        return self.all()
