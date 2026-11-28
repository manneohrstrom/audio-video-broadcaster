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

The effects added 2026-11-17 (user: "the ones that give a cinematic look"), same rule:
  colour pop   in the colour table (grade.colour_pop), so it is exact          lut3d
  denoise      in the decoders, before the look; temporal, so not in the preview  hqdn3d
  clarity      local contrast on brightness: picture + k x (picture - big blur)
               (quarter-size blur; unsharp is capped at 13 px on the hub)     gblur + lut2
  pulse        a zoom punch on each downbeat (beats.json), decaying
               (per-frame scale then crop breaks: a graph can't change size)   zoompan
  projector    gate weave, whole pixels, a fixed pattern of the frame number    crop + pad
               and a brightness flicker (same pattern)                        hue
  halation     red-orange halo around the brightest things (film's signature) gblur + blend
  trails       bright things leave trails: max(frame, previous x decay)       lagfun
  letterbox    black bars to 1.78-2.39:1, last (after the grain)               drawbox

Why ffmpeg and not numpy (2026-11-16): per-frame numpy effects on 1080p RGB needed up to
~270 MB in the render's Python process and ran the 1 GB hub out of memory during a
dissolve; ffmpeg's filters run in C, multithreaded, on frames it already holds.
Masks are computed once with numpy and written as .ppm images.
"""
import json
import os

import numpy as np

import grade

WIDTH, HEIGHT = 1920, 1080
EFFECTS = ("grain", "vignette", "leak", "glow", "vhs", "halation", "letterbox", "projector", "clarity", "trails",
           "colorpop", "pulse", "denoise")


def look_settings(look, looks):
    """(adjust settings, filter settings, filter strength) for a saved look."""
    look = look or {}
    adjust = grade.adjustment_settings(look.get("adjust"), looks.get("adjustments"))
    filter_settings = next((f["grade"] for f in looks.get("filters", []) if f["id"] == look.get("filter")), {})
    return adjust, filter_settings, float(look.get("filter_strength", 1))


def write_look_lut(path, look, looks):
    """The look's colour (with colour pop) as a .cube file; None if it changes nothing."""
    adjust, filter_settings, strength = look_settings(look, looks)
    neutral = grade.adjustment_settings({}, looks.get("adjustments"))
    pop = active_effects(look).get("colorpop", 0)
    if (not filter_settings or strength == 0) and adjust == neutral and not pop:
        return None
    grade.write_cube(path, filter_settings, strength, adjust=adjust, pop=pop)
    return path


def active_effects(look):
    """The look's effects that are on: built-in ones and a film overlay ("overlay:<id>")."""
    return {name: float(value) for name, value in ((look or {}).get("effects") or {}).items()
            if value and (name in EFFECTS or name.startswith("overlay:"))}


OVERLAY_BLEND = {"overlay": "overlay", "screen": "screen", "soft-light": "softlight", "multiply": "multiply"}


def film_overlay(effects, overlays_dir):
    """(clip path, ffmpeg blend mode, strength) of the look's film overlay, or None."""
    for name, strength in effects.items():
        if not name.startswith("overlay:"):
            continue
        folder = os.path.join(overlays_dir, name.split(":", 1)[1])
        try:
            with open(os.path.join(folder, "overlay.json")) as f:
                info = json.load(f)
        except (OSError, ValueError):
            return None                               # removed since: render without it
        return os.path.join(folder, "clip.mp4"), OVERLAY_BLEND.get(info.get("mode"), "overlay"), strength
    return None


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


# The shader's fixed patterns of the song's frame number n (the render's n starts at song
# time 0, the shader's is round(seconds x 30)): gate weave in pixels, brightness flicker.
def weave_x(strength):
    return "%.4f*(0.6*sin(n*0.73)+0.4*sin(n*1.91+1.3))" % (1.6 * strength)


def weave_y(strength):
    return "%.4f*(0.6*sin(n*0.57+0.4)+0.4*sin(n*2.33))" % (1.0 * strength)


def flicker(strength):
    """The shader's flicker as a geq expression of the frame number N: 128 + the RGB levels
    to add (0.035 x strength x pattern, x 255), for blend grainmerge (A + B - 128).
    Done in RGB like the shader. Tried first, 2026-11-18: eq's brightness isn't even monotonic
    on a dark picture (b = 0.01 came out darker than 0), and hue's b works in whole Y levels
    with a bias of about +1 RGB level."""
    return "128+round(%.4f*(0.6*sin(N*2.17)+0.4*sin(N*5.71+0.8)))" % (0.035 * 255 * strength)


def letterbox_bar_px(strength, height=HEIGHT):
    """Height of each bar: 16:9 at 0, 2.39:1 at 1; even (4:2:0)."""
    aspect = 16 / 9 + (2.39 - 16 / 9) * strength
    return 2 * int(round(height * (1 - (16 / 9) / aspect) / 4))


PULSE_ZOOM = 0.045          # zoom at a downbeat at full strength
PULSE_DECAY_S = 0.15        # e-folding time of the punch
PULSE_LENGTH_S = 0.5


def pulse_zoom(strength, downbeats_s, audio_start_s):
    """The zoom factor as an expression of it (zoompan's input seconds, video t): 1 + a punch
    after each downbeat. downbeats_s: bounce seconds; audio_start_s: bounce seconds at t = 0."""
    terms = ["if(between(it+%.4f,%.3f,%.3f),exp(-(it+%.4f-%.3f)/%.3f),0)"
             % (audio_start_s, d, d + PULSE_LENGTH_S, audio_start_s, d, PULSE_DECAY_S)
             for d in downbeats_s if d + PULSE_LENGTH_S >= audio_start_s]
    return "(1+%.4f*(%s))" % (PULSE_ZOOM * strength, balanced_sum(terms))


def balanced_sum(terms):
    """terms added up as a balanced tree, "((a+b)+(c+d))": ffmpeg's expressions refuse a plain
    chain "a+b+c+..." of more than 94 terms (2026-11-28: a 12-minute song has 302 downbeats,
    and its render failed at the start with "Failed to configure output pad on zoompan")."""
    if not terms:
        return "0"
    if len(terms) == 1:
        return terms[0]
    half = len(terms) // 2
    return "(%s+%s)" % (balanced_sum(terms[:half]), balanced_sum(terms[half:]))


def effect_filter_graph(effects, work_dir, first_extra_input, overlays_dir="/var/lib/hubd/overlays",
                        downbeats_s=(), audio_start_s=0.0):
    """(extra ffmpeg inputs, filter_complex text) turning [0:v] into [video] with the effects.
    first_extra_input: the input number the first extra input (a mask image) will get.
    downbeats_s, audio_start_s: for the beat pulse (bounce seconds; bounce seconds at t = 0)."""
    inputs, chains = [], []
    current = "[0:v]"
    step = [0]

    def label():
        step[0] += 1
        return "[e%d]" % step[0]

    def clip_input(path):
        """A film overlay clip, looped for as long as the video runs."""
        inputs.extend(["-stream_loop", "-1", "-i", path])
        return "[%d:v]" % (first_extra_input + count_inputs() - 1)

    def count_inputs():
        return sum(1 for part in inputs if part == "-i")

    def image_input(path):
        # One frame, no -loop: blend repeats a finished input's last frame (eof_action
        # repeat). A looping image input made ffmpeg decode copies ahead: +250 MB per
        # mask on the 1 GB hub (2026-11-16).
        inputs.extend(["-i", path])
        return "[%d:v]" % (first_extra_input + count_inputs() - 1)

    # On the decoded YUV picture: clarity, the beat pulse, the projector's weave.
    if effects.get("clarity"):
        a, b, blurred, out = label(), label(), label(), label()
        chains.append("%ssplit%s%s" % (current, a, b))
        chains.append("%sscale=%d:%d,gblur=sigma=4,scale=%d:%d%s" % (b, WIDTH // 4, HEIGHT // 4, WIDTH, HEIGHT, blurred))
        chains.append("%s%slut2=c0='clip(x+%.4f*(x-y),16,235)':c1=x:c2=x%s" % (a, blurred, 1.2 * effects["clarity"], out))
        current = out
    # zoompan crops whole pixels, so the punch matches the preview to about a pixel (PSNR ~31 dB
    # on a real frame, 2026-11-18). On yuv444p it would be ~35 dB but 3x slower on the hub
    # (20 vs 6 ms a frame): not visible in a half-second punch, so it stays on 4:2:0.
    if effects.get("pulse") and downbeats_s:
        zoom = pulse_zoom(effects["pulse"], downbeats_s, audio_start_s)
        out = label()
        chains.append("%szoompan=z='%s':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=%dx%d:fps=%d%s"
                      % (current, zoom, WIDTH, HEIGHT, 30, out))
        current = out
    if effects.get("projector"):
        out = label()
        # exact=1: without it crop rounds x and y down to even numbers on 4:2:0 video, and
        # the weave would move in 2-pixel steps (the preview moves in single pixels).
        chains.append("%scrop=%d:%d:x='4+round(%s)':y='4+round(%s)':exact=1,pad=%d:%d:4:4,fillborders=4:4:4:4:smear%s"
                      % (current, WIDTH - 8, HEIGHT - 8, weave_x(effects["projector"]), weave_y(effects["projector"]),
                         WIDTH, HEIGHT, out))
        current = out
    out = label()
    # The decoded picture is Rec. 709, limited range, but raw video from a pipe carries no tags,
    # and swscale then assumes Rec. 601: with any effect on, a warm red came out 11 Y levels
    # darker than without (found 2026-11-18). Say it, so the round trip to RGB keeps the colours.
    chains.append("%sscale=in_color_matrix=bt709:in_range=tv,format=gbrp%s" % (current, out))
    current = out
    if effects.get("vhs"):
        shift = max(1, int(round(3 * effects["vhs"])))
        out = label()
        chains.append("%srgbashift=rh=%d:bh=%d%s" % (current, -shift, shift, out))
        current = out
    if effects.get("halation"):
        # The brightest parts (smoothstep of brightness 0.6..1), blurred, tinted red-orange, screened.
        a, b, halo, out = label(), label(), label(), label()
        k = 1.4 * effects["halation"]
        bright = "255*st(0,clip((val/255-0.6)/0.4,0,1))*ld(0)*(3-2*ld(0))"
        chains.append("%ssplit%s%s" % (current, a, b))
        chains.append("%scolorchannelmixer=rr=0.2126:rg=0.7152:rb=0.0722:gr=0.2126:gg=0.7152:gb=0.0722:"
                      "br=0.2126:bg=0.7152:bb=0.0722,lutrgb=r='%s':g='%s':b='%s',scale=%d:%d,gblur=sigma=2.5,scale=%d:%d,"
                      "colorchannelmixer=rr=%.4f:gg=%.4f:bb=%.4f,format=gbrp%s"
                      % (b, bright, bright, bright, WIDTH // 4, HEIGHT // 4, WIDTH, HEIGHT, k, 0.38 * k, 0.12 * k, halo))
        chains.append("%s%sblend=all_mode=screen%s" % (a, halo, out))
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
        chains.append("%s%sblend=all_mode=multiply%s" % (current, mask, out))
        current = out
    film = film_overlay(effects, overlays_dir)
    if film:                                     # after the vignette, as in the editor's shader
        clip_in = clip_input(film[0])
        layer, out = label(), label()
        chains.append("%sscale=%d:%d,format=gbrp%s" % (clip_in, WIDTH, HEIGHT, layer))
        chains.append("%s%sblend=all_mode=%s:all_opacity=%.4f:shortest=1%s" % (current, layer, film[1], film[2], out))
        current = out
    if effects.get("vhs"):
        mask_in = image_input(write_ppm(os.path.join(work_dir, "scanlines.ppm"), scanline_mask(effects["vhs"])))
        mask, out = label(), label()
        chains.append("%sformat=gbrp%s" % (mask_in, mask))
        chains.append("%s%sblend=all_mode=multiply%s" % (current, mask, out))
        current = out
    if effects.get("trails"):
        out = label()
        chains.append("%slagfun=decay=%.4f%s" % (current, 0.80 + 0.17 * effects["trails"], out))
        current = out
    if effects.get("projector"):
        # The flicker: a tiny grey frame per frame (geq on 8x8 is cheap), scaled up and added.
        level, out = label(), label()
        expression = flicker(effects["projector"])
        chains.append("color=gray:s=8x8:r=30,format=gbrp,geq=r='%s':g='%s':b='%s',scale=%d:%d:flags=neighbor,format=gbrp%s"
                      % (expression, expression, expression, WIDTH, HEIGHT, level))
        chains.append("%s%sblend=all_mode=grainmerge:shortest=1%s" % (current, level, out))
        current = out
    # Back to Rec. 709 YUV; grain on the brightness only (the shader
    # adds one value to r, g and b); the letterbox bars last, so they stay black.
    noise = effects.get("grain", 0) * 0.16 + effects.get("vhs", 0) * 0.05
    tail = "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
    if noise:
        tail += ",noise=c0s=%d:c0f=t+u" % max(1, int(round(noise * 255 / 2)))
    if effects.get("letterbox"):
        bar = letterbox_bar_px(effects["letterbox"])
        if bar:
            tail += ",drawbox=x=0:y=0:w=iw:h=%d:color=black:t=fill,drawbox=x=0:y=ih-%d:w=iw:h=%d:color=black:t=fill" % (bar, bar, bar)
    chains.append("%s%s[video]" % (current, tail))
    return inputs, ";".join(chains)
