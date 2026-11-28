# AI cut plan ("Smart cut" on screen)

User decisions (2026-11-28). On screen it is the **Smart cut**, with no star or other "AI"
sign (user: the kids hate the notion of AI); in the code and the API it stays `ai_cut`. The AI agent was dropped at 1.0 (`docs/release-1.0.md`); the
user asked for it again in this form after a manual test in AI Studio ("it looks pretty
good"). The user judges the cuts; this plan gives them the building blocks to tune the
model, its cost and the prompts themselves.

## What it is

- **Smart cut** is a button in the editor's Cut step. Gemini listens to the song's bounce and
  gets a description of what each camera shows (no video). It sends back a list of
  decisions: which camera from when, and why. The hub places each cut on a bar and checks it.
- **Nothing is automatic any more** (user: "all triggered by user action"). A song opened for
  the first time starts with one cut: the first Pi camera for the whole song. The rule-based
  cut (`auto_cut.py`: beats, movement, the wide camera) and the quick mix's cut as a starting
  point are removed. Cutting by hand stays as it is.
- **Gemini through AI Studio**, the user's API key (`ai_studio_key` in the repo folder on the
  Mac, in `.gitignore`, copied to the hub by Ansible like the YouTube login; never in git).
  The hub makes the calls: the bounce, the beats and the edits are there.

## Two steps per run

1. **Listen** (the bounce attached): the song's form (sections, who leads), what each
   instrument does throughout with a prominence, including when the bass or drums do
   something out of their usual role, and moments worth a camera.
2. **Edit** (same conversation): from the listening and the camera table, the cuts:
   `{time_s, camera, why}` in time order, and a short summary.

Both answers are JSON, enforced by Gemini's structured output (`responseJsonSchema`).
The listening is reused when the bounce, the model, its settings and the listen prompt are
unchanged, so tuning the edit prompt re-runs only step 2.

## Prompts

Each prompt has a part the user writes (creative direction; in the settings) and a fixed
part the hub owns (what data comes with it and the answer's format). Placeholders in
`{{name}}` form are filled by the hub in both:

| Placeholder | What |
|---|---|
| `{{song_name}}`, `{{take_name}}` | the song's and the take's names |
| `{{song_length}}` | the bounce's length, m:ss |
| `{{video_start}}`, `{{video_end}}` | where in the recording the video starts and ends (the bounce often starts earlier) |
| `{{bars}}` | where each bar starts (beats.json), `bar=m:ss.s` |
| `{{cameras}}` | the camera table: id, name, what it shows, shot size, mount, note |

Step 2 is the same conversation as step 1 (the bounce and the listening answer are in it),
so the edit prompt doesn't repeat the listening. The hub's fixed part comes after the user's
text, after a `---` line: the song's data and the answer's fields in words. The exact answer
format is not in the text: it goes alongside as a JSON schema (structured output), with the
camera ids as the only allowed cameras.

The settings show the full prompts as they will be sent (a preview), the fixed parts and the
schemas, each with a copy button, and reset to the default. The defaults are the prompts from
the manual test. For trying it by hand (user, 2026-11-28): the bounce exactly as sent (mono
MP3) can be downloaded, and every run's details show both prompts, the settings and schema sent
with each, the raw answers, tokens, cost and what the hub changed, with the whole run as JSON.

## Settings (shared by all songs, kept on the hub)

- **Model:** from Google's live list for the key (Gemini models with generateContent; not
  TTS, image, live or embedding models), so new models appear by themselves.
- **Thinking level** (Gemini 3 and later: minimal / low / medium / high, as the model allows)
  or **thinking budget** (Gemini 2.5: tokens). The biggest lever on quality and cost.
- **Temperature**, and the **longest answer** (output tokens, thinking included).
- There is no context window to choose: each model's is fixed, and a song uses a few
  percent of it.

## Camera tags

Every camera, Pi or phone, gets the same setting with the same API and UI:

- **Shows:** one or more instruments, or **Whole band**.
- **Shot:** wide, medium or close.
- **Note:** free text ("from the back", "bass not very visible").

Set on the hub page (the camera's current role), copied into each take at REC (the take
record, `camera_tags`), and adjustable for one take in the editor (its AI cut settings,
Cameras). Takes recorded before this use the camera's current tags. `{{cameras}}` is built
from the take's tags, the camera's name and its mount (fixed, tripod, handheld).
The instruments to pick from are a list kept on the hub, not in the config (user: who comes in
changes from take to take): it starts with standard names, and "+ instrument" adds one (a
cello), for every camera. Standard names help the cut; an unusual one only makes it less good.

## Placing the cuts (the hub, after step 2)

- Times are bounce seconds; the hub turns them into the song's time with the sync (and its
  drift), the same way the beats are placed.
- Each cut moves to the nearest bar start within 1.2 s, else the nearest beat within 0.5 s.
- A camera that wasn't recording then is replaced: by a wide camera that covers it, else the
  previous camera. Dropped frames in a shot are tolerated, as everywhere, and said in the note.
- A cut before the video starts opens it (the last such one). Cuts after the song's end, to
  unknown cameras, or less than 2 s after the previous one are left out (Gemini 3 Flash went
  on cutting past the end of a 12-minute song in the first test: the edit prompt now says
  where the video starts and ends).
- Every change the hub makes is kept on the cut (`note`), next to the model's `reason`.

## Runs, cost and errors

- Every run is kept on the hub's SD card (`<state>/ai_cut/<take>/<song>/<run>.json`): the
  settings, both prompts as sent, Gemini's raw answers, tokens per call, the cost, the
  listening, the cuts and the hub's changes, the time taken, or the error.
- **Cost:** Gemini reports the tokens; the hub prices them with `ai_prices.json` (per model:
  input, audio input, output with thinking; dated, from Google's price page, edited by hand
  when Google changes them). Shown per run, with an estimate before running. A model
  missing from the table shows its tokens and "price not known".
- **Errors** in plain words: no key; key refused; **credit used up** (the AI Studio balance,
  with where to top it up); rate limit (try again in a minute); model unavailable; an answer
  that isn't valid JSON (the raw text is kept to look at).

## The editor

- **AI cut** and a settings key next to it, above the timeline in the Cut step (next to
  Zoom). Pressing AI cut shows the estimate and the steps as they run (preparing the
  bounce, uploading it, listening, editing, placing the cuts), then the cost.
- The result replaces the song's cut through the edit history: Undo brings back the cut from
  before. Opening the song never runs anything; the cut is saved like any other.
- **Why:** hovering a shot on the timeline shows the model's reason, and the hub's change
  if it made one; while playing, the video window shows the reason for the shot on screen,
  the music's section, and the hub's change (the preview only, never the render).
- **Music lane** above the cameras: the sections the model heard (kind, leader) and its
  moments, with their notes on hover. It shows whether a bad cut came from bad hearing or a
  bad edit.
- **Runs:** the song's runs with time, model, cost and number of cuts; any run's cut can be
  used again.

## Order of work

1. Camera tags: store, API, hub page, take copy at REC, per-take copy in the editor.
2. The first camera for a new song; the rule-based cut removed.
3. On the hub: the Gemini client (standard library), the AI cut job (prompts, the two calls,
   placing, cost, runs), the API, the key deployed.
4. The editor: the button, settings, progress, reasons, Music lane, runs.

Not in it: the vertical story (the AI cut makes the main video's cut only), sending
pictures or video to the model, colour or look decisions.
