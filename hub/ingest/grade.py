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
import math
import sys

LUT_SIZE = 33
LUMA = (0.2126, 0.7152, 0.0722)        # Rec. 709


def clamp01(value):
    return 0.0 if value < 0 else 1.0 if value > 1 else value


# ---- CSS filters and blended colour layers (W3C Filter Effects and Compositing specs), the
# model of the Instagram-style filters (after CSSgram, MIT). Each filter function clamps.
def _matrix(c, m):
    return [clamp01(m[i][0] * c[0] + m[i][1] * c[1] + m[i][2] * c[2]) for i in range(3)]


def css_filter(c, name, amount):
    if name == "brightness":
        return [clamp01(v * amount) for v in c]
    if name == "contrast":
        return [clamp01((v - 0.5) * amount + 0.5) for v in c]
    if name == "saturate":
        s = amount
        return _matrix(c, [[0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
                           [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
                           [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]])
    if name == "grayscale":
        g = 1 - min(1, amount)
        return _matrix(c, [[0.2126 + 0.7874 * g, 0.7152 - 0.7152 * g, 0.0722 - 0.0722 * g],
                           [0.2126 - 0.2126 * g, 0.7152 + 0.2848 * g, 0.0722 - 0.0722 * g],
                           [0.2126 - 0.2126 * g, 0.7152 - 0.7152 * g, 0.0722 + 0.9278 * g]])
    if name == "sepia":
        g = 1 - min(1, amount)
        return _matrix(c, [[0.393 + 0.607 * g, 0.769 - 0.769 * g, 0.189 - 0.189 * g],
                           [0.349 - 0.349 * g, 0.686 + 0.314 * g, 0.168 - 0.168 * g],
                           [0.272 - 0.272 * g, 0.534 - 0.534 * g, 0.131 + 0.869 * g]])
    if name == "hue-rotate":
        a = math.radians(amount)
        cs, sn = math.cos(a), math.sin(a)
        return _matrix(c, [[0.213 + cs * 0.787 - sn * 0.213, 0.715 - cs * 0.715 - sn * 0.715, 0.072 - cs * 0.072 + sn * 0.928],
                           [0.213 - cs * 0.213 + sn * 0.143, 0.715 + cs * 0.285 + sn * 0.140, 0.072 - cs * 0.072 - sn * 0.283],
                           [0.213 - cs * 0.213 - sn * 0.787, 0.715 - cs * 0.715 + sn * 0.715, 0.072 + cs * 0.928 + sn * 0.072]])
    raise ValueError("unknown CSS filter %r" % name)


def _soft_light(b, s):
    if s <= 0.5:
        return b - (1 - 2 * s) * b * (1 - b)
    d = ((16 * b - 12) * b + 4) * b if b <= 0.25 else math.sqrt(b)
    return b + (2 * s - 1) * (d - b)


def _hard_light(b, s):
    return b * 2 * s if s <= 0.5 else b + (2 * s - 1) - b * (2 * s - 1)


SEPARABLE = {
    "normal": lambda b, s: s,
    "multiply": lambda b, s: b * s,
    "screen": lambda b, s: b + s - b * s,
    "overlay": lambda b, s: _hard_light(s, b),
    "darken": min,
    "lighten": max,
    "color-dodge": lambda b, s: 0.0 if b == 0 else 1.0 if s >= 1 else min(1.0, b / (1 - s)),
    "color-burn": lambda b, s: 1.0 if b >= 1 else 0.0 if s <= 0 else 1 - min(1.0, (1 - b) / s),
    "soft-light": _soft_light,
    "hard-light": _hard_light,
    "exclusion": lambda b, s: b + s - 2 * b * s,
    "difference": lambda b, s: abs(b - s),
}


def _lum(c):
    return 0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]


def _set_lum(c, l):
    d = l - _lum(c)
    c = [v + d for v in c]
    lum, lo, hi = _lum(c), min(c), max(c)
    if lo < 0:
        c = [lum + (v - lum) * lum / (lum - lo) for v in c]
    if hi > 1:
        c = [lum + (v - lum) * (1 - lum) / (hi - lum) for v in c]
    return c


def blend_layer(backdrop, layer):
    """A solid colour layer (CSS color + alpha x opacity) blended over the picture."""
    colour, mode = layer["color"], layer.get("mode", "normal")
    alpha = layer.get("alpha", 1.0) * layer.get("opacity", 1.0)
    if mode == "color":
        mixed = _set_lum(colour, _lum(backdrop))
    else:
        f = SEPARABLE[mode]
        mixed = [f(b, s) for b, s in zip(backdrop, colour)]
    return [clamp01((1 - alpha) * b + alpha * m) for b, m in zip(backdrop, mixed)]


def css_look(colour, settings):
    for name, amount in settings.get("css") or []:
        colour = css_filter(colour, name, amount)
    for layer in settings.get("layers") or []:
        colour = blend_layer(colour, layer)
    return colour


def grade_colour(colour, settings):
    if settings.get("css") or settings.get("layers"):
        colour = css_look(list(colour), settings)
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
