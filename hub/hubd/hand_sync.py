"""Sync by hand (docs/editor-plan.md section 4b): the user places the bounce on the take.

The automatic sync (hub/ingest/audiosync.py) writes sync.json, or sync_failed.json when it
isn't sure. For any bounce version the user can also place the bounce themselves in the
editor's Sync mode. Saving writes sync.json with anchor "by hand", in the same format as the
automatic one, so everything that reads sync.json (the editor, "Make video", the render)
works unchanged. The automatic result it replaces is kept next to it as sync-auto.json.

Files in the bounce version's folder on the hub (<audio_dir>/<take>/<song>/v<N>/):
    sync.json          where the bounce sits on the hub clock (automatic or by hand)
    sync-auto.json     the automatic result, kept when a by-hand sync replaced it
    sync_failed.json   why the automatic sync wasn't sure (may hold its best guess)
"""
import json
import os

NS_PER_SECOND = 1_000_000_000
BY_HAND = "by hand"
MIC_LATENCY_S = 0.074       # as audiosync.DEFAULTS: the hub mic hears ~74 ms after the cameras see
MAX_DRIFT_PPM = 300         # as audiosync.DEFAULTS: more than this was a wrong match


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, document):
    temporary = path + ".tmp"
    with open(temporary, "w") as f:
        json.dump(document, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


def mic_start_ns(take_audio_dir):
    """Hub time of the hub mic recording's first block (scratch.times.tsv, first line), or None."""
    try:
        with open(os.path.join(take_audio_dir, "scratch", "scratch.times.tsv")) as f:
            return int(f.readline().split()[1])
    except (OSError, ValueError, IndexError):
        return None


def best_guess_ns(failed, take_audio_dir):
    """Where the failed automatic sync's whole-bounce match put the bounce, or None.
    Its offset is in seconds from the mic's first block, before the mic's latency."""
    try:
        offset_s = float(failed["details"]["whole"]["offset_s"])
    except (TypeError, KeyError, ValueError):
        return None
    start_ns = mic_start_ns(take_audio_dir)
    if start_ns is None:
        return None
    return start_ns + int(round((offset_s - MIC_LATENCY_S) * NS_PER_SECOND))


def starting_point(version_dir, take_audio_dir, take_start_ns):
    """Where Sync mode puts the bounce when it opens, and what that is:
    {"bounce_start_ns", "source": "by hand"|"automatic"|"best guess"|"take start",
     "anchor", "confidence", "automatic_start_ns" (the automatic result, if there is one)}."""
    current = read_json(os.path.join(version_dir, "sync.json"))
    kept = read_json(os.path.join(version_dir, "sync-auto.json"))
    automatic = kept if current and current.get("anchor") == BY_HAND else current
    automatic_start_ns = int(automatic["bounce_start_ns"]) if automatic and "bounce_start_ns" in automatic else None
    if current and "bounce_start_ns" in current:
        return {"bounce_start_ns": int(current["bounce_start_ns"]),
                "source": BY_HAND if current.get("anchor") == BY_HAND else "automatic",
                "anchor": current.get("anchor"), "confidence": current.get("confidence"),
                "automatic_start_ns": automatic_start_ns}
    failed = read_json(os.path.join(version_dir, "sync_failed.json"))
    guess = best_guess_ns(failed, take_audio_dir)
    if guess is not None:
        return {"bounce_start_ns": guess, "source": "best guess", "anchor": None, "confidence": None,
                "reason": failed.get("reason"), "automatic_start_ns": None}
    return {"bounce_start_ns": int(take_start_ns), "source": "take start", "anchor": None, "confidence": None,
            "reason": (failed or {}).get("reason"), "automatic_start_ns": None}


def save(version_dir, bounce_start_ns, clock):
    """Write the by-hand sync.json, keeping an automatic result as sync-auto.json.
    The drift (the bounce's clock against the hub's) comes from the automatic match when
    there is one; a hand placement only sets the offset."""
    current = read_json(os.path.join(version_dir, "sync.json"))
    if current and current.get("anchor") != BY_HAND:
        write_json(os.path.join(version_dir, "sync-auto.json"), current)
    automatic = read_json(os.path.join(version_dir, "sync-auto.json")) or {}
    drift_ppm = automatic.get("drift_ppm") or 0
    if not isinstance(drift_ppm, (int, float)) or abs(drift_ppm) > MAX_DRIFT_PPM:
        drift_ppm = 0
    document = {"bounce_start_ns": int(bounce_start_ns), "drift_ppm": drift_ppm, "anchor": BY_HAND,
                "confidence": None, "set_at": clock()}
    if "bounce_start_ns" in automatic:
        document["moved_from_automatic_s"] = round((int(bounce_start_ns) - int(automatic["bounce_start_ns"]))
                                                   / NS_PER_SECOND, 4)
    write_json(os.path.join(version_dir, "sync.json"), document)
    return document
