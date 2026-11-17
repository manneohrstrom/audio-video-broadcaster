// Play, pause and seek: keeps the store's playhead and the bounce player in step.
// While playing, the bounce is the clock and the playhead follows it every frame (tick).
// In Sync mode the bounce can move under the video; the player follows (see followBounce).
import { BouncePlayer } from "./media/bounce-player.js";

const FRAME_SECONDS = 1 / 30;

export class Transport {
  constructor(store) {
    this.store = store;
    this.player = new BouncePlayer(store.project.bounce.url, store.bounceOffsetSeconds);
  }

  async play() {
    const store = this.store;
    if (store.playheadSeconds >= store.songSeconds - 0.05) store.playheadSeconds = 0;
    store.isPlaying = true;
    store.notify();
    try {
      await this.player.play(store.playheadSeconds);
    } catch (error) {
      store.isPlaying = false;
      store.showToast(`The bounce can't play: ${error.message}`);
    }
  }

  pause() {
    const store = this.store;
    if (!store.isPlaying) return;
    store.playheadSeconds = this.player.pause(store.playheadSeconds);
    store.isPlaying = false;
    store.notify();
  }

  toggle() {
    if (this.store.isPlaying) this.pause(); else this.play();
  }

  /** Move the playhead; playback carries on from there if it was playing. */
  seek(seconds) {
    const store = this.store;
    const wasPlaying = store.isPlaying;
    if (wasPlaying) this.player.pause(store.playheadSeconds);
    store.playheadSeconds = Math.max(0, Math.min(store.songSeconds, seconds));
    if (wasPlaying) this.player.play(store.playheadSeconds);
    store.notify();
  }

  stepFrames(count) {
    this.seek(Math.round(this.store.playheadSeconds / FRAME_SECONDS) * FRAME_SECONDS + count * FRAME_SECONDS);
  }

  /** Called every animation frame: follow the bounce, stop at the end, keep the playhead in view. */
  tick() {
    const store = this.store;
    this.followBounce();
    if (!store.isPlaying || !this.player.isPlaying) return;
    store.playheadSeconds = this.player.currentSongSeconds(store.playheadSeconds);
    if (store.playheadSeconds >= store.songSeconds) {
      this.pause();
      store.playheadSeconds = store.songSeconds;
    }
    const visibleSeconds = store.timelineWidth / store.pixelsPerSecond;
    if (!store.fitWholeSong && store.playheadSeconds > store.viewStartSeconds + visibleSeconds * 0.92) {
      store.viewStartSeconds = store.playheadSeconds - visibleSeconds * 0.1;
      store.clampView();
    }
  }

  /** Sync mode: the bounce was moved. Play it from its new place, at the same song time;
   *  while it's being dragged, wait for the release (restarting every frame would stutter). */
  followBounce() {
    const store = this.store;
    const offset = store.bounceOffsetSeconds;
    if (this.player.offsetSeconds === offset || store.gesture?.kind === "moveBounce") return;
    if (!this.player.isPlaying) {
      this.player.offsetSeconds = offset;
      return;
    }
    const at = this.player.pause(store.playheadSeconds);
    this.player.offsetSeconds = offset;
    this.player.play(at);
  }
}
