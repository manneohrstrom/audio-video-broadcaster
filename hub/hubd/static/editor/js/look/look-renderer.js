// Shows a picture through the song's look with WebGL2: the filter as a 3D colour table
// (grade.js, the same table the render gives ffmpeg), then the effects in the shader.
//
// Effects, and how the render does the same (hub/ingest/look_render.py):
//   vignette  darkens towards the corners: the same formula, as a mask image in the render
//   grain     random noise per frame, same amount (not the same grains)
//   leak      warm light drifting in from the left edge: the same formula, slowly moving
//   glow      a soft bright halo: a blur of the picture added on top (screen)
//   vhs       red/blue shifted sideways, scanlines, a little noise
// Added 2026-11-17 (the cinematic set), same formulas as look_render.py:
//   pulse     a zoom punch on each downbeat (setDownbeats), decaying       render: zoompan
//   projector gate weave in whole 1080p pixels + brightness flicker,
//             fixed patterns of the song's frame number                    render: crop/pad, eq
//   clarity   brightness + k x (brightness - big blur)                     render: gblur + lut2
//   halation  red-orange halo around the brightest things                  render: gblur + blend
//   trails    max(this frame, previous frame x decay)                      render: lagfun
//   letterbox black bars, 16:9 at 0 to 2.39:1 at 1, last                   render: drawbox
//   colour pop is in the colour table (grade.js), denoise is render only (temporal).
// Every effect has a strength 0..1; 0 (or absent) means off.
import { buildLut, LUT_SIZE, adjustmentSettings } from "./grade.js";

const VERTEX_SHADER = `#version 300 es
in vec2 corner;
out vec2 uv;
void main() {
  uv = corner * 0.5 + 0.5;
  gl_Position = vec4(corner, 0.0, 1.0);
}`;

const FRAGMENT_SHADER = `#version 300 es
precision highp float;
precision highp sampler3D;
in vec2 uv;
out vec4 colour;
uniform sampler2D picture;
uniform sampler3D lut;
uniform float grain, vignette, leak, glow, vhs, seconds, frameNumber;
uniform float halation, letterbox, projector, clarity, trails, pulseZoom, trailsReady;
uniform sampler2D previousFrame;       // the last frame drawn (light trails)
uniform vec2 size;
uniform sampler2D overlayPicture;      // a film overlay clip (grain, dust…), looping
uniform float overlayStrength;         // 0 = none
uniform int overlayMode;               // 0 overlay, 1 screen, 2 soft-light, 3 multiply

float random(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

// The render does the same with ffmpeg's blend filter (picture on top, overlay below).
vec3 blendOverlay(vec3 b, vec3 s, int mode) {
  if (mode == 1) return b + s - b * s;
  if (mode == 3) return b * s;
  if (mode == 2) return mix(b - b * (1.0 - 2.0 * s) * (1.0 - b), b + (2.0 * s - 1.0) * (sqrt(b) - b), step(0.5, s));
  return mix(2.0 * b * s, 1.0 - 2.0 * (1.0 - b) * (1.0 - s), step(0.5, b));
}

vec3 graded(vec2 at) {
  vec3 c = texture(picture, vec2(at.x, 1.0 - at.y)).rgb;
  float n = float(${LUT_SIZE});
  return texture(lut, c * ((n - 1.0) / n) + 0.5 / n).rgb;     // sample texel centres
}

const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
const vec2 PX = vec2(1.0 / 1920.0, 1.0 / 1080.0);   // one 1080p pixel in uv

// Average of 8 taps on a ring of radius (1080p pixels) around at.
vec3 ring(vec2 at, float radius) {
  vec3 sum = vec3(0.0);
  for (int i = 0; i < 8; i++) {
    float a = float(i) * 0.7854;
    sum += graded(at + vec2(cos(a), sin(a)) * radius * PX);
  }
  return sum / 8.0;
}

float bright(vec3 c) { return smoothstep(0.6, 1.0, dot(c, LUMA)); }

void main() {
  float songFrame = floor(seconds * 30.0 + 0.5);
  vec2 at = uv;
  if (pulseZoom > 1.0) at = 0.5 + (at - 0.5) / pulseZoom;
  if (projector > 0.0) {
    float dx = round(1.6 * projector * (0.6 * sin(songFrame * 0.73) + 0.4 * sin(songFrame * 1.91 + 1.3)));
    float dy = round(1.0 * projector * (0.6 * sin(songFrame * 0.57 + 0.4) + 0.4 * sin(songFrame * 2.33)));
    at += vec2(dx, -dy) * PX;
  }
  vec3 c;
  if (vhs > 0.0) {
    float shift = 3.0 * vhs / size.x;
    c = vec3(graded(at + vec2(shift, 0.0)).r, graded(at).g, graded(at - vec2(shift, 0.0)).b);
  } else {
    c = graded(at);
  }
  if (clarity > 0.0) {                     // local contrast on brightness: a big blur subtracted
    vec3 blur = 0.2 * c + 0.4 * ring(at, 12.0) + 0.4 * ring(at, 28.0);
    c += 1.2 * clarity * (dot(c, LUMA) - dot(blur, LUMA));
  }
  if (halation > 0.0) {                    // the brightest parts, blurred, tinted red-orange, screened
    float halo = 0.2 * bright(graded(at)) + 0.4 * bright(ring(at, 6.0)) + 0.4 * bright(ring(at, 14.0));
    c = 1.0 - (1.0 - c) * (1.0 - vec3(1.0, 0.38, 0.12) * halo * 1.4 * halation);
  }
  if (glow > 0.0) {                        // a cheap blur: 12 taps on a ring, screened on top
    vec3 halo = vec3(0.0);
    for (int i = 0; i < 12; i++) {
      float a = float(i) * 0.5236;
      halo += graded(at + vec2(cos(a), sin(a)) * vec2(14.0 / size.x, 14.0 / size.y) * size.y / 540.0);
    }
    halo = halo / 12.0 * 1.15;
    c = 1.0 - (1.0 - c) * (1.0 - halo * 0.75 * glow);
  }
  if (leak > 0.0) {                        // warm light from the left, drifting slowly
    float drift = 0.05 + 0.1 * sin(seconds * 0.3);
    float d = distance(uv * vec2(size.x / size.y, 1.0), vec2(drift * size.x / size.y, 0.3));
    float amount = leak * (0.45 + 0.2 * sin(seconds * 0.6)) * smoothstep(0.55 * size.x / size.y, 0.0, d);
    c = 1.0 - (1.0 - c) * (1.0 - vec3(1.0, 0.43, 0.16) * amount);
  }
  if (vignette > 0.0) {
    float d = distance(uv, vec2(0.5)) / 0.7071;            // 0 centre .. 1 corner
    c *= 1.0 - vignette * 0.9 * smoothstep(0.45, 1.0, d);
  }
  if (overlayStrength > 0.0) {
    vec3 film = texture(overlayPicture, vec2(uv.x, 1.0 - uv.y)).rgb;
    c = mix(c, blendOverlay(c, film, overlayMode), overlayStrength);
  }
  if (vhs > 0.0) {
    c *= 1.0 - vhs * 0.35 * step(0.5, fract(gl_FragCoord.y / 3.0));
  }
  if (trails > 0.0 && trailsReady > 0.0) {
    c = max(c, texture(previousFrame, uv).rgb * (0.80 + 0.17 * trails));
  }
  if (projector > 0.0) {
    c += 0.035 * projector * (0.6 * sin(songFrame * 2.17) + 0.4 * sin(songFrame * 5.71 + 0.8));
  }
  if (grain > 0.0 || vhs > 0.0) {
    float n = random(floor(gl_FragCoord.xy) + frameNumber * 17.13) - 0.5;
    c += n * (grain * 0.16 + vhs * 0.05);
  }
  if (letterbox > 0.0) {
    float aspect = 1.7778 + (2.39 - 1.7778) * letterbox;
    float bar = 2.0 * floor(1080.0 * (1.0 - 1.7778 / aspect) / 4.0 + 0.5) / 1080.0;
    if (uv.y < bar || uv.y > 1.0 - bar) c = vec3(0.0);
  }
  colour = vec4(clamp(c, 0.0, 1.0), 1.0);
}`;

export class LookRenderer {
  constructor(canvas) {
    const gl = canvas.getContext("webgl2", { preserveDrawingBuffer: true });
    if (!gl) throw new Error("this browser has no WebGL2");
    this.canvas = canvas;
    this.gl = gl;
    this.program = this.link(VERTEX_SHADER, FRAGMENT_SHADER);
    gl.useProgram(this.program);
    const corners = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, corners);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const location = gl.getAttribLocation(this.program, "corner");
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
    this.pictureTexture = this.makeTexture(gl.TEXTURE_2D);
    this.lutTexture = this.makeTexture(gl.TEXTURE_3D);
    this.overlayTexture = this.makeTexture(gl.TEXTURE_2D);
    this.previousTexture = this.makeTexture(gl.TEXTURE_2D);
    this.uniforms = Object.fromEntries(["picture", "lut", "grain", "vignette", "leak", "glow", "vhs", "seconds", "frameNumber", "size",
                                        "overlayPicture", "overlayStrength", "overlayMode", "halation", "letterbox", "projector",
                                        "clarity", "trails", "pulseZoom", "trailsReady", "previousFrame"]
      .map(name => [name, gl.getUniformLocation(this.program, name)]));
    gl.uniform1i(this.uniforms.picture, 0);
    gl.uniform1i(this.uniforms.lut, 1);
    gl.uniform1i(this.uniforms.overlayPicture, 2);
    gl.uniform1i(this.uniforms.previousFrame, 3);
    this.lutKey = null;
    this.frameNumber = 0;
    this.downbeats = [];                 // song seconds of the bounce's bars (the beat pulse)
    this.previousSeconds = null;         // the time of the frame kept for light trails
  }

  /** The bounce's downbeats in song seconds (beats.json's, minus where the song starts in the bounce). */
  setDownbeats(songSeconds) {
    const list = songSeconds || [];
    const key = list.length ? `${list.length}:${list[0]}` : "";
    if (key === this.downbeatsKey) return;          // called every frame: only re-sort on a change
    this.downbeatsKey = key;
    this.downbeats = list.slice().sort((a, b) => a - b);
  }

  /** The beat pulse's zoom at `seconds`: 1 + a punch after the last downbeat (as look_render.pulse_zoom). */
  pulseZoomAt(seconds, strength) {
    if (!strength) return 1;
    let last = null;
    for (const beat of this.downbeats) { if (beat <= seconds) last = beat; else break; }
    if (last === null || seconds - last > 0.5) return 1;
    return 1 + 0.045 * strength * Math.exp(-(seconds - last) / 0.15);
  }

  link(vertexSource, fragmentSource) {
    const gl = this.gl;
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    const program = gl.createProgram();
    gl.attachShader(program, compile(gl.VERTEX_SHADER, vertexSource));
    gl.attachShader(program, compile(gl.FRAGMENT_SHADER, fragmentSource));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    return program;
  }

  makeTexture(target) {
    const gl = this.gl;
    const texture = gl.createTexture();
    gl.bindTexture(target, texture);
    gl.texParameteri(target, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(target, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    for (const wrap of [gl.TEXTURE_WRAP_S, gl.TEXTURE_WRAP_T, gl.TEXTURE_WRAP_R]) gl.texParameteri(target, wrap, gl.CLAMP_TO_EDGE);
    return texture;
  }

  /** The look's colour: Adjust sliders (with their definitions from looks.json), then the
   * filter, then the Colour pop effect (`pop`, its strength; 0 = off). */
  setLook(adjustSliders, adjustDefinitions, filterSettings, strength, pop = 0) {
    this.setFilter(filterSettings, strength, adjustmentSettings(adjustSliders, adjustDefinitions), pop);
  }

  /** Rebuild the colour table only when something in it changes. */
  setFilter(settings, strength, adjust = {}, pop = 0) {
    const key = JSON.stringify([settings, strength, adjust, pop]);
    if (key === this.lutKey) return;
    this.lutKey = key;
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_3D, this.lutTexture);
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGBA8, LUT_SIZE, LUT_SIZE, LUT_SIZE, 0, gl.RGBA, gl.UNSIGNED_BYTE,
                  buildLut(settings, strength, LUT_SIZE, adjust, pop));
  }

  /**
   * Draw `source` (a canvas, image or VideoFrame) through the filter and effects.
   * overlay: { video (a playing <video>), mode, strength } or null.
   */
  render(source, effects, seconds, overlay = null) {
    const gl = this.gl;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.pictureTexture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
    for (const name of ["grain", "vignette", "leak", "glow", "vhs", "halation", "letterbox", "projector", "clarity", "trails"]) {
      gl.uniform1f(this.uniforms[name], effects[name] || 0);
    }
    gl.uniform1f(this.uniforms.pulseZoom, this.pulseZoomAt(seconds, effects.pulse || 0));
    // Light trails need the frame before this one in time: not after a jump (seek, scrub).
    const follows = this.previousSeconds !== null && seconds >= this.previousSeconds && seconds - this.previousSeconds < 0.25;
    gl.uniform1f(this.uniforms.trailsReady, effects.trails && follows ? 1 : 0);
    gl.activeTexture(gl.TEXTURE3);
    gl.bindTexture(gl.TEXTURE_2D, this.previousTexture);
    gl.uniform1f(this.uniforms.seconds, seconds);
    gl.uniform1f(this.uniforms.frameNumber, this.frameNumber++ % 1000);
    gl.uniform2f(this.uniforms.size, this.canvas.width, this.canvas.height);
    const overlayReady = overlay && overlay.video && overlay.video.readyState >= 2;
    gl.uniform1f(this.uniforms.overlayStrength, overlayReady ? overlay.strength : 0);
    if (overlayReady) {
      gl.activeTexture(gl.TEXTURE2);
      gl.bindTexture(gl.TEXTURE_2D, this.overlayTexture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, overlay.video);
      gl.uniform1i(this.uniforms.overlayMode, { overlay: 0, screen: 1, "soft-light": 2, multiply: 3 }[overlay.mode] ?? 0);
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    if (effects.trails) {                  // keep this frame for the next one's trails
      gl.activeTexture(gl.TEXTURE3);
      gl.bindTexture(gl.TEXTURE_2D, this.previousTexture);
      gl.copyTexImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 0, 0, this.canvas.width, this.canvas.height, 0);
      this.previousSeconds = seconds;
    } else {
      this.previousSeconds = null;
    }
  }
}
