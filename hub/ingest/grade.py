"""The colour grade behind every Filter, in Python (for the render's 3D LUT).

THE SAME MATHS IS IN hub/hubd/static/editor/js/look/grade.js (the editor's preview):
change both together. `python3 grade.py --vectors` prints test vectors that the editor's
test (hub/hubd/tests/editor/grade.test.mjs) checks the JavaScript against.

Colours are r, g, b in 0..1 (display, gamma-encoded, as in the video). Steps, in order:
exposure (stops) -> temperature and tint -> contrast around 0.5 -> highlight and shadow
lift -> saturation (or mono) -> shadows/highlights colour -> fade -> clamp. A look is the
Adjust sliders first (primary correction), then the filter, whose strength blends between
its input (0) and the filtered colour (1): look_colour(). The render writes the result as
a .cube file for ffmpeg's lut3d filter.
"""
import json
import sys

LUT_SIZE = 33
LUMA = (0.2126, 0.7152, 0.0722)        # Rec. 709


def clamp01(value):
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def grade_colour(colour, settings):
    r, g, b = colour
    exposure = settings.get("exposure", 0)
    temperature = settings.get("temperature", 0)
    tint = settings.get("tint", 0)
    contrast = settings.get("contrast", 1)
    saturation = 0 if settings.get("mono") else settings.get("saturation", 1)
    shadows = settings.get("shadows", [1, 1, 1])
    highlights = settings.get("highlights", [1, 1, 1])
    fade = settings.get("fade", 0)
    highlight_lift = settings.get("highlight_lift", 0)
    shadow_lift = settings.get("shadow_lift", 0)
    gain = 2 ** exposure
    r, g, b = r * gain, g * gain, b * gain
    r *= 1 + 0.1 * temperature
    b *= 1 - 0.1 * temperature
    g *= 1 - 0.08 * tint
    r, g, b = [(c - 0.5) * contrast + 0.5 for c in (r, g, b)]

    def tone(c):
        x = clamp01(c)
        return c + highlight_lift * 0.25 * x * x * x + shadow_lift * 0.25 * (1 - x) * (1 - x) * (1 - x)
    r, g, b = tone(r), tone(g), tone(b)
    luma = LUMA[0] * r + LUMA[1] * g + LUMA[2] * b
    r, g, b = [luma + (c - luma) * saturation for c in (r, g, b)]
    light = clamp01(luma)
    r *= shadows[0] * (1 - light) + highlights[0] * light
    g *= shadows[1] * (1 - light) + highlights[1] * light
    b *= shadows[2] * (1 - light) + highlights[2] * light
    r, g, b = [fade + c * (1 - fade) for c in (r, g, b)]
    return [clamp01(r), clamp01(g), clamp01(b)]


def grade_with_strength(colour, settings, strength):
    graded = grade_colour(colour, settings)
    return [c + (d - c) * strength for c, d in zip(colour, graded)]


def adjustment_settings(sliders, definitions):
    """Grade settings from the Adjust sliders ({id: -1..1}), as defined in looks.json."""
    settings = {}
    for definition in definitions or []:
        value = (sliders or {}).get(definition["id"], 0) or 0
        if definition.get("positive_only"):
            value = max(0, value)
        settings[definition["setting"]] = definition["base"] + value * definition["scale"]
    return settings


def look_colour(colour, adjust, settings, strength):
    """The whole look on one colour: Adjust first, then the filter at its strength."""
    return grade_with_strength(grade_colour(colour, adjust), settings, strength)


def write_cube(path, settings, strength, size=LUT_SIZE, adjust=None):
    """A .cube 3D LUT for ffmpeg's lut3d (red fastest, then green, then blue)."""
    lines = ["TITLE \"studio-multicam look\"", "LUT_3D_SIZE %d" % size]
    for bi in range(size):
        for gi in range(size):
            for ri in range(size):
                r, g, b = look_colour([ri / (size - 1), gi / (size - 1), bi / (size - 1)], adjust or {}, settings, strength)
                lines.append("%.6f %.6f %.6f" % (r, g, b))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def test_vectors(looks):
    """Graded colours for a spread of inputs, every filter, two strengths."""
    inputs = [[0, 0, 0], [1, 1, 1], [0.5, 0.5, 0.5], [0.8, 0.3, 0.2], [0.1, 0.4, 0.9], [0.25, 0.7, 0.35]]
    out = []
    for look in looks["filters"]:
        for strength in (1.0, 0.4):
            for colour in inputs:
                out.append({"filter": look["id"], "strength": strength, "in": colour,
                            "out": [round(v, 6) for v in grade_with_strength(colour, look["grade"], strength)]})
    # The Adjust sliders, alone and together with a filter.
    for sliders in ({"brightness": 0.6, "contrast": -0.3, "highlights": -0.8, "shadows": 0.7},
                    {"saturation": -0.5, "warmth": 0.8, "tint": -0.4, "fade": 0.9},
                    {"brightness": -0.4, "highlights": 0.5, "shadows": -0.6, "warmth": -1, "fade": -1}):
        adjust = adjustment_settings(sliders, looks["adjustments"])
        for filter_id, strength in (("normal", 1.0), ("golden", 0.7)):
            grade = next(f["grade"] for f in looks["filters"] if f["id"] == filter_id)
            for colour in inputs:
                out.append({"sliders": sliders, "filter": filter_id, "strength": strength, "in": colour,
                            "out": [round(v, 6) for v in look_colour(colour, adjust, grade, strength)]})
    return out


if __name__ == "__main__" and "--vectors" in sys.argv:
    with open(sys.argv[sys.argv.index("--vectors") + 1]) as f:
        print(json.dumps(test_vectors(json.load(f))))
