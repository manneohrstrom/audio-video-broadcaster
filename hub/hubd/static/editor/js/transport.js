// Play, pause and seek: keeps the store's playhead and the bounce player in step.
// While playing, the bounce is the clock and the playhead follows it every frame (tick).
import { BouncePlayer } from "./media/bounce-player.js";
import { NS_PER_SECOND } from "./time-format.js";

const FRAME_SECONDS = 1 / 30;

export class Transport {
  constructor(store) {
    this.store = store;
    const bounce = store.project.bounce;
    this.player = new BouncePlayer(bounce.url, (store.rangeStartNs - bounce.start_ns) / NS_PER_SECOND);
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
}
