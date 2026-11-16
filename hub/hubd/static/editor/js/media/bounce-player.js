// Plays the bounce and is the editor's clock.
//
// The whole bounce is decoded into memory (Web Audio) so playback starts instantly at any
// point and the time read back is exact: the video follows this clock (as in spike E2).
// A song's bounce is a few minutes; very long bounces would want streaming instead.

export class BouncePlayer {
  /** offsetSeconds: bounce time at song time 0 (the bounce usually starts before the video). */
  constructor(url, offsetSeconds) {
    this.url = url;
    this.offsetSeconds = offsetSeconds;
    this.context = null;
    this.buffer = null;
    this.source = null;
    this.startedAt = null;        // { songSeconds, contextTime } while playing
    this.loading = null;
  }

  /** Fetch and decode the bounce (once). */
  load() {
    if (!this.loading) {
      this.context = new AudioContext({ latencyHint: "interactive" });
      this.loading = fetch(this.url)
        .then(response => {
          if (!response.ok) throw new Error(`bounce: ${response.status}`);
          return response.arrayBuffer();
        })
        .then(bytes => this.context.decodeAudioData(bytes))
        .then(buffer => { this.buffer = buffer; });
    }
    return this.loading;
  }

  get isPlaying() {
    return this.startedAt !== null;
  }

  /** The song time being heard right now (or where it stopped). */
  currentSongSeconds(fallback) {
    if (!this.isPlaying) return fallback;
    return this.startedAt.songSeconds + (this.context.currentTime - this.startedAt.contextTime);
  }

  async play(fromSongSeconds) {
    await this.load();
    await this.context.resume();
    this.stopSource();
    const when = this.context.currentTime + 0.03;
    const bounceSeconds = fromSongSeconds + this.offsetSeconds;
    this.source = this.context.createBufferSource();
    this.source.buffer = this.buffer;
    this.source.connect(this.context.destination);
    if (bounceSeconds >= 0) this.source.start(when, bounceSeconds);
    else this.source.start(when - bounceSeconds, 0);          // the video starts before the bounce
    this.startedAt = { songSeconds: fromSongSeconds, contextTime: when };
  }

  /** Stop; returns the song time it stopped at. */
  pause(fallback) {
    const at = this.currentSongSeconds(fallback);
    this.stopSource();
    this.startedAt = null;
    return at;
  }

  stopSource() {
    if (this.source) {
      try { this.source.stop(); } catch (error) { /* already stopped */ }
      this.source = null;
    }
  }
}
