// Chooses which cameras to decode and keeps their decoders where they need to be.
//
// Each frame the video window asks for pictures: "camera X at song time t". The player
// keeps one CameraDecoder per camera (opened the first time it's needed) and:
//   - while paused (scrubbing, skimming), seeks the decoder to the asked-for frame, at
//     most one seek in flight per camera, the latest request winning;
//   - while playing, keeps EVERY camera decoding along with the playhead (keepAllRunning),
//     so switching camera mid-play is instant: a seek through the hub takes up to a second
//     (2026-11-16: switching during playback froze the picture). Spike E2: the Mac decodes
//     4-5 cameras at once; ~35 Mbit/s from the hub for four.
// Until a camera has a decoded frame, pictureFor() returns null and the window falls back
// to the strip thumbnail.
import { CameraDecoder } from "./camera-decoder.js";
import { cutIndexAt } from "../edit-model.js";

const PREROLL_SECONDS = 1.5;
const SEEK_SETTLE_MS = 60;        // while scrubbing, wait this long between seeks of one camera
const PLAYING_SEEK_SETTLE_MS = 300;
// A seek takes a moment (fetch a group of pictures, decode up to the frame). While playing,
// aim this far ahead so the frame is ready when the playhead gets there.
const PLAYING_SEEK_LEAD_SECONDS = 0.3;

/**
 * Browsers only offer WebCodecs on secure pages (HTTPS or localhost). The hub is plain
 * http://studio-hub.local, so it needs Chrome's "treat insecure origin as secure" flag for
 * it (as the phone page does for the camera), or HTTPS on the hub.
 */
export const fullVideoAvailable = typeof VideoDecoder === "function";

export class VideoPlayer {
  constructor(store) {
    this.store = store;
    // All cameras can decode at once only with proxies (full-quality files need ~60 Mbit/s for four).
    this.allProxies = store.cameras.every(camera => camera.proxy_url);
    this.decoders = new Map();    // camera id -> CameraDecoder
    this.lastSeek = new Map();    // camera id -> time of the last seek (ms)
  }

  decoder(camera) {
    let decoder = this.decoders.get(camera.id);
    if (!decoder) {
      decoder = new CameraDecoder(camera.proxy_url || camera.file_url);   // the small editing proxy when there is one
      this.decoders.set(camera.id, decoder);
      decoder.open().catch(() => {});      // the problem is kept on the decoder
    }
    return decoder;
  }

  /** The decoded VideoFrame of `camera` at song time `seconds`, or null (not ready yet). */
  pictureFor(camera, seconds, now) {
    if (!fullVideoAvailable || !this.store.isRecording(camera, seconds)) return null;
    const decoder = this.decoder(camera);
    if (!decoder.ready || decoder.problem) return null;
    const fileSeconds = this.store.cameraFileSeconds(camera, seconds);
    this.keepNear(camera, decoder, fileSeconds, now);
    const frame = decoder.frameAt(fileSeconds);
    // While a seek is still decoding, the frame on screen may be from somewhere else: only
    // use it if it's within a frame or two of the frame that belongs here, the latest one
    // not after this time. Measured from that frame, not from the time itself: inside
    // dropped frames the right picture is the last frame before them, maybe seconds back.
    if (frame && !this.store.isPlaying) {
      const wanted = decoder.frameIndexAt(Math.round(fileSeconds * 1e6) + 1);
      const wantedSeconds = wanted >= 0 ? decoder.samples[wanted].timeUs / 1e6 : fileSeconds;
      if (Math.abs(frame.timestamp / 1e6 - wantedSeconds) > 0.07) return null;
    }
    return frame;
  }

  keepNear(camera, decoder, fileSeconds, now) {
    const playing = this.store.isPlaying;
    if (decoder.isNear(fileSeconds, playing)) return;
    const last = this.lastSeek.get(camera.id) || 0;
    if (now - last < (playing ? PLAYING_SEEK_SETTLE_MS : SEEK_SETTLE_MS)) return;
    this.lastSeek.set(camera.id, now);
    decoder.seek(playing ? fileSeconds + PLAYING_SEEK_LEAD_SECONDS : fileSeconds);
  }

  /** While playing: every recording camera follows the playhead (its frames are consumed too). */
  keepAllRunning(seconds, now) {
    if (!fullVideoAvailable || !this.store.isPlaying || !this.allProxies) return;
    for (const camera of this.store.cameras) {
      if (this.store.isRecording(camera, seconds)) this.pictureFor(camera, seconds, now);
    }
  }

  /** While playing: start the next shot's camera early so it's decoded when the cut comes. */
  prerollNextShot(now) {
    const store = this.store;
    if (!fullVideoAvailable || !store.isPlaying) return;
    const index = cutIndexAt(store.cuts, store.playheadSeconds);
    const next = store.cuts[index + 1];
    if (!next) return;
    const startShowing = next.atSeconds - store.dissolveSeconds / 2;
    if (startShowing - store.playheadSeconds > PREROLL_SECONDS) return;
    const camera = store.camerasById.get(next.cameraId);
    if (!store.isRecording(camera, startShowing)) return;
    const decoder = this.decoder(camera);
    if (!decoder.ready) return;
    const fileSeconds = store.cameraFileSeconds(camera, Math.max(startShowing, store.playheadSeconds));
    if (decoder.targetUs === null || Math.abs(decoder.targetUs / 1e6 - fileSeconds) > PREROLL_SECONDS + 0.5) {
      this.lastSeek.set(camera.id, now);
      decoder.seek(fileSeconds);
    }
  }

  close() {
    for (const decoder of this.decoders.values()) decoder.close();
    this.decoders.clear();
  }
}
