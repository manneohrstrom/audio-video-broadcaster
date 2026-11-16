// Decodes one camera file in the browser, frame-accurately, with WebCodecs (spike E2,
// docs/editor-spikes.md: 4-5 cameras at once on the user's Mac, exact-frame seeks ~0.1 s).
//
// How it works:
//   - open(): read the file's index (the "moov" box) with mp4box.js, fetching the file
//     piece by piece with HTTP Range requests until the index is complete. Every frame's
//     time, size and position is then known without downloading the video.
//   - seek(t): start decoding at the keyframe before t, throw away frames before t.
//   - the decoder is fed one group of pictures (keyframe to keyframe, one Range request)
//     at a time, staying a few frames ahead of what's shown.
//   - frameAt(t): the decoded frame that is on screen at file time t (or null if it isn't
//     decoded yet). Frames are VideoFrames and must be closed: this class owns them.
//
// Times are file seconds (the camera file's own time; editor-store.js converts).
// Needs the global MP4Box (vendor/mp4box.all.min.js, loaded by index.html).

const FIRST_INDEX_PIECE = 1 << 20;
const MAX_INDEX_PIECE = 16 << 20;
const FRAMES_AHEAD = 6;
const DECODE_QUEUE_LIMIT = 3;
const CACHED_GROUPS = 6;

export class CameraDecoder {
  constructor(url) {
    this.url = url;
    this.samples = null;           // decode order: { timeUs, durationUs, isKey, offset, size }
    this.presentation = null;      // sample indices sorted by time
    this.keyframes = null;         // decode-order indices of keyframes
    this.config = null;
    this.decoder = null;
    this.frames = [];              // decoded, not yet shown, sorted by time
    this.current = null;           // the frame on screen
    this.generation = 0;           // bumps on every seek; stale work checks it and stops
    this.nextToFeed = 0;
    this.discardBeforeUs = 0;
    this.groups = new Map();       // keyframe index -> Promise<{ start, bytes }>
    this.targetUs = null;          // what the last seek aimed for
    this.problem = null;
    this.opening = null;
  }

  // -- reading the file
  async fetchRange(start, end) {
    const response = await fetch(this.url, { headers: { Range: `bytes=${start}-${end}` } });
    if (!response.ok) throw new Error(`${this.url}: HTTP ${response.status}`);
    return response.arrayBuffer();
  }

  open() {
    if (!this.opening) this.opening = this.readIndex().catch(error => { this.problem = String(error); throw error; });
    return this.opening;
  }

  async readIndex() {
    const file = MP4Box.createFile();
    let info = null;
    file.onReady = ready => { info = ready; };
    let position = 0;
    let piece = FIRST_INDEX_PIECE;
    while (!info) {
      const bytes = await this.fetchRange(position, position + piece - 1);
      bytes.fileStart = position;
      const next = file.appendBuffer(bytes);
      if (bytes.byteLength < piece) { file.flush(); break; }
      position = typeof next === "number" && next > position ? next : position + bytes.byteLength;
      piece = Math.min(piece * 2, MAX_INDEX_PIECE);
    }
    if (!info) throw new Error("no video index (moov) in the file");
    const track = info.videoTracks[0];
    const trak = file.getTrackById(track.id);
    if (!trak.samples || trak.samples.length !== track.nb_samples) file.buildSampleLists();
    const entry = trak.mdia.minf.stbl.stsd.entries[0];
    const stream = new DataStream(undefined, 0, DataStream.BIG_ENDIAN);
    (entry.avcC || entry.hvcC).write(stream);
    this.config = {
      codec: track.codec,
      description: new Uint8Array(stream.buffer, 8),               // the box minus its header
      codedWidth: track.video.width,
      codedHeight: track.video.height,
      hardwareAcceleration: "prefer-hardware",
      optimizeForLatency: true,
    };
    const support = await VideoDecoder.isConfigSupported(this.config);
    if (!support.supported) throw new Error(`this browser can't decode ${track.codec}`);
    this.samples = trak.samples.map(sample => ({
      timeUs: Math.round(sample.cts / sample.timescale * 1e6),
      durationUs: Math.round(sample.duration / sample.timescale * 1e6),
      isKey: sample.is_sync,
      offset: sample.offset,
      size: sample.size,
    }));
    this.presentation = this.samples.map((_, i) => i).sort((a, b) => this.samples[a].timeUs - this.samples[b].timeUs);
    this.keyframes = this.samples.map((sample, i) => (sample.isKey ? i : -1)).filter(i => i >= 0);
    this.createDecoder();
  }

  createDecoder() {
    if (this.decoder && this.decoder.state !== "closed") this.decoder.close();
    this.decoder = new VideoDecoder({
      output: frame => this.receive(frame),
      error: error => { this.problem = String(error); },
    });
    this.decoder.configure(this.config);
  }

  get ready() {
    return this.samples !== null;
  }

  // -- finding frames
  /** Decode-order index of the frame on screen at timeUs (the latest frame not after it), or -1. */
  frameIndexAt(timeUs) {
    const order = this.presentation;
    if (timeUs < this.samples[order[0]].timeUs) return -1;
    let low = 0;
    let high = order.length - 1;
    while (low < high) {
      const middle = (low + high + 1) >> 1;
      if (this.samples[order[middle]].timeUs <= timeUs) low = middle; else high = middle - 1;
    }
    return order[low];
  }

  keyframeBefore(index) {
    let low = 0;
    let high = this.keyframes.length - 1;
    while (low < high) {
      const middle = (low + high + 1) >> 1;
      if (this.keyframes[middle] <= index) low = middle; else high = middle - 1;
    }
    return this.keyframes[low];
  }

  /** The bytes of the group of pictures starting at keyframe `key` (one Range request, cached). */
  group(key) {
    if (!this.groups.has(key)) {
      const position = this.keyframes.indexOf(key);
      const last = position + 1 < this.keyframes.length ? this.keyframes[position + 1] - 1 : this.samples.length - 1;
      let start = Infinity;
      let end = 0;
      for (let i = key; i <= last; i++) {
        start = Math.min(start, this.samples[i].offset);
        end = Math.max(end, this.samples[i].offset + this.samples[i].size);
      }
      this.groups.set(key, this.fetchRange(start, end - 1).then(bytes => ({ start, bytes })));
      while (this.groups.size > CACHED_GROUPS) this.groups.delete(this.groups.keys().next().value);
    }
    return this.groups.get(key);
  }

  // -- decoding
  receive(frame) {
    if (frame.timestamp < this.discardBeforeUs) { frame.close(); return; }
    let i = this.frames.length;
    while (i > 0 && this.frames[i - 1].timestamp > frame.timestamp) i--;
    this.frames.splice(i, 0, frame);
  }

  /** Start decoding so that fileSeconds is shown as soon as possible. */
  seek(fileSeconds) {
    if (!this.ready) return;
    const target = this.frameIndexAt(Math.round(fileSeconds * 1e6));
    const index = Math.max(0, target);
    this.generation++;
    this.createDecoder();
    for (const frame of this.frames) frame.close();
    this.frames = [];
    this.nextToFeed = this.keyframeBefore(index);
    this.discardBeforeUs = this.samples[index].timeUs - 1;
    this.targetUs = this.samples[index].timeUs;
    this.feed(this.generation);
  }

  async feed(generation) {
    while (generation === this.generation) {
      if (this.nextToFeed >= this.samples.length || this.decoder.decodeQueueSize >= DECODE_QUEUE_LIMIT ||
          this.frames.length >= FRAMES_AHEAD) {
        await new Promise(resolve => setTimeout(resolve, 4));
        continue;
      }
      const index = this.nextToFeed;
      const key = this.keyframeBefore(index);
      let group;
      try {
        group = await this.group(key);
      } catch (error) {
        this.problem = String(error);
        return;
      }
      if (generation !== this.generation) return;
      const position = this.keyframes.indexOf(key);
      if (index === key && position + 1 < this.keyframes.length) this.group(this.keyframes[position + 1]);   // fetch ahead
      const sample = this.samples[index];
      this.decoder.decode(new EncodedVideoChunk({
        type: sample.isKey ? "key" : "delta",
        timestamp: sample.timeUs,
        duration: sample.durationUs,
        data: new Uint8Array(group.bytes, sample.offset - group.start, sample.size),
      }));
      this.nextToFeed++;
    }
  }

  /**
   * The frame on screen at fileSeconds: moves the decoded frames along and returns the
   * newest one not after that time (or the last one shown, or null).
   */
  frameAt(fileSeconds) {
    const timeUs = Math.round(fileSeconds * 1e6) + 1;
    let pick = -1;
    for (let i = 0; i < this.frames.length && this.frames[i].timestamp <= timeUs; i++) pick = i;
    if (pick >= 0) {
      for (let i = 0; i < pick; i++) this.frames[i].close();
      const frame = this.frames[pick];
      this.frames.splice(0, pick + 1);
      if (this.current) this.current.close();
      this.current = frame;
    }
    return this.current;
  }

  /** True if what's on screen (or being decoded towards) is near fileSeconds; otherwise a seek is due. */
  isNear(fileSeconds, playing) {
    if (this.targetUs === null) return false;
    const timeUs = fileSeconds * 1e6;
    if (!playing) {
      const wanted = this.frameIndexAt(Math.round(timeUs));
      return wanted >= 0 && this.samples[wanted].timeUs === this.targetUs ||
        (this.current && wanted >= 0 && this.current.timestamp === this.samples[wanted].timeUs);
    }
    const shownUs = this.current ? this.current.timestamp : this.targetUs;
    return timeUs - shownUs > -50_000 && timeUs - shownUs < 500_000;     // playing: we're keeping up
  }

  close() {
    this.generation++;
    if (this.decoder && this.decoder.state !== "closed") this.decoder.close();
    for (const frame of this.frames) frame.close();
    this.frames = [];
    if (this.current) this.current.close();
    this.current = null;
  }
}
