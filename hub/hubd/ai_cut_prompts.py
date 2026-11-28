"""The AI cut's prompts and settings (docs/ai-cut-plan.md). Standard library only.

Each of the two prompts (listen, edit) is the user's creative direction (in the settings)
followed by a fixed part owned here: what data comes with it and what the answer must hold.
Both may use {{placeholders}}, filled from the song (fill()). The answer's shape is also
enforced by Gemini's structured output (LISTEN_SCHEMA, edit_schema()).

The defaults are the prompts of the user's manual test in AI Studio (2026-11-28).
"""
import copy
import re

PLACEHOLDERS = {
    "song_name": "the song's name",
    "take_name": "the take's name (or its id)",
    "song_length": "the bounce's length, m:ss",
    "video_start": "where in the recording the video starts, m:ss.s",
    "video_end": "where in the recording the video ends, m:ss.s",
    "bars": "where each bar starts, from the beat tracker",
    "cameras": "the camera table: id, name, what it shows, shot, mount, note",
}
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

DEFAULT_LISTEN_DIRECTION = """You are listening to a live studio recording by a small jazz group playing "{{song_name}}". \
The music is improvised; tempo and feel shift on purpose.

Listen to the whole recording and describe what happens, as material for a video editor:

1. Form. Split the recording into sections (intro, head, each solo, trading, drum solo, head out, ending...). \
For each: when it starts and ends, what it is, and who leads.
2. Each instrument, all the way through: what it is doing (soloing, comping, walking, playing the melody, \
laying out, fills, backing figures...) with a prominence from 0 (absent) to 3 (clearly in front). \
Accompanying instruments matter too: say when the bass or the drums do something out of their usual role \
(a pedal point, a fill, a break, a change of feel, a short solo phrase), because the editor wants to show those moments.
3. Moments: short events worth a camera on them, such as a big fill, a quote, a climax, a call and response, \
an ensemble hit, a laugh."""

DEFAULT_EDIT_DIRECTION = """Now be the video editor for this recording. Make the cut, using what you heard:

- Follow the music: the soloist's close-up during a solo, now and then a cut to someone reacting or answering; \
the head and ensemble passages on the wide shots.
- The bass and the drums play all the time, so don't show them all the time. Show them now and then, and \
especially when they do something different (the moments and role changes you heard).
- Come back to a wide shot about every 30-60 s, and use every camera at least once.
- Shots normally 4-10 s: longer where the music is calm, shorter where it's busy. A solo can stay on its \
soloist longer if it's going somewhere, up to about 20 s.
- Cut at the start of a bar, preferably at the start of a phrase (every 4 or 8 bars), and when the leader changes.
- Every cut is a short dissolve, so don't cut more often than every 3 s."""

LISTEN_FIXED = """

---
The recording is attached: "{{song_name}}", {{song_length}} long. Give every time in seconds from the start \
of the recording, to one decimal. Where each bar starts (bar=minutes:seconds), from an automatic beat tracker \
that can be off in rubato passages:

{{bars}}

Answer in the JSON format asked for: the instruments you hear, the sections, each instrument's activity and \
the moments. If you're unsure about something, say so in the notes rather than guessing."""

EDIT_FIXED = """

---
There is video from {{video_start}} to {{video_end}} of the recording: cut only within that. \
The cameras all film the whole song. What each one shows:

{{cameras}}

Answer in the JSON format asked for: the cuts in time order, each starting a shot that runs until the next \
cut (the first at the start of the song, the last until its end). time_s: seconds from the start of the \
recording, as in your listening; camera: one of the ids above; why: the musical reason in a few words. \
Then a summary of two or three lines on how you approached the cut."""

# Thinking: one setting for every model. Gemini 3 and later take a level; Gemini 2.5 a token budget.
THINKING_LEVELS = ("default", "minimal", "low", "medium", "high", "xhigh")
THINKING_BUDGETS_25 = {"minimal": 0, "low": 1024, "medium": 8192, "high": 24576, "xhigh": 32768}

DEFAULT_SETTINGS = {
    "model": "gemini-3-flash-preview",
    "thinking": "default",          # the model's own default
    "temperature": None,            # the model's own default (1.0; Google advises keeping it for Gemini 3)
    "max_output_tokens": 32768,     # per answer, thinking included
    "listen_direction": DEFAULT_LISTEN_DIRECTION,
    "edit_direction": DEFAULT_EDIT_DIRECTION,
}
DIRECTION_LONGEST = 20000


def validated_settings(settings):
    """The settings cleaned (missing ones from the defaults), or ValueError saying what is wrong."""
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    clean = copy.deepcopy(DEFAULT_SETTINGS)
    model = settings.get("model", clean["model"])
    if not isinstance(model, str) or not re.fullmatch(r"gemini[a-z0-9.\-]{1,60}", model):
        raise ValueError("the model must be a Gemini model id, like gemini-3-flash-preview")
    clean["model"] = model
    thinking = settings.get("thinking", "default")
    if thinking not in THINKING_LEVELS:
        raise ValueError("thinking must be one of %s" % ", ".join(THINKING_LEVELS))
    clean["thinking"] = thinking
    temperature = settings.get("temperature")
    if temperature is not None and (not isinstance(temperature, (int, float)) or not 0 <= temperature <= 2):
        raise ValueError("temperature must be 0-2, or empty for the model's own")
    clean["temperature"] = temperature
    longest = settings.get("max_output_tokens", clean["max_output_tokens"])
    if not isinstance(longest, int) or not 1024 <= longest <= 65536:
        raise ValueError("the longest answer must be 1024-65536 tokens")
    clean["max_output_tokens"] = longest
    for key in ("listen_direction", "edit_direction"):
        text = settings.get(key, clean[key])
        if not isinstance(text, str) or not text.strip() or len(text) > DIRECTION_LONGEST:
            raise ValueError("%s must be 1-%d characters" % (key.replace("_", " "), DIRECTION_LONGEST))
        unknown = sorted(set(PLACEHOLDER.findall(text)) - set(PLACEHOLDERS))
        if unknown:
            raise ValueError("unknown placeholder {{%s}} (known: %s)" % (unknown[0], ", ".join(sorted(PLACEHOLDERS))))
        clean[key] = text
    return clean


def generation_config(settings, schema):
    """Gemini's generationConfig for one call: JSON in this schema, thinking, temperature, length."""
    config = {"responseMimeType": "application/json", "responseJsonSchema": schema,
              "maxOutputTokens": settings["max_output_tokens"]}
    if settings["temperature"] is not None:
        config["temperature"] = settings["temperature"]
    thinking = settings["thinking"]
    if thinking != "default":
        if settings["model"].startswith("gemini-2."):
            budget = THINKING_BUDGETS_25[thinking]
            if budget == 0 and "pro" in settings["model"]:
                budget = 128                            # 2.5 Pro can't think less than this
            config["thinkingConfig"] = {"thinkingBudget": budget}
        else:
            config["thinkingConfig"] = {"thinkingLevel": thinking}
    return config


# ---------------------------------------------------------------- filling in the song

def clock(seconds):
    """93.4 -> "1:33.4"."""
    minutes, rest = divmod(max(0.0, seconds), 60)
    return "%d:%04.1f" % (minutes, rest)


def bar_list(downbeats_s, per_line=8):
    """Where each bar starts, eight to a line: "1=0:00.2  2=0:02.9 ..."."""
    entries = ["%d=%s" % (i + 1, clock(t)) for i, t in enumerate(downbeats_s)]
    lines = ["  ".join(entries[i:i + per_line]) for i in range(0, len(entries), per_line)]
    return "\n".join(lines) or "(no bars found: cut on the music itself)"


def camera_table(cameras):
    """The cameras as a table for the prompt. cameras: the editor project's (with tags)."""
    lines = ["| id | name | shows | shot | mount | note |", "|---|---|---|---|---|---|"]
    for camera in cameras:
        tags = camera["tags"]
        mount = {"fixed": "fixed on the wall", "tripod": "phone on a tripod",
                 "handheld": "phone, handheld (it shakes)"}.get(camera.get("mount"), camera.get("mount") or "")
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            camera["id"], camera["label"].replace("|", "/"), ", ".join(tags["shows"]) or "not said",
            tags["shot"] or "not said", mount, (tags["note"] or "").replace("|", "/")))
    return "\n".join(lines)


def fill(text, values):
    """{{name}} -> its value (unknown names are left as written)."""
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def prompts(settings, values):
    """{"listen": ..., "edit": ...}: the full prompts as sent."""
    return {"listen": fill(settings["listen_direction"] + LISTEN_FIXED, values),
            "edit": fill(settings["edit_direction"] + EDIT_FIXED, values)}


# ---------------------------------------------------------------- the answers' shapes

LISTEN_SCHEMA = {
    "type": "object",
    "properties": {
        "instruments": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "start": {"type": "number"}, "end": {"type": "number"}, "kind": {"type": "string"},
            "leader": {"type": "string"}, "notes": {"type": "string"}},
            "required": ["start", "end", "kind"]}},
        "activity": {"type": "array", "items": {"type": "object", "properties": {
            "instrument": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"},
            "doing": {"type": "string"}, "prominence": {"type": "integer", "minimum": 0, "maximum": 3}},
            "required": ["instrument", "start", "end", "doing"]}},
        "moments": {"type": "array", "items": {"type": "object", "properties": {
            "time": {"type": "number"}, "instrument": {"type": "string"}, "what": {"type": "string"}},
            "required": ["time", "what"]}},
    },
    "required": ["sections", "activity", "moments"],
}


def edit_schema(camera_ids):
    """The edit answer, with the camera ids as the only allowed cameras."""
    return {
        "type": "object",
        "properties": {
            "cuts": {"type": "array", "items": {"type": "object", "properties": {
                "time_s": {"type": "number"}, "camera": {"type": "string", "enum": list(camera_ids)},
                "why": {"type": "string"}}, "required": ["time_s", "camera", "why"]}},
            "summary": {"type": "string"},
        },
        "required": ["cuts", "summary"],
    }
