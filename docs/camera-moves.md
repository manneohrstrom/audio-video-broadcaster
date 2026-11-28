# Camera moves ("Ken Burns")

User decisions (2026-11-28). A shot can have a slow zoom or drift, made in the edit (the
cameras stay fixed on the wall), "to give the cuts a bit more life", "a subtle sort of
nuance", "not always but sometimes". The smart cut can give shots moves; the Cut step is where
they are changed by hand.

## What a move is

A cut may carry its shot's move: `"move": {"kind": "in" | "out" | "left" | "right", "amount": 0.05..1}`.

| Kind | What it does | For |
|---|---|---|
| `in` | a slow push in on the middle of the picture | close-ups: the player is in the middle, so this zooms in on the player (the soloist as the solo builds) |
| `out` | a slow pull back from the middle | the same, the other way |
| `left`, `right` | a slow sideways drift at a constant zoom | wide shots: only breaks up the still picture |

Moves always centre on the middle of the picture (user: "for the close-ups you should just
assume that the centre of attention is in the middle of the frame"). There are no marked spots
per camera.

- **Timing:** the move runs in a straight line over everything of the shot on screen, from half
  a dissolve before its cut to half a dissolve after the next one, so it is already moving as
  the shot dissolves in. It is timed over the whole shot also when the video's or the story's
  start or end cuts the shot short (the render's `trimmed()` keeps `move_span_ns`).
- **How much:** amount 1 is a 12 % zoom, and at most 1.5 % per second of the shot, so a short
  shot moves less instead of faster. The default (and the smart cut's) amount is 0.5: 6 %.
  The Pis record 1080p, so a move is an upscale; above about 12 % it starts to look soft.
- **Story:** the story's 9:16 slice is cut from the moved picture, so a story moves too.

## Where it lives

- Geometry: `hub/ingest/camera_move.py` (the render) and `hub/hubd/static/editor/js/camera-move.js`
  (the preview), tested against the same numbers. hubd checks moves on save
  (`editor_projects.clean_move`; a test keeps it equal to the render's).
- Render: ffmpeg's `perspective` filter (cubic, evaluated per frame) in the moved shot's decoder,
  right after the scale (`edit_render.decoder_command`). A dissolve mixes two pictures that each
  keep their own move.
- Preview: the camera's picture drawn through a scaled canvas (`VideoWindow.drawCamera`), sampled
  between pixels like the render.
- Cut step: **Move** beside the Smart cut button: still / in / out / ← / → for the shot under the
  playhead, and how much (the most it zooms, in %). A moved shot's label on the Video strip says
  its move ("cam2 · in"); hovering shows it. Moves stay with their shot when it is slid or its
  edges are dragged, and both parts keep it when another camera is chosen inside it. Undo covers
  every change (a slider drag is one step).
- Smart cut: each cut in the edit answer has a `move` (`none` / `in` / `out` / `left` / `right`,
  required in the schema). What moves are and when to use them is in the hub's fixed part of the
  edit prompt (`EDIT_FIXED`), so it reaches every run whatever the edit direction in the settings
  says; the direction (or a run's own direction) can steer it ("no moves", "more moves").

## The spike (2026-11-28)

Real footage (take 20261127-192905, cam3, 1.00 to 1.12 over 8 s), the render's decoder chain on
the hub. Jitter: the measured frame-to-frame shift of static patches minus the move's exact
shift (median px; the move itself about 0.5 px a frame):

| Method | Jitter | One decoder, 1 thread |
|---|---|---|
| no move (the floor) | 0.03 | 48 fps, 89 MB |
| `zoompan` (the pulse effect's filter) | 0.31 | 52 fps, 94 MB |
| `scale` (per frame) + `crop` | 0.40-0.77 | |
| `perspective`, cubic | 0.15 | 15 fps, 128 MB |

`zoompan` and scale+crop place the picture on whole pixels, which steps on a move this slow.
`perspective` is fast enough (looked renders run at 3.5-4.4 fps); the cost is ~40 MB more per
moved decoder (two during a dissolve), on a hub whose renders already run close to its memory.
The user watched zoompan and perspective side by side: "clips look absolutely fine".
