"""The title sequence at the start of the video (user, 2026-11-21; docs/editor-plan.md section 5b).

With the title on, every video opens the same way, timed from the start of the video:

  1. from black, "BROCKLEY LIVE" fades in (Elastic, a Barlow Regular, 83 px, cream);
  2. it fades out to black, and after 0.2 s of black the song's title fades in (Outfit Bold,
     83 px, letters 33 % apart, capitals, in the title's colour);
  3. the title fades out, the video fades in from black, and a strip along the bottom
     (black at 50 %) shows the lines below the title (who's playing), left-aligned, in cream, Outfit Bold,
     smaller, no extra letter spacing, as typed; after a while it dissolves out.

The music plays from the start of the video underneath (set the video's start a little
earlier to give the cards room). Without the title nothing of this happens.

The editor's preview draws the same thing (static/editor/js/title/title-layout.js) with the
same font file and these same numbers: change them in both places. The render draws each
card once as a PNG with Pillow (ffmpeg's drawtext can't space letters), places every letter
by its advance measured at 100x the size (as the browser's fractional advances; no kerning
in either), and the encoder's filter graph fades them in and out (edit_render.encoder_command).
"""
import os

WIDTH, HEIGHT = 1920, 1080
FONT = "Outfit-Bold.ttf"          # the title card and the strip
BRAND_FONT = "Barlow-Elastic.otf" # "BROCKLEY LIVE" (user, 2026-11-21: "Elastic", a Barlow Regular)
CREAM = "#f3ead8"                 # "BROCKLEY LIVE" and the strip's text (user: a creamy white)
BRAND_TEXT = "BROCKLEY LIVE"

CARD_SIZE = 83                    # px in the 1920x1080 picture (user: about 83 points)
CARD_TRACKING = 0.33              # the title's letter spacing, as a fraction of the size (user: about 33 %)
CARD_MAX_WIDTH = 1632             # a longer title gets a smaller size until it fits (144 px each side)
CAP_HEIGHT = 0.706                # Outfit's capitals, as a fraction of the size (centring)
BRAND_TRACKING = 0.0              # "BROCKLEY LIVE": no extra letter spacing (user, 2026-11-21)
BRAND_CAP_HEIGHT = 0.700          # Elastic's capitals

DETAIL_SIZE = 38                  # the strip's text (user: much smaller), left-aligned at DETAIL_LEFT
DETAIL_LINE_SPACING = 54          # baseline to baseline
STRIP_PADDING = 30                # strip edge to the top of the first capitals, and last baseline to the edge
STRIP_DESCENT = 10                # extra room under the last baseline for descenders
STRIP_ALPHA = 0.5                 # the strip's black (user: 50 %)
DETAIL_LEFT = 144                 # the strip's text starts here (user, 2026-11-21: left-aligned)
DETAIL_MAX_WIDTH = 1632

# The sequence, in seconds from the start of the video: (start, length). Stages one and two
# were cut to 75 % of 4 s and 5 s (user, 2026-11-21), with 0.2 s of black between them.
BRAND_IN = (0.0, 0.75)            # "BROCKLEY LIVE" fades in from black
BRAND_OUT = (2.25, 0.75)          # and out to black: gone at 3.0 s
TITLE_IN = (3.2, 0.75)            # 0.2 s of black, then the title fades in
TITLE_OUT = (6.2, 0.75)           # and out: gone at 6.95 s
VIDEO_IN = (6.95, 1.5)            # the video fades in from black
DETAILS_IN = 7.95                 # the strip starts dissolving in, 1 s after the video starts to
DETAILS_FADE = 0.7                # its dissolve in, and the one out; it stays duration_s in between

# The Brockley Live logo (the brand frame's logo; the border is on from the start) fades in
# shortly after the sequence is over, or at 5 s when there's no title (user, 2026-11-18).
LOGO_AFTER_TITLE_S = 1.0
LOGO_WITHOUT_TITLE_S = 5.0
LOGO_FADE_S = 1.0

MAX_LINES = 8
MAX_CHARACTERS = 80
MEASURE_SCALE = 100               # advances measured at 100x the size (Pillow rounds them to whole px)


def lines_below(title):
    """The non-empty lines under the title, trimmed: the strip's text."""
    return [line.strip() for line in title.get("lines") or [] if line.strip()][:MAX_LINES]


def is_shown(title):
    """Is the title sequence on?"""
    return bool(title) and title.get("on") is True


def title_text(title):
    """The title card's text: the title in capitals."""
    return (title.get("text") or "").strip().upper()


def details_end_s(title):
    """When the strip is gone (seconds from the start of the video)."""
    return DETAILS_IN + DETAILS_FADE + float(title.get("duration_s", 6)) + DETAILS_FADE


def logo_start_s(title):
    """When the logo starts fading in (seconds from the start of the video)."""
    if is_shown(title):
        return (details_end_s(title) if lines_below(title) else VIDEO_IN[0] + VIDEO_IN[1]) + LOGO_AFTER_TITLE_S
    return LOGO_WITHOUT_TITLE_S


# -- layout: the same numbers as title-layout.js
def letter_positions(text, size, tracking, advance):
    """[(letter, x from the start of the row)] and the row's width (no spacing after the last
    letter). advance(letter, size): the letter's advance in px."""
    positions, x = [], 0.0
    for index, letter in enumerate(text):
        positions.append((letter, x))
        x += advance(letter, size) + (tracking * size if index + 1 < len(text) else 0)
    return positions, x


def card_size(text, advance, tracking=CARD_TRACKING):
    """The card's font size: CARD_SIZE, smaller (whole px) when the text would be wider than CARD_MAX_WIDTH."""
    size = CARD_SIZE
    while size > 24 and letter_positions(text, size, tracking, advance)[1] > CARD_MAX_WIDTH:
        size -= 1
    return size


def card_baseline(size, cap_height=CAP_HEIGHT):
    """The card's baseline: its capitals centred top to bottom."""
    return round((HEIGHT + cap_height * size) / 2)


def strip_layout(lines):
    """(strip top, [baseline of each line]) for the strip along the bottom."""
    height = STRIP_PADDING + round(CAP_HEIGHT * DETAIL_SIZE) + (len(lines) - 1) * DETAIL_LINE_SPACING + STRIP_PADDING + STRIP_DESCENT
    top = HEIGHT - height
    first = top + STRIP_PADDING + round(CAP_HEIGHT * DETAIL_SIZE)
    return top, [first + i * DETAIL_LINE_SPACING for i in range(len(lines))]


# -- the cards as images (Pillow)
class Letters:
    """One font file at any size, for measuring and drawing single letters."""

    def __init__(self, fonts_dir, font=FONT):
        from PIL import ImageFont                  # only where titles are rendered (python3-pil on the hub)
        self.ImageFont = ImageFont
        self.path = os.path.join(fonts_dir, font)
        self.fonts = {}
        self.widths = {}

    def font(self, size):
        if size not in self.fonts:
            self.fonts[size] = self.ImageFont.truetype(self.path, size, layout_engine=self.ImageFont.Layout.BASIC)
        return self.fonts[size]

    def advance(self, letter, size):
        key = (letter, size)
        if key not in self.widths:
            self.widths[key] = self.font(size * MEASURE_SCALE).getlength(letter) / MEASURE_SCALE
        return self.widths[key]


def _colour(hex_colour):
    return tuple(int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))


def _text_layer(letters, rows, box, colour):
    """An RGBA image of the box (x, y, w, h) with each row's letters in `colour`.
    rows: [(text, size, tracking, x of the row, baseline)], picture coordinates."""
    from PIL import Image, ImageDraw
    x0, y0, w, h = box
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    for text, size, tracking, row_x, baseline in rows:
        positions, _ = letter_positions(text, size, tracking, letters.advance)
        for letter, x in positions:
            if not letter.isspace():
                draw.text((row_x + x - x0, baseline - y0), letter, fill=255, font=letters.font(size), anchor="ls")
    layer = Image.new("RGBA", (w, h), _colour(colour) + (0,))
    layer.putalpha(mask)
    return layer


def card_image(letters, text, colour, tracking=CARD_TRACKING, cap_height=CAP_HEIGHT):
    """(image, x, y): a centred card's text, cropped to its box, or None for no text."""
    if not text:
        return None
    size = card_size(text, letters.advance, tracking)
    _, width = letter_positions(text, size, tracking, letters.advance)
    row_x = (WIDTH - width) / 2
    baseline = card_baseline(size, cap_height)
    x0, y0 = max(0, int(row_x) - 16), baseline - int(size * 1.1)
    box = (x0, y0, min(WIDTH, int(row_x + width) + 16) - x0, int(size * 0.45) + int(size * 1.1))
    return _text_layer(letters, [(text, size, tracking, row_x, baseline)], box, colour), x0, y0


def strip_image(letters, lines):
    """(image, x, y): the strip along the bottom with the lines, left-aligned, or None for no lines."""
    if not lines:
        return None
    from PIL import Image
    top, baselines = strip_layout(lines)
    rows = []
    for line, baseline in zip(lines, baselines):
        size = DETAIL_SIZE
        _, width = letter_positions(line, size, 0, letters.advance)
        if width > DETAIL_MAX_WIDTH:                      # a very long line: smaller, as the cards
            size = max(16, int(size * DETAIL_MAX_WIDTH / width))
        rows.append((line, size, 0, DETAIL_LEFT, baseline))
    box = (0, top, WIDTH, HEIGHT - top)
    strip = Image.new("RGBA", box[2:], (0, 0, 0, round(255 * STRIP_ALPHA)))
    return Image.alpha_composite(strip, _text_layer(letters, rows, box, CREAM)), 0, top


# -- timing
def _ramp(t, start, length):
    return min(1.0, max(0.0, (t - start) / length))


def opacities_at(title, t):
    """How visible each layer is at t seconds into the video, as the filter graph fades them
    (linear): {"black", "brand", "title", "strip"}. The editor's preview follows these."""
    if not is_shown(title):
        return {"black": 0.0, "brand": 0.0, "title": 0.0, "strip": 0.0}
    out_start, out = TITLE_OUT
    strip_out = details_end_s(title) - DETAILS_FADE
    return {
        "black": 1.0 - _ramp(t, *VIDEO_IN),
        "brand": _ramp(t, *BRAND_IN) * (1.0 - _ramp(t, *BRAND_OUT)),
        "title": _ramp(t, *TITLE_IN) * (1.0 - _ramp(t, out_start, out)) if title_text(title) else 0.0,
        "strip": _ramp(t, DETAILS_IN, DETAILS_FADE) * (1.0 - _ramp(t, strip_out, DETAILS_FADE)) if lines_below(title) else 0.0,
    }


# -- the filter graph
def fade_chain(start_in, fade_in, start_out, fade_out):
    """Alpha fades in and out for a looped card (seconds from the start of the video)."""
    return ("fade=t=in:st=%.4f:d=%.4f:alpha=1,fade=t=out:st=%.4f:d=%.4f:alpha=1,trim=end=%.4f"
            % (start_in, fade_in, start_out, fade_out, start_out + fade_out))


def sequence_cards(title, work_dir, fonts_dir):
    """The cards as PNG files in work_dir with where and when they show:
    [(path, x, y, fade_chain)], in drawing order."""
    letters = Letters(fonts_dir)
    out_start, out = TITLE_OUT
    cards = [
        ("title-brand.png", card_image(Letters(fonts_dir, BRAND_FONT), BRAND_TEXT, CREAM, BRAND_TRACKING, BRAND_CAP_HEIGHT), fade_chain(BRAND_IN[0], BRAND_IN[1], BRAND_OUT[0], BRAND_OUT[1])),
        ("title-title.png", card_image(letters, title_text(title), title.get("colour") or CREAM),
         fade_chain(TITLE_IN[0], TITLE_IN[1], out_start, out)),
        ("title-strip.png", strip_image(letters, lines_below(title)),
         fade_chain(DETAILS_IN, DETAILS_FADE, details_end_s(title) - DETAILS_FADE, DETAILS_FADE)),
    ]
    placed = []
    for name, made, fades in cards:
        if made is None:
            continue
        image, x, y = made
        path = os.path.join(work_dir, name)
        image.save(path)
        placed.append((path, x, y, fades))
    return placed


def sequence_graph(graph, extra_inputs, title, work_dir, fonts_dir, fps):
    """The filter graph (ending in [video], or None) with the title sequence: black, then the
    video fading in, with the cards on top. Returns (extra_inputs, graph).
    Each card is a one-frame PNG input looped by the loop filter (no -loop: a looping image
    input made ffmpeg decode copies ahead, 2026-11-16), trimmed when it's gone."""
    first = 2 + sum(1 for part in extra_inputs if part == "-i")
    cards = sequence_cards(title, work_dir, fonts_dir)
    chains = [(graph[:-len("[video]")] + "[looked]") if graph else "[0:v]null[looked]",
              "[looked]fade=t=in:st=%.4f:d=%.4f[seq0]" % VIDEO_IN]
    for index, (path, x, y, fades) in enumerate(cards):
        extra_inputs = extra_inputs + ["-i", path]
        chains.append("[%d:v]format=rgba,loop=loop=-1:size=1,setpts=N/%d/TB,%s[card%d]" % (first + index, fps, fades, index))
        chains.append("[seq%d][card%d]overlay=x=%d:y=%d:format=auto:eof_action=pass[seq%d]" % (index, index, x, y, index + 1))
    chains[-1] = chains[-1][:chains[-1].rindex("[")] + "[video]" if cards else chains[-1].replace("[seq0]", "[video]")
    return extra_inputs, ";".join(chains)
