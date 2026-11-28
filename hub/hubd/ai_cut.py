"""The AI cut (user, 2026-11-28; docs/ai-cut-plan.md): Gemini listens to a song's bounce, is
told what each camera shows, and decides the cut. Runs only when the user presses Smart cut (the name on
screen: the user's children dislike "AI", 2026-11-28).

One run, in a background thread (one at a time on the hub):
  1. the bounce made small for upload (mono MP3; Gemini mixes to mono at 16 kbit/s anyway)
     and uploaded once (Google keeps files 48 h; the upload is reused meanwhile);
  2. listen: the bounce and the listen prompt -> the form, each instrument, the moments
     (reused from an earlier run of the song when the bounce, the model, its settings and
     the listen prompt are all the same);
  3. edit: in the same conversation, the edit prompt with the camera table -> the cuts;
  4. the hub places the cuts (ai_cut_place.py) and prices the run (ai_prices.json).

Runs are kept in <state>/ai_cut/runs/<take>/<song>/<run id>.json with everything that made
them: settings, prompts as sent, raw answers, tokens, cost, cuts, or the error. Opening a
song never runs anything; the editor puts a run's cuts into the edit, through its undo.
Settings (shared by every song): <state>/ai_cut/settings.json.
"""
import glob
import hashlib
import json
import os
import re
import subprocess
import threading
import time

import ai_cut_place
import ai_cut_prompts
import gemini

UPLOAD_KEPT_S = 46 * 3600            # Google deletes uploads after 48 h; reuse them for 46
AUDIO_TOKENS_PER_S = 32              # Google's audio tokenization
RUN_ID = re.compile(r"^\d{8}-\d{6}(-\d+)?$")
STEPS = {"audio": "preparing the bounce", "upload": "uploading the bounce", "listen": "listening",
         "edit": "editing", "place": "placing the cuts"}


class AiCut:
    def __init__(self, state_dir, client, editor, prices_path, clock=time.time, ffmpeg="ffmpeg",
                 spawn=lambda fn: threading.Thread(target=fn, daemon=True).start()):
        self.folder = os.path.join(state_dir, "ai_cut")
        self.client = client                # gemini.Gemini
        self.editor = editor                # editor_projects.EditorProjects
        self.prices_path = prices_path
        self.clock = clock
        self.ffmpeg = ffmpeg
        self.spawn = spawn
        self.lock = threading.Lock()
        self.running = None                 # the run in progress (its document), or None
        self.models_cache = (0.0, None)
        self.mark_interrupted()

    def mark_interrupted(self):
        """Runs still "running" on disk were cut off by a restart (hubd, the hub): say so."""
        for path in glob.glob(os.path.join(self.folder, "runs", "*", "*", "*.json")):
            run = read_json(path)
            if isinstance(run, dict) and run.get("status") == "running":
                run.update(status="error", step=None, step_text=None,
                           error={"kind": "hub", "message": "cut off: the hub restarted during the run"})
                write_json(path, run)

    # ---------------------------------------------------------------- settings

    def settings_path(self):
        return os.path.join(self.folder, "settings.json")

    def settings(self):
        try:
            with open(self.settings_path()) as f:
                return ai_cut_prompts.validated_settings(json.load(f))
        except (OSError, ValueError):
            return ai_cut_prompts.validated_settings({})

    def save_settings(self, settings):
        clean = ai_cut_prompts.validated_settings(settings)
        write_json(self.settings_path(), clean)
        return clean

    def settings_page(self):
        """What the settings window needs: the settings, the defaults, the fixed parts of the
        prompts, the placeholders, whether there is a key, and the price table's date."""
        prices = self.prices()
        return {"settings": self.settings(), "defaults": ai_cut_prompts.validated_settings({}),
                "fixed": {"listen": ai_cut_prompts.LISTEN_FIXED.strip(), "edit": ai_cut_prompts.EDIT_FIXED.strip()},
                # The answers' format, sent alongside each prompt (structured output), not in its text.
                "schemas": {"listen": ai_cut_prompts.LISTEN_SCHEMA,
                            "edit": ai_cut_prompts.edit_schema(["(the song's camera ids)"])},
                "placeholders": ai_cut_prompts.PLACEHOLDERS, "thinking_levels": list(ai_cut_prompts.THINKING_LEVELS),
                "has_key": self.client.has_key(), "prices_as_of": prices.get("as_of"),
                "prices_coming": prices.get("coming")}

    def models(self):
        """The key's Gemini models, each with its price if known (cached 10 minutes)."""
        at, cached = self.models_cache
        if cached is None or self.clock() - at > 600:
            cached = self.client.models()
            self.models_cache = (self.clock(), cached)
        prices = self.prices().get("models") or {}
        return [dict(model, price=prices.get(model["id"])) for model in cached]

    def prices(self):
        try:
            with open(self.prices_path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    # ---------------------------------------------------------------- one song's inputs

    def inputs(self, take_id, song_id, version):
        """Everything a run of this song needs, from the editor's project of it."""
        project = self.editor.project(take_id, song_id, version)
        bounce_path, beats = self.editor.bounce_files(take_id, song_id, version)
        settings = self.settings()
        duration_s = project["bounce"]["duration_s"]
        values = {
            "song_name": project["song"]["name"],
            "take_name": project.get("take_name") or take_id,
            "song_length": "%d:%02d" % divmod(int(round(duration_s)), 60),
            # The video is where the bounce and the cameras overlap (the song's range).
            "video_start": ai_cut_prompts.clock((project["range_ns"][0] - project["bounce"]["start_ns"]) / 1e9),
            "video_end": ai_cut_prompts.clock((project["range_ns"][1] - project["bounce"]["start_ns"]) / 1e9),
            "bars": ai_cut_prompts.bar_list((beats or {}).get("downbeats_s") or []),
            "cameras": ai_cut_prompts.camera_table(project["cameras"]),
        }
        return {"project": project, "bounce_path": bounce_path, "beats": beats, "settings": settings,
                "prompts": ai_cut_prompts.prompts(settings, values), "duration_s": duration_s}

    def preview(self, take_id, song_id, version):
        """The prompts as they would be sent now, and what the run would cost, about."""
        found = self.inputs(take_id, song_id, version)
        earlier = self.reusable_listening(take_id, song_id, found)
        return {"prompts": found["prompts"], "estimate": self.estimate(found), "model": found["settings"]["model"],
                "configs": self.configs(found), "reuses_listening": earlier is not None,
                "reusable_from": earlier["id"] if earlier else None}

    def configs(self, found):
        """What goes to Gemini with each prompt besides its text: the generationConfig, with the
        answer's JSON schema (structured output) and the thinking and length settings."""
        camera_ids = [camera["id"] for camera in found["project"]["cameras"]]
        return {"listen": ai_cut_prompts.generation_config(found["settings"], ai_cut_prompts.LISTEN_SCHEMA),
                "edit": ai_cut_prompts.generation_config(found["settings"], ai_cut_prompts.edit_schema(camera_ids))}

    def audio_for(self, take_id, song_id, version):
        """The bounce exactly as it goes to Gemini (mono MP3), made now if needed: its path."""
        bounce_path, _ = self.editor.bounce_files(take_id, song_id, version)
        return self.small_bounce(bounce_path)

    def run_audio(self, take_id, song_id, run_id):
        """The file a run sent to Gemini: its path (kept until the bounce changes)."""
        run = self.run_document(take_id, song_id, run_id)
        path = os.path.join(self.folder, "audio", run.get("audio_file") or "")
        if not run.get("audio_file") or not os.path.exists(path):
            raise ValueError("this run's audio file isn't kept any more")
        return path

    def estimate(self, found):
        """About what a run costs: the audio twice (both calls carry it), the prompts' text
        (~4 characters a token), and ~8000 tokens of answer and thinking per call."""
        audio = int(found["duration_s"] * AUDIO_TOKENS_PER_S)
        text = sum(len(p) for p in found["prompts"].values()) // 4
        usage = [{"audio": audio, "text": len(found["prompts"]["listen"]) // 4, "output": 8000},
                 {"audio": audio, "text": text + 6000, "output": 8000}]          # + the listening answer
        price = (self.prices().get("models") or {}).get(found["settings"]["model"])
        costs = [cost_of(price, u["text"], u["audio"], u["output"]) for u in usage]
        return {"input_tokens": sum(u["audio"] + u["text"] for u in usage), "output_tokens": 16000,
                "cost_usd": None if None in costs else round(sum(costs), 4)}

    # ---------------------------------------------------------------- runs

    def runs_dir(self, take_id, song_id):
        return os.path.join(self.folder, "runs", take_id, song_id)

    def runs(self, take_id, song_id):
        """The song's runs, newest first, as summaries (no prompts or raw answers)."""
        folder = self.runs_dir(take_id, song_id)
        try:
            names = sorted((n for n in os.listdir(folder) if n.endswith(".json")), reverse=True)
        except OSError:
            names = []
        with self.lock:
            running = self.running
        out = []
        for name in names:
            run = read_json(os.path.join(folder, name))
            if isinstance(run, dict):
                live = running if running and running["id"] == run.get("id") else run    # its step as it is now
                out.append(summary(live))
        return out

    def run_document(self, take_id, song_id, run_id):
        if not RUN_ID.match(run_id or ""):
            raise ValueError("bad run id")
        with self.lock:
            running = self.running
        if running and running["id"] == run_id:
            return dict(running)
        run = read_json(os.path.join(self.runs_dir(take_id, song_id), run_id + ".json"))
        if not isinstance(run, dict):
            raise ValueError("no such run")
        return run

    def start(self, take_id, song_id, version, listen_again=False):
        """Start a run of this song in the background; returns its summary at once.
        listen_again: listen even when an earlier run's listening could be reused (the same
        model and settings can hear a song differently from run to run)."""
        found = self.inputs(take_id, song_id, version)        # fails early: not synced, no drive, ...
        if not self.client.has_key():
            raise gemini.GeminiError("no_key")
        with self.lock:
            if self.running:
                raise ValueError("a smart cut is already running (%s, %s)" % (self.running["take"], self.running["song"]))
            run_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(self.clock()))
            while os.path.exists(os.path.join(self.runs_dir(take_id, song_id), run_id + ".json")):
                run_id += "-2"
            run = {"id": run_id, "take": take_id, "song": song_id, "version": version, "status": "running",
                   "step": "audio", "step_text": STEPS["audio"], "started_at": self.clock(),
                   "settings": found["settings"], "prompts": found["prompts"], "estimate": self.estimate(found),
                   "cost_usd": 0.0, "listen_again": bool(listen_again)}
            self.running = run
        self.save_run(run)                  # on disk from the start, so a restart can't lose it
        self.spawn(lambda: self._run(run, found))
        return summary(run)

    def _run(self, run, found):
        try:
            self._steps(run, found)
            run.update(status="done")
        except gemini.GeminiError as error:
            run.update(status="error", error={"kind": error.kind, "message": str(error)})
            if getattr(error, "call", None):
                run["failed_call"] = error.call          # the raw answer, to see what went wrong
        except Exception as error:                       # noqa: BLE001 (the run says what went wrong)
            run.update(status="error", error={"kind": "hub", "message": "the hub failed: %s" % error})
        finally:
            run.update(finished_at=self.clock(), step=None, step_text=None)
            self.save_run(run)
            with self.lock:
                self.running = None

    def save_run(self, run):
        write_json(os.path.join(self.runs_dir(run["take"], run["song"]), run["id"] + ".json"), run)

    def _step(self, run, step):
        run.update(step=step, step_text=STEPS[step])

    def _steps(self, run, found):
        settings, project = found["settings"], found["project"]
        price = (self.prices().get("models") or {}).get(settings["model"])
        self._step(run, "audio")
        small = self.small_bounce(found["bounce_path"])
        run["audio_file"] = os.path.basename(small)
        self._step(run, "upload")
        uri = self.uploaded(small)
        listen_turn = {"role": "user", "parts": [{"file_data": {"mime_type": "audio/mpeg", "file_uri": uri}},
                                                 {"text": run["prompts"]["listen"]}]}
        earlier = None if run["listen_again"] else self.reusable_listening(run["take"], run["song"], found)
        if earlier:
            run["listening"] = dict(earlier["listening"], reused_from=earlier["id"], cost_usd=0.0)
        else:
            self._step(run, "listen")
            run["listening"] = self._call(settings, [listen_turn], ai_cut_prompts.LISTEN_SCHEMA, price)
        run["listen_key"] = listening_key(found)
        run["cost_usd"] = round(run["listening"]["cost_usd"] or 0.0, 6)
        self._step(run, "edit")
        camera_ids = [camera["id"] for camera in project["cameras"]]
        conversation = [listen_turn, {"role": "model", "parts": [{"text": run["listening"]["text"]}]},
                        {"role": "user", "parts": [{"text": run["prompts"]["edit"]}]}]
        run["editing"] = self._call(settings, conversation, ai_cut_prompts.edit_schema(camera_ids), price)
        known = [c["cost_usd"] for c in (run["listening"], run["editing"])]
        run["cost_usd"] = None if None in known else round(sum(known), 6)
        self._step(run, "place")
        sync = project["bounce"]
        to_song = ai_cut_place.bounce_to_song(sync["start_ns"], (sync.get("sync") or {}).get("drift_ppm"),
                                              project["range_ns"][0])
        cuts, left_out = ai_cut_place.place_cuts(run["editing"]["answer"].get("cuts") or [], project["cameras"],
                                                 project["range_ns"], to_song, found["beats"])
        if not cuts:
            raise gemini.GeminiError("answer", "no usable cuts in the answer")
        run.update(cuts=cuts, left_out=left_out, range_ns=project["range_ns"],
                   bounce_start_ns=sync["start_ns"], drift_ppm=(sync.get("sync") or {}).get("drift_ppm"),
                   summary=run["editing"]["answer"].get("summary"))

    def _call(self, settings, contents, schema, price):
        started = time.monotonic()
        config = ai_cut_prompts.generation_config(settings, schema)
        text, usage, finish, version = self.client.generate(settings["model"], contents, config)
        tokens = token_counts(usage)
        call = {"text": text, "config": config, "usage": usage, "tokens": tokens, "finish": finish, "model_version": version,
                "seconds": round(time.monotonic() - started, 1),
                "cost_usd": cost_of(price, tokens["text"], tokens["audio"], tokens["output"])}
        try:
            call["answer"] = json.loads(text)
        except ValueError:
            call["answer"] = None
        if not isinstance(call["answer"], dict):
            detail = "cut short at the longest answer" if finish == "MAX_TOKENS" else "not valid JSON (%s)" % finish
            error = gemini.GeminiError("answer", detail)
            error.call = call
            raise error
        return call

    def reusable_listening(self, take_id, song_id, found):
        """An earlier finished run of this song whose listening can be reused, or None."""
        key = listening_key(found)
        for entry in self.runs(take_id, song_id):
            if entry["status"] != "done":
                continue
            run = read_json(os.path.join(self.runs_dir(take_id, song_id), entry["id"] + ".json"))
            if isinstance(run, dict) and run.get("listen_key") == key and run.get("listening"):
                return run
        return None

    # ---------------------------------------------------------------- the bounce for Gemini

    def small_bounce(self, bounce_path):
        """The bounce as mono MP3 (64 kbit/s, 22 kHz) for the upload, made once per bounce file."""
        stat = os.stat(bounce_path)
        name = hashlib.sha1(("%s|%d|%d" % (bounce_path, stat.st_size, stat.st_mtime)).encode()).hexdigest()[:16]
        out = os.path.join(self.folder, "audio", name + ".mp3")
        if not os.path.exists(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
            temporary = out + ".tmp.mp3"
            subprocess.run([self.ffmpeg, "-nostdin", "-v", "error", "-y", "-i", bounce_path, "-vn", "-ac", "1",
                            "-ar", "22050", "-b:a", "64k", temporary], check=True, timeout=600)
            os.replace(temporary, out)
        return out

    def uploaded(self, small_path):
        """The file's uri at Google: uploaded now, or the earlier upload while Google still keeps it."""
        record_path = small_path + ".upload.json"
        record = read_json(record_path)
        if isinstance(record, dict) and self.clock() - record.get("at", 0) < UPLOAD_KEPT_S and record.get("uri"):
            return record["uri"]
        uri = self.client.upload(small_path, "audio/mpeg", os.path.basename(small_path))
        write_json(record_path, {"uri": uri, "at": self.clock()})
        return uri


# ---------------------------------------------------------------- helpers

def listening_key(found):
    """What the listening depends on: the bounce file, the model and its settings, the prompt."""
    settings = found["settings"]
    stat = os.stat(found["bounce_path"])
    parts = [found["bounce_path"], stat.st_size, int(stat.st_mtime), settings["model"], settings["thinking"],
             settings["temperature"], settings["max_output_tokens"], found["prompts"]["listen"]]
    return hashlib.sha1(json.dumps(parts).encode()).hexdigest()


def token_counts(usage):
    """{"text", "audio", "output"} tokens from Gemini's usageMetadata (output includes thinking)."""
    details = {d.get("modality"): d.get("tokenCount", 0) for d in usage.get("promptTokensDetails") or []}
    audio = details.get("AUDIO", 0)
    prompt = usage.get("promptTokenCount", 0)
    return {"text": max(0, prompt - audio), "audio": audio,
            "output": usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)}


def cost_of(price, text_tokens, audio_tokens, output_tokens):
    """US dollars for one call with ai_prices.json's entry for the model, or None if not known."""
    if not price:
        return None
    if price.get("above_tokens") and text_tokens + audio_tokens > price["above_tokens"]:
        price = dict(price, **price["above"])
    return round((text_tokens * price["input"] + audio_tokens * price.get("audio", price["input"])
                  + output_tokens * price["output"]) / 1e6, 6)


def summary(run):
    """A run for the list: no prompts or raw answers."""
    out = {key: run.get(key) for key in ("id", "take", "song", "version", "status", "step", "step_text",
                                         "started_at", "finished_at", "cost_usd", "estimate", "error", "summary")}
    out["model"] = (run.get("settings") or {}).get("model")
    out["thinking"] = (run.get("settings") or {}).get("thinking")
    out["cuts"] = len(run.get("cuts") or [])
    out["reused_listening"] = bool((run.get("listening") or {}).get("reused_from"))
    return out


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path, document):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w") as f:
        json.dump(document, f, indent=1)
    os.replace(temporary, path)
