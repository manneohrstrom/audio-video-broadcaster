"""The song's look in the render: the same as the editor's preview, done by ffmpeg.

The editor shows the look with WebGL (hub/hubd/static/editor/js/look/look-renderer.js).
The render gives the same picture with ffmpeg, following that shader step by step:

  colour   Adjust sliders, then the filter at its strength, as a 33^3 colour table
           (grade.py, the same maths as the editor's grade.js), applied by the decoders
           with lut3d (trilinear, like the WebGL 3D texture): write_look_lut()
  effects  in the encoder's filter graph (effect_filter_graph()), in the shader's order:
    vhs       red and blue shifted sideways (3 px x strength at 1080p)        rgbashift
    glow      the picture blurred (quarter size), brightened, screened on top gblur + blend
    leak      warm light from the left, drifting with song time: the shader's
              formula, generated at quarter size by geq, screened on top     geq + blend
    vignette  darker towards the corners: the shader's formula as a mask     blend multiply
    vhs       scanlines (every third row): the shader's pattern as a mask    blend multiply
    grain     random luma noise per frame, the same amount (not the same grains)  noise

Why ffmpeg and not numpy (2026-11-16): per-frame numpy effects on 1080p RGB needed up to
~270 MB in the render's Python process and ran the 1 GB hub out of memory during a
dissolve; ffmpeg's filters run in C, multithreaded, on frames it already holds.
Masks are computed once with numpy and written as .ppm images.
"""
import os

import numpy as np

import grade

WIDTH, HEIGHT = 1920, 1080
EFFECTS = ("grain", "vignette", "leak", "glow", "vhs")


def look_settings(look, looks):
    """(adjust settings, filter settings, filter strength) for a saved look."""
    look = look or {}
    adjust = grade.adjustment_settings(look.get("adjust"), looks.get("adjustments"))
    filter_settings = next((f["grade"] for f in looks.get("filters", []) if f["id"] == look.get("filter")), {})
    return adjust, filter_settings, float(look.get("filter_strength", 1))


def write_look_lut(path, look, looks):
    """The look's colour as a .cube file; None if it changes nothing (no table needed)."""
    adjust, filter_settings, strength = look_settings(look, looks)
    neutral = grade.adjustment_settings({}, looks.get("adjustments"))
    if (not filter_settings or strength == 0) and adjust == neutral:
        return None
    grade.write_cube(path, filter_settings, strength, adjust=adjust)
    return path


def active_effects(look):
    return {name: float(value) for name, value in ((look or {}).get("effects") or {}).items()
            if value and name in EFFECTS}


def smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def shader_uv(width=WIDTH, height=HEIGHT):
    """Pixel centres in the shader's uv space: x 0..1 left to right, y 0..1 bottom to top."""
    x = (np.arange(width, dtype=np.float32) + 0.5) / width
    y = 1.0 - (np.arange(height, dtype=np.float32) + 0.5) / height
    return np.meshgrid(x, y)


def write_ppm(path, gray):
    """A grey image (0..1 floats) as an 8-bit RGB .ppm, which ffmpeg reads directly."""
    values = (np.clip(gray, 0, 1) * 255 + 0.5).astype(np.uint8)
    rgb = np.repeat(values[..., None], 3, axis=2)
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (gray.shape[1], gray.shape[0]))
        f.write(rgb.tobytes())
    return path


def vignette_mask(strength):
    uv_x, uv_y = shader_uv()
    distance = np.hypot(uv_x - 0.5, uv_y - 0.5) / 0.7071
    return 1.0 - strength * 0.9 * smoothstep(0.45, 1.0, distance)


def scanline_mask(strength):
    rows_from_bottom = (HEIGHT - 1 - np.arange(HEIGHT)) + 0.5
    darker = (np.mod(rows_from_bottom / 3.0, 1.0) >= 0.5).astype(np.float32)
    return np.repeat((1.0 - strength * 0.35 * darker)[:, None], WIDTH, axis=1)


def leak_expression(strength, channel_weight, width, height):
    """The shader's light leak for one colour channel, as an ffmpeg geq expression of X, Y, T."""
    aspect = WIDTH / HEIGHT
    uv_x = "((X+0.5)/%d)" % width
    uv_y = "(1-(Y+0.5)/%d)" % height
    drift = "(0.05+0.1*sin(T*0.3))"
    distance = "hypot((%s-%s)*%.6f\\,%s-0.3)" % (uv_x, drift, aspect, uv_y)
    t = "clip(1-%s/%.6f\\,0\\,1)" % (distance, 0.55 * aspect)
    amount = "%.6f*(0.45+0.2*sin(T*0.6))*(%s)*(%s)*(3-2*(%s))" % (strength, t, t, t)
    return "255*%.4f*%s" % (channel_weight, amount)


def effect_filter_graph(effects, work_dir, first_extra_input):
    """(extra ffmpeg inputs, filter_complex text) turning [0:v] into [video] with the effects.
    first_extra_input: the input number the first extra input (a mask image) will get."""
    inputs, chains = [], []
    current = "[0:v]"
    step = [0]

    def label():
        step[0] += 1
        return "[e%d]" % step[0]

    def image_input(path):
        inputs.extend(["-loop", "1", "-framerate", "30", "-i", path])
        return "[%d:v]" % (first_extra_input + len(inputs) // 6 - 1)

    out = label()
    chains.append("%sformat=gbrp%s" % (current, out))
    current = out
    if effects.get("vhs"):
        shift = max(1, int(round(3 * effects["vhs"])))
        out = label()
        chains.append("%srgbashift=rh=%d:bh=%d%s" % (current, -shift, shift, out))
        current = out
    if effects.get("glow"):
        a, b, halo, out = label(), label(), label(), label()
        chains.append("%ssplit%s%s" % (current, a, b))
        chains.append("%sscale=%d:%d,gblur=sigma=%.2f,lutrgb=r=min(val*1.15\\,255):g=min(val*1.15\\,255):b=min(val*1.15\\,255),"
                      "scale=%d:%d,format=gbrp%s" % (b, WIDTH // 4, HEIGHT // 4, 14.0 * HEIGHT / 540 / 4 / 1.5, WIDTH, HEIGHT, halo))
        chains.append("%s%sblend=all_mode=screen:all_opacity=%.4f%s" % (a, halo, 0.75 * effects["glow"], out))
        current = out
    if effects.get("leak"):
        w, h = WIDTH // 4, HEIGHT // 4
        light, out = label(), label()
        chains.append("color=black:s=%dx%d:r=30,format=rgb24,geq=r='%s':g='%s':b='%s',scale=%d:%d,format=gbrp%s" % (
            w, h, leak_expression(effects["leak"], 1.0, w, h), leak_expression(effects["leak"], 0.43, w, h),
            leak_expression(effects["leak"], 0.16, w, h), WIDTH, HEIGHT, light))
        chains.append("%s%sblend=all_mode=screen:shortest=1%s" % (current, light, out))
        current = out
    if effects.get("vignette"):
        mask_in = image_input(write_ppm(os.path.join(work_dir, "vignette.ppm"), vignette_mask(effects["vignette"])))
        mask, out = label(), label()
        chains.append("%sformat=gbrp%s" % (mask_in, mask))
        chains.append("%s%sblend=all_mode=multiply:shortest=1%s" % (current, mask, out))
        current = out
    if effects.get("vhs"):
        mask_in = image_input(write_ppm(os.path.join(work_dir, "scanlines.ppm"), scanline_mask(effects["vhs"])))
        mask, out = label(), label()
        chains.append("%sformat=gbrp%s" % (mask_in, mask))
        chains.append("%s%sblend=all_mode=multiply:shortest=1%s" % (current, mask, out))
        current = out
    # Back to Rec. 709 YUV, then grain on the brightness only (the shader adds one value to r, g and b).
    noise = effects.get("grain", 0) * 0.16 + effects.get("vhs", 0) * 0.05
    tail = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
    if noise:
        tail += ",noise=c0s=%d:c0f=t+u" % max(1, int(round(noise * 255 / 2)))
    chains.append("%s%s[video]" % (current, tail))
    return inputs, ";".join(chains)
