"""The video's title (docs/editor-plan.md section 5b): a title line and the lines below it
(usually who's playing), left-aligned at LEFT and centred top to bottom, in Figtree, in one colour. It dissolves in all at once at
start_s (song seconds), stays, and dissolves out so it's gone duration_s later.

The editor's preview draws the same thing on a canvas (static/editor/js/title/title-layout.js),
with the same font files and these same numbers: change them in both places. The render uses
ffmpeg's drawtext (HarfBuzz shaping, y_align=baseline, so a baseline here is the canvas's
alphabetic baseline) on the finished picture: after the look and its effects, under the
border and logo.
"""
import os

WIDTH, HEIGHT = 1920, 1080
LEFT = 144                       # px from the left edge: the start of every row (user: left-aligned)
TITLE_FONT = "Figtree-SemiBold.ttf"
LINE_FONT = "Figtree-Regular.ttf"
TITLE_SIZE = 84                  # px, in the 1920x1080 picture
LINE_SIZE = 42
TITLE_TO_LINES = 78              # baseline of the title to the baseline of the first line below
LINE_SPACING = 60                # baseline to baseline between the lines below
CAP_HEIGHT = 0.7                 # Figtree's capitals, as a fraction of the size (centring)
FADE_S = 0.5                     # the dissolve in, and the one out
SHADOW_Y = 2                     # a hard shadow straight down, so light text reads on light walls
SHADOW_ALPHA = 0.45

# The Brockley Live logo (the brand frame's logo; the border is on from the start) fades in
# a little into the song (user, 2026-11-18): shortly after the title is gone, or at 5 s when
# there's no title.
LOGO_AFTER_TITLE_S = 1.0
LOGO_WITHOUT_TITLE_S = 5.0
LOGO_FADE_S = 1.0

MAX_LINES = 8
MAX_CHARACTERS = 80


def lines_below(title):
    """The non-empty lines under the title, trimmed."""
    return [line.strip() for line in title.get("lines") or [] if line.strip()][:MAX_LINES]


def is_shown(title):
    """Is there a title to put on the video?"""
    return bool(title) and title.get("on") is True and bool((title.get("text") or "").strip() or lines_below(title))


def layout(title):
    """[(text, font file, size, baseline y)] for the title and each line below, top to bottom.
    The block is centred: from the top of the title's capitals to the last baseline."""
    text = (title.get("text") or "").strip()
    below = lines_below(title)
    rows = ([(text, TITLE_FONT, TITLE_SIZE)] if text else []) + [(line, LINE_FONT, LINE_SIZE) for line in below]
    steps = [0]
    for (_, _, previous), (_, _, size) in zip(rows, rows[1:]):
        steps.append(steps[-1] + (TITLE_TO_LINES if previous == TITLE_SIZE else LINE_SPACING))
    first_size = rows[0][2]
    first_baseline = round((HEIGHT + CAP_HEIGHT * first_size - steps[-1]) / 2)
    return [(row_text, font, size, first_baseline + step) for (row_text, font, size), step in zip(rows, steps)]


def logo_start_s(title):
    """When the logo starts fading in (song seconds)."""
    if is_shown(title):
        return float(title["start_s"]) + float(title["duration_s"]) + LOGO_AFTER_TITLE_S
    return LOGO_WITHOUT_TITLE_S


def opacity_expression(title):
    """drawtext's alpha as an expression of the output time t (song seconds)."""
    start = float(title["start_s"])
    end = start + float(title["duration_s"])
    return "clip(min((t-%.4f)/%.4f,(%.4f-t)/%.4f),0,1)" % (start, FADE_S, end, FADE_S)


def title_filters(title, work_dir, fonts_dir):
    """The drawtext filters (comma-joined) that put the title on a picture. Each line's text
    goes in through a file (no escaping) with expansion=none, so a name with % or : is just text."""
    colour = "0x" + title.get("colour", "#ffffff").lstrip("#")
    alpha = opacity_expression(title)
    filters = []
    for index, (text, font, size, baseline) in enumerate(layout(title)):
        text_file = os.path.join(work_dir, "title-%d.txt" % index)
        with open(text_file, "w", encoding="utf-8") as f:
            f.write(text)
        filters.append(
            "drawtext=fontfile='%s':textfile='%s':expansion=none:fontsize=%d:fontcolor=%s:"
            "x=%d:y=%d:y_align=baseline:shadowx=0:shadowy=%d:shadowcolor=black@%.2f:alpha='%s'"
            % (os.path.join(fonts_dir, font), text_file, size, colour, LEFT, baseline, SHADOW_Y, SHADOW_ALPHA, alpha))
    return ",".join(filters)
