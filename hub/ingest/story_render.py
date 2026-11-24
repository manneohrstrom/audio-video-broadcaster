"""The Instagram story of an edit: a 9:16 slice of the finished video (user, 2026-11-24).

The story is the same video as the YouTube one, cut, look and effects alike, from its own
start to its own end (story "range_ns"), without the title sequence and the border and logo.
Its last step crops a 9:16 slice out of each 1920x1080 frame and scales it to 1080x1920.

Where the slice sits is set per camera (story "framing": {camera: 0..1}, 0 at the left edge,
1 at the right, 0.5 in the middle; the cameras don't move, so neither does what they show).
At a cut the slice glides from one camera's place to the next one's over the dissolve, with
the dissolve's own curve, so it never jumps while two pictures are mixed. The editor's
preview (static/editor/js/story.js) does the same.

The slice is 608 of 1920 pixels wide, so the story is that slice scaled up about 1.8 times.
"""
import edit_render

WIDTH, HEIGHT = 1080, 1920             # the story: Instagram's 9:16 at 1080p
SOURCE_WIDTH, SOURCE_HEIGHT = edit_render.WIDTH, edit_render.HEIGHT
# 1080 x 9 / 16 = 607.5 pixels of the 1080p frame, rounded to even for 4:2:0.
SLICE_WIDTH = 608
CENTRE = 0.5
DEFAULT_LENGTH_NS = 30_000_000_000     # a new story: 30 s from the video's start (as the editor)
FADE_IN_S = 0.3                        # the sound fades in and out: a story starts and ends mid-song
FADE_OUT_S = 1.0


def slice_left(position):
    """The slice's left edge in 1080p pixels for a position 0..1 (whole pixels)."""
    position = min(1.0, max(0.0, float(position)))
    return int(round(position * (SOURCE_WIDTH - SLICE_WIDTH)))


def story_range_ns(edit):
    """[start, end] of the story in ns from the start of the song's range: its own, else
    30 s from the video's start (trim_ns), shorter if the video is."""
    story = edit.get("story") or {}
    if story.get("range_ns"):
        return [int(story["range_ns"][0]), int(story["range_ns"][1])]
    length = edit["range_ns"][1] - edit["range_ns"][0]
    start, end = edit.get("trim_ns") or [0, length]
    return [int(start), int(min(end, start + DEFAULT_LENGTH_NS))]


def story_edit(edit):
    """The edit as the story renders it: its range as the video's start and end, no title
    sequence, no border and logo (both are drawn for the 16:9 frame)."""
    return dict(edit, trim_ns=story_range_ns(edit), title=None, branding=False)


def positions_per_frame(shots, framing, half_dissolve):
    """The slice's left edge (pixels) at every output frame of the story.
    shots: edit_render.Shot list (first_frame, end_frame, camera_id), in order.
    framing: {camera: 0..1}; a camera that isn't in it is centred."""
    if not shots:
        return []
    left = [slice_left(framing.get(shot.camera_id, CENTRE)) for shot in shots]
    positions = []
    for index, shot in enumerate(shots):
        for frame in range(shot.first_frame, shot.end_frame):
            x = left[index]
            if index + 1 < len(shots) and frame >= shots[index + 1].first_frame - half_dissolve:
                mix = edit_render.dissolve_mix(frame, shots[index + 1].first_frame, half_dissolve)
                x = left[index] * (1 - mix) + left[index + 1] * mix
            elif index > 0 and frame < shot.first_frame + half_dissolve:
                mix = edit_render.dissolve_mix(frame, shot.first_frame, half_dissolve)
                x = left[index - 1] * (1 - mix) + left[index] * mix
            positions.append(int(round(x)))
    return positions


def runs(positions):
    """[(first frame, last frame, x)]: the frames where the slice stays put."""
    out = []
    for frame, x in enumerate(positions):
        if out and out[-1][2] == x:
            out[-1] = (out[-1][0], frame, x)
        else:
            out.append((frame, frame, x))
    return out


def x_expression(positions):
    """crop's x as an expression of the frame number n: one term per run, so it's short
    between cuts and a few terms per dissolve."""
    terms = ["%d*between(n,%d,%d)" % (x, first, last) for first, last, x in runs(positions) if x]
    return "+".join(terms) or "0"


def crop_filter(positions):
    """The story's last filters: the 9:16 slice (whole pixels: exact=1), scaled to 1080x1920."""
    return ("crop=w=%d:h=%d:x='%s':y=0:exact=1,scale=%d:%d:flags=lanczos,setsar=1"
            % (SLICE_WIDTH, SOURCE_HEIGHT, x_expression(positions), WIDTH, HEIGHT))


class StoryRenderer(edit_render.EditRenderer):
    """EditRenderer for the story: the story's part of the edit, its slice last, the sound faded."""

    def __init__(self, edit, *args, **kwargs):
        kwargs.update(brand_dir=None)
        super().__init__(story_edit(edit), *args, **kwargs)
        framing = (edit.get("story") or {}).get("framing") or {}
        self.positions = positions_per_frame(self.shots, framing, self.half_dissolve)

    def encoder_command(self, out_path):
        return edit_render.encoder_command(self.bounce, self.audio_start_s(), self.total_frames, out_path,
                                           self.effects, self.work_dir, downbeats_s=self.downbeats_s,
                                           title=None, fonts_dir=self.fonts_dir,
                                           last_filters=crop_filter(self.positions),
                                           audio_fades_s=(FADE_IN_S, FADE_OUT_S))
