// Shows a picture through the song's look with WebGL2: the filter as a 3D colour table
// (grade.js, the same table the render gives ffmpeg), then the effects in the shader.
//
// Effects, and how the render does the same (hub/ingest/look_render.py):
//   vignette  darkens towards the corners: the same formula, as a mask image in the render
//   grain     random noise per frame, same amount (not the same grains)
//   leak      warm light drifting in from the left edge: the same formula, slowly moving
//   glow      a soft bright halo: a blur of the picture added on top (screen)
//   vhs       red/blue shifted sideways, scanlines, a little noise
// Every effect has a strength 0..1; 0 (or absent) means off.
import { buildLut, LUT_SIZE } from "./grade.js";

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
uniform vec2 size;

float random(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

vec3 graded(vec2 at) {
  vec3 c = texture(picture, vec2(at.x, 1.0 - at.y)).rgb;
  float n = float(${LUT_SIZE});
  return texture(lut, c * ((n - 1.0) / n) + 0.5 / n).rgb;     // sample texel centres
}

void main() {
  vec3 c;
  if (vhs > 0.0) {
    float shift = 3.0 * vhs / size.x;
    c = vec3(graded(uv + vec2(shift, 0.0)).r, graded(uv).g, graded(uv - vec2(shift, 0.0)).b);
  } else {
    c = graded(uv);
  }
  if (glow > 0.0) {                        // a cheap blur: 12 taps on a ring, screened on top
    vec3 halo = vec3(0.0);
    for (int i = 0; i < 12; i++) {
      float a = float(i) * 0.5236;
      halo += graded(uv + vec2(cos(a), sin(a)) * vec2(14.0 / size.x, 14.0 / size.y) * size.y / 540.0);
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
  if (vhs > 0.0) {
    c *= 1.0 - vhs * 0.35 * step(0.5, fract(gl_FragCoord.y / 3.0));
  }
  if (grain > 0.0 || vhs > 0.0) {
    float n = random(floor(gl_FragCoord.xy) + frameNumber * 17.13) - 0.5;
    c += n * (grain * 0.16 + vhs * 0.05);
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
    this.uniforms = Object.fromEntries(["picture", "lut", "grain", "vignette", "leak", "glow", "vhs", "seconds", "frameNumber", "size"]
      .map(name => [name, gl.getUniformLocation(this.program, name)]));
    gl.uniform1i(this.uniforms.picture, 0);
    gl.uniform1i(this.uniforms.lut, 1);
    this.lutKey = null;
    this.frameNumber = 0;
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

  /** Rebuild the colour table only when the filter or its strength changes. */
  setFilter(settings, strength) {
    const key = JSON.stringify([settings, strength]);
    if (key === this.lutKey) return;
    this.lutKey = key;
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_3D, this.lutTexture);
    gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGBA8, LUT_SIZE, LUT_SIZE, LUT_SIZE, 0, gl.RGBA, gl.UNSIGNED_BYTE, buildLut(settings, strength));
  }

  /** Draw `source` (a canvas, image or VideoFrame) through the filter and effects. */
  render(source, effects, seconds) {
    const gl = this.gl;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.pictureTexture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
    for (const name of ["grain", "vignette", "leak", "glow", "vhs"]) gl.uniform1f(this.uniforms[name], effects[name] || 0);
    gl.uniform1f(this.uniforms.seconds, seconds);
    gl.uniform1f(this.uniforms.frameNumber, this.frameNumber++ % 1000);
    gl.uniform2f(this.uniforms.size, this.canvas.width, this.canvas.height);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}
