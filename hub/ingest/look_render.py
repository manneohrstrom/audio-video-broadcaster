"""The song's look in the render: the same as the editor's preview, in numpy.

The editor shows the look with WebGL (hub/hubd/static/editor/js/look/look-renderer.js).
The render must give the same picture, so this file follows that shader step by step:

  colour   Adjust sliders, then the filter at its strength, as a 33^3 colour table
           (grade.py, the same maths as the editor's grade.js) applied by ffmpeg's lut3d
           with trilinear interpolation, like the WebGL 3D texture
  vhs      red shifted right, blue left (3 px x strength at 1080p)
  glow     the picture blurred on a ring (radius 14 px at 540 lines), screened on top
  leak     warm light from the left, drifting slowly with song time
  vignette darker towards the corners
  vhs      scanlines (every third row)
  grain    random noise per frame, the same amount (not the same grains)

Effects run on RGB frames (H x W x 3, uint8) in that order. Effects that are off cost
nothing: with no effects the render doesn't even convert to RGB.
"""
import numpy as np

import grade

EFFECT_ORDER = ("vhs_shift", "glow", "leak", "vignette", "scanlines", "grain")


def look_settings(look, looks):
    """(adjust settings, filter settings, filter strength) for a saved look."""
    look = look or {}
    adjust = grade.adjustment_settings(look.get("adjust"), looks.get("adjustments"))
    filter_settings = next((f["grade"] for f in looks.get("filters", []) if f["id"] == look.get("filter")), {})
    return adjust, filter_settings, float(look.get("filter_strength", 1))


def write_look_lut(path, look, looks):
    """The look's colour as a .cube file; None if it changes nothing (no table needed)."""
    adjust, filter_settings, strength = look_settings(look, looks)
    if not filter_settings and all(v == grade.adjustment_settings({}, looks.get("adjustments")).get(k)
                                   for k, v in adjust.items()):
        return None
    grade.write_cube(path, filter_settings, strength, adjust=adjust)
    return path


def active_effects(look):
    return {name: float(value) for name, value in ((look or {}).get("effects") or {}).items()
            if value and name in ("grain", "vignette", "leak", "glow", "vhs")}


def smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class EffectProcessor:
    """Applies the look's effects to RGB frames, like the editor's shader."""

    def __init__(self, effects, width=1920, height=1080, fps=30, seed=1):
        self.effects = effects
        self.width, self.height, self.fps = width, height, fps
        self.aspect = width / height
        # Pixel centres in the shader's uv space: x 0..1 left to right, y 0..1 bottom to top.
        x = (np.arange(width, dtype=np.float32) + 0.5) / width
        y = 1.0 - (np.arange(height, dtype=np.float32) + 0.5) / height
        self.uv_x, self.uv_y = np.meshgrid(x, y)
        if "vignette" in effects:
            distance = np.hypot(self.uv_x - 0.5, self.uv_y - 0.5) / 0.7071
            self.vignette_mask = (1.0 - effects["vignette"] * 0.9 * smoothstep(0.45, 1.0, distance))[..., None]
        if "vhs" in effects:
            rows_from_bottom = (height - 1 - np.arange(height)) + 0.5
            darker = (np.mod(rows_from_bottom / 3.0, 1.0) >= 0.5).astype(np.float32)
            self.scanline_mask = (1.0 - effects["vhs"] * 0.35 * darker)[:, None, None]
        noise_amount = effects.get("grain", 0) * 0.16 + effects.get("vhs", 0) * 0.05
        if noise_amount:
            rng = np.random.default_rng(seed)
            # A bank of noise frames, cycled: per-frame random noise without the cost.
            self.noise_bank = [((rng.random((height, width), dtype=np.float32) - 0.5) * noise_amount)[..., None]
                               for _ in range(8)]

    def process(self, frame, frame_number):
        if not self.effects:
            return frame
        c = frame.astype(np.float32) / 255.0
        effects = self.effects
        seconds = frame_number / self.fps
        if "vhs" in effects:
            shift = max(1, int(round(3 * effects["vhs"] * self.width / 1920)))
            red, blue = c[..., 0].copy(), c[..., 2].copy()
            c[:, :-shift, 0] = red[:, shift:]                  # red from the right
            c[:, shift:, 2] = blue[:, :-shift]                 # blue from the left
        if "glow" in effects:
            c = self.glow(c, effects["glow"])
        if "leak" in effects:
            # A soft gradient: computed at quarter size (same formula), then enlarged.
            drift = 0.05 + 0.1 * np.sin(seconds * 0.3)
            distance = np.hypot((self.uv_x[::4, ::4] - drift) * self.aspect, self.uv_y[::4, ::4] - 0.3)
            amount = effects["leak"] * (0.45 + 0.2 * np.sin(seconds * 0.6)) * smoothstep(0.55 * self.aspect, 0.0, distance)
            amount = np.repeat(np.repeat(amount, 4, axis=0), 4, axis=1)[: self.height, : self.width]
            light = np.array([1.0, 0.43, 0.16], dtype=np.float32)
            c = 1.0 - (1.0 - c) * (1.0 - light * amount[..., None])
        if "vignette" in effects:
            c *= self.vignette_mask
        if "vhs" in effects:
            c *= self.scanline_mask
        if hasattr(self, "noise_bank"):
            c += self.noise_bank[frame_number % len(self.noise_bank)]
        return (np.clip(c, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)

    def glow(self, c, strength):
        """12 samples on a ring (radius 14 px at 540 lines), averaged, screened on top.
        Done at quarter size for speed: the halo is soft anyway."""
        small = c[: self.height // 4 * 4, : self.width // 4 * 4].reshape(self.height // 4, 4, self.width // 4, 4, 3).mean(axis=(1, 3))
        radius = 14.0 * self.height / 540.0 / 4.0
        halo = np.zeros_like(small)
        for i in range(12):
            angle = i * 0.5236
            dx, dy = int(round(np.cos(angle) * radius)), int(round(np.sin(angle) * radius))
            halo += np.roll(np.roll(small, dy, axis=0), dx, axis=1)
        halo = halo / 12.0 * 1.15
        halo = np.repeat(np.repeat(halo, 4, axis=0), 4, axis=1)
        full = np.zeros_like(c)
        full[: halo.shape[0], : halo.shape[1]] = halo
        return 1.0 - (1.0 - c) * (1.0 - full * 0.75 * strength)
