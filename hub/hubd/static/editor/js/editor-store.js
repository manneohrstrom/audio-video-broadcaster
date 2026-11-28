// The editor's state and the actions that change it, in one place.
//
// Components subscribe to be re-rendered when something they show changes (`notify`).
// The timeline and the video window are canvases redrawn every animation frame; they read
// the store directly, so fast-changing values (playhead, hover) don't re-render components.
//
// Time in the store is "song seconds": seconds from the start of the song's range, the part
// of the take where the bounce and the cameras overlap (hub/hubd/editor_projects.py).
//
// Sync mode (docs/editor-plan.md section 4b, project.mode "sync"): the range is the whole
// take, one camera shows at a time, and the bounce can be moved against the video by hand.
// The cut isn't edited or saved there; Save writes where the bounce starts (sync.json).
import * as EditModel from "./edit-model.js";
import { cutsFromFile, cutsToFile, NS_PER_SECOND } from "./time-format.js";
import { saveEdit, saveSync } from "./api.js";
import { DEFAULT_STORY_SECONDS, cutsInStory } from "./story.js";

const SAVE_DELAY_MS = 800;
export const FRAME_SECONDS = 1 / 30;
export const MAX_PIXELS_PER_SECOND = 320;
/** The shortest video the start and end may leave (seconds). */
export const MIN_VIDEO_SECONDS = 1;

export class EditorStore {
  constructor(project) {
    this.project = project;
    this.rangeStartNs = project.range_ns[0];
    this.songSeconds = (project.range_ns[1] - project.range_ns[0]) / NS_PER_SECOND;
    this.cameras = project.cameras.map((camera, index) => ({ ...camera, index, number: index + 1 }));
    this.camerasById = new Map(this.cameras.map(camera => [camera.id, camera]));

    // Where the bounce's t=0 sits on the hub clock. Fixed in the edit; moved by hand in Sync mode.
    this.syncMode = project.mode === "sync";
    this.bounceStartNs = project.bounce.start_ns;
    this.syncStartNs = project.bounce.start_ns;       // where Sync mode opened (to go back to)
    this.syncSaveState = "idle";                       // "idle" | "saving" | "error"

    this.cuts = cutsFromFile(project.edit.cuts);
    this.dissolveSeconds = project.edit.dissolve_s ?? 0.3;
    // The border and logo on top of everything (brand/brand-border.png and brand-logo.png; the logo fades in): in the preview and the video.
    this.branding = project.edit.branding === true;
    this.brandingColour = project.edit.branding_colour || "#ffffff";
    // Step 3, the title sequence (docs/editor-plan.md section 5b; title/title-layout.js): the
    // title, its colour, the names on the strip and how long they stay (duration_s). It runs
    // from the video's start; start_s is kept in the file but no longer used. Off until switched on.
    this.title = { on: false, text: project.song.name, lines: [], colour: "#f3ead8", start_s: 0, duration_s: 6,
                   ...(project.edit.title || {}) };

    // The video's start and end (user, 2026-11-21): the edit keeps the whole song (the clap
    // before it, the talk after), the video is rendered from inSeconds to outSeconds only.
    // In the edit file: trim_ns, [start, end] in nanoseconds from the start of the range, or null.
    const trim = project.edit.trim_ns;
    this.inSeconds = trim ? trim[0] / NS_PER_SECOND : 0;
    this.outSeconds = trim ? Math.min(this.songSeconds, trim[1] / NS_PER_SECOND) : this.songSeconds;

    // Step 4, the Instagram story (story.js, hub/ingest/story_render.py): its own start and
    // end (null until set: 30 s from the video's start) and where the 9:16 slice sits in each
    // camera's picture (camera id -> 0 left edge .. 1 right edge; missing: the middle).
    const story = project.edit.story || {};
    this.storyRange = story.range_ns
      ? [story.range_ns[0] / NS_PER_SECOND, Math.min(this.songSeconds, story.range_ns[1] / NS_PER_SECOND)] : null;
    this.storyFraming = { ...(story.framing || {}) };

    // Step 2, the look (docs/editor-plan.md section 5): one filter with a strength and any
    // number of effects with a strength each, for the whole song. Definitions: looks.json.
    this.step = "cut";                       // "cut" | "look" | "title" | "story"
    this.looks = null;                       // looks.json, loaded by the page
    this.look = { filter: "normal", filter_strength: 1, effects: {}, adjust: {}, ...(project.edit.look || {}) };
    this.comparing = false;                  // held down on the video: show without the look
    this.history = new EditModel.History();

    // The AI cut (docs/ai-cut-plan.md): the run whose cut was last put in (its id is saved
    // with the edit), loaded by the page for the Music lane and the reasons.
    this.aiRunId = project.edit.ai_run || null;
    this.aiRun = null;
    this.musicFor = undefined;               // the run the Music lane was last worked out for
    this.musicCache = null;

    this.playheadSeconds = 0;
    this.isPlaying = false;
    this.hover = null;            // { seconds, x, lane } while the mouse is over the timeline
    this.gesture = null;          // the drag in progress, see timeline-pointer.js
    this.selectedCutIndex = -1;

    this.pixelsPerSecond = 1;     // set to "whole song" once the timeline knows its width
    this.viewStartSeconds = 0;
    this.fitWholeSong = true;
    this.timelineWidth = 0;

    this.saveState = "saved";     // "saved" | "unsaved" | "saving" | "error"
    this.saveProblem = null;
    this.saveTimer = null;
    this.toast = null;
    this.listeners = new Set();
  }

  // -- subscriptions
  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    for (const listener of this.listeners) listener();
  }

  showToast(text) {
    this.toast = text;
    this.notify();
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => { this.toast = null; this.notify(); }, 3000);
  }

  // -- time on the hub clock, and the cameras
  /** Bounce seconds at song time 0 (positive: the bounce started before the song's range). */
  get bounceOffsetSeconds() {
    return (this.rangeStartNs - this.bounceStartNs) / NS_PER_SECOND;
  }

  hubNsAt(songSeconds) {
    return this.rangeStartNs + songSeconds * NS_PER_SECOND;
  }

  /** Seconds into the camera's file at a song time. */
  cameraFileSeconds(camera, songSeconds) {
    return (this.hubNsAt(songSeconds) - camera.file_zero_ns) / NS_PER_SECOND;
  }

  /** Song seconds at which the camera starts and stops recording (may be outside the song). */
  recordedSpan(camera) {
    return [(camera.first_frame_ns - this.rangeStartNs) / NS_PER_SECOND,
            (camera.last_frame_ns - this.rangeStartNs) / NS_PER_SECOND];
  }

  isRecording(camera, songSeconds) {
    const [start, end] = this.recordedSpan(camera);
    return songSeconds >= start && songSeconds <= end;
  }

  /** Rough sections (dropped frames) in song seconds: [[start, end], ...]. */
  roughSections(camera) {
    return camera.rough_sections_ns.map(([start, end]) => [
      (start - this.rangeStartNs) / NS_PER_SECOND, (end - this.rangeStartNs) / NS_PER_SECOND]);
  }

  hasRoughSectionIn(camera, start, end) {
    return this.roughSections(camera).some(([roughStart, roughEnd]) => roughStart < end && start < roughEnd);
  }

  cameraAt(songSeconds) {
    return this.camerasById.get(EditModel.cameraAt(this.cuts, songSeconds));
  }

  shots() {
    return EditModel.shots(this.cuts, this.songSeconds).map(shot => ({ ...shot, camera: this.camerasById.get(shot.cameraId) }));
  }

  // -- changing the cut (every change goes through here: history, warnings, saving)
  beginChange() {
    this.history.record(this.cuts);
  }

  setCuts(cuts, { warnAbout } = {}) {
    this.cuts = cuts;
    if (warnAbout) this.warnAboutShot(warnAbout.camera, warnAbout.start, warnAbout.end);
    this.scheduleSave();
    this.notify();
  }

  warnAboutShot(camera, start, end) {
    const [recordStart, recordEnd] = this.recordedSpan(camera);
    if (start < recordStart - 0.05 || end > recordEnd + 0.05) {
      this.showToast(`${camera.label} didn't record all of this: the picture is black where it didn't`);
    } else if (this.hasRoughSectionIn(camera, start, end)) {
      this.showToast(`${camera.label} dropped frames in this stretch: it may stutter (striped)`);
    }
  }

  chooseCameraForStretch(camera, start, end) {
    this.beginChange();
    this.setCuts(EditModel.chooseCameraForStretch(this.cuts, camera.id, start, end, this.songSeconds),
                 { warnAbout: { camera, start, end } });
  }

  cutToCameraAtPlayhead(camera) {
    if (this.syncMode) {
      this.showCamera(camera);
      return;
    }
    const at = this.playheadSeconds;
    if (!this.isRecording(camera, at)) {
      this.showToast(`${camera.label} wasn't recording here`);
      return;
    }
    this.beginChange();
    this.setCuts(EditModel.cutToCameraAt(this.cuts, camera.id, at));
  }

  /** Set shot `index`'s camera move (camera-move.js; null: none). Dragging the amount slider
   *  sends many changes: those to the same shot's amount within a moment are one undo step. */
  setShotMove(index, move) {
    if (index < 0 || index >= this.cuts.length) return;
    const now = performance.now();
    const previous = this.cuts[index].move;
    const sameDrag = this.lastMoveChange && this.lastMoveChange.index === index && now - this.lastMoveChange.at < 1500
      && previous && move && previous.kind === move.kind;
    if (!sameDrag) this.beginChange();
    this.lastMoveChange = { index, at: now };
    this.setCuts(EditModel.setShotMove(this.cuts, index, move));
  }

  removeSelectedCut() {
    const index = this.selectedCutIndex;
    if (index <= 0) return;
    this.beginChange();
    this.selectedCutIndex = -1;
    this.setCuts(EditModel.removeCut(this.cuts, index));
  }

  /** Put an AI cut run's cuts in as the song's cut (undo brings back the cut before).
   *  The run's times are against the song range it was made with: moved onto today's. */
  applyAiRun(run) {
    const offsetNs = run.range_ns[0] - this.rangeStartNs;
    const cuts = EditModel.normalize(cutsFromFile(run.cuts, offsetNs).filter(cut => cut.atSeconds < this.songSeconds));
    if (!cuts.length) {
      this.showToast("That run has no cuts inside this song");
      return;
    }
    this.beginChange();
    this.selectedCutIndex = -1;
    this.aiRunId = run.id;
    this.aiRun = run;
    this.setCuts(cuts);
  }

  /** The Music lane from the AI run's listening: sections and moments in song seconds, or null. */
  get music() {
    if (this.musicFor !== this.aiRun) {             // worked out once per run (drawn every frame)
      this.musicFor = this.aiRun;
      this.musicCache = this.musicOf(this.aiRun);
    }
    return this.musicCache;
  }

  musicOf(run) {
    const answer = run && run.listening && run.listening.answer;
    if (!answer) return null;
    const scale = 1 + (run.drift_ppm || 0) * 1e-6;
    const songSecondsOf = bounceSeconds => (run.bounce_start_ns + bounceSeconds * NS_PER_SECOND * scale - this.rangeStartNs) / NS_PER_SECOND;
    return {
      sections: (answer.sections || []).map(section => ({
        start: songSecondsOf(section.start), end: songSecondsOf(section.end),
        label: [section.kind, section.leader].filter(part => part && part !== "none").join(" · ").replace(/_/g, " "),
        notes: section.notes || "" })),
      moments: (answer.moments || []).map(moment => ({
        at: songSecondsOf(moment.time), label: [moment.instrument, moment.what].filter(Boolean).join(": ") })),
    };
  }

  /** A camera's tags for this take changed (the AI cut's Cameras settings). */
  setCameraTags(cameraId, tags) {
    const camera = this.camerasById.get(cameraId);
    if (camera) {
      camera.tags = tags;
      camera.tags_from = "take";
    }
    this.notify();
  }

  undo() {
    if (this.syncMode) return;
    this.cuts = this.history.undo(this.cuts);
    this.selectedCutIndex = -1;
    this.scheduleSave();
    this.notify();
  }

  redo() {
    if (this.syncMode) return;
    this.cuts = this.history.redo(this.cuts);
    this.selectedCutIndex = -1;
    this.scheduleSave();
    this.notify();
  }

  // -- the look
  setStep(step) {
    this.step = step;
    this.hover = null;
    this.gesture = null;
    this.notify();
  }

  /** The chosen film overlay: { id, mode, strength, preview_url } or null. */
  activeOverlay() {
    const key = Object.keys(this.look.effects).find(k => k.startsWith("overlay:"));
    const overlay = key && (this.overlays || []).find(o => `overlay:${o.id}` === key);
    return overlay ? { ...overlay, strength: this.look.effects[key] } : null;
  }

  filterSettings(id = this.look.filter) {
    return this.looks?.filters.find(filter => filter.id === id)?.grade || {};
  }

  /** Tap a filter: choose it (at full strength). Its strength slider shows under the row. */
  tapFilter(id) {
    if (this.look.filter === id) return;
    this.look = { ...this.look, filter: id, filter_strength: 1 };
    this.scheduleSave();
    this.notify();
  }

  /** Tap an effect: on (at its default strength) or, if it's on, off (user, 2026-11-16).
   *  Film overlays ("overlay:<id>") are one at a time: choosing one replaces the other. */
  tapEffect(id) {
    if (this.look.effects[id] != null) {
      this.removeEffect(id);
      return;
    }
    const isOverlay = id.startsWith("overlay:");
    const effect = isOverlay ? (this.overlays || []).find(o => `overlay:${o.id}` === id) : this.looks.effects.find(e => e.id === id);
    const effects = Object.fromEntries(Object.entries(this.look.effects).filter(([key]) => !(isOverlay && key.startsWith("overlay:"))));
    this.look = { ...this.look, effects: { ...effects, [id]: effect.default_strength } };
    this.scheduleSave();
    this.notify();
  }

  setStrength(target, strength) {
    if (target === "filter") this.look = { ...this.look, filter_strength: strength };
    else this.look = { ...this.look, effects: { ...this.look.effects, [target]: strength } };
    this.scheduleSave();
    this.notify();
  }

  /** An Adjust slider: -1..1, 0 = untouched (definitions in looks.json "adjustments"). */
  setAdjustment(id, value) {
    const adjust = { ...this.look.adjust, [id]: value };
    if (!value) delete adjust[id];
    this.look = { ...this.look, adjust };
    this.scheduleSave();
    this.notify();
  }

  resetAdjustments() {
    this.look = { ...this.look, adjust: {} };
    this.scheduleSave();
    this.notify();
  }

  removeEffect(id) {
    const effects = { ...this.look.effects };
    delete effects[id];
    this.look = { ...this.look, effects };
    this.scheduleSave();
    this.notify();
  }

  setBrandingColour(colour) {
    this.brandingColour = colour;
    this.scheduleSave();
    this.notify();
  }

  setBranding(on) {
    this.branding = on;
    this.scheduleSave();
    this.notify();
  }

  /** Change some of the title's settings ({ text, lines, colour, duration_s, on }). */
  setTitle(changes) {
    this.title = { ...this.title, ...changes };
    this.scheduleSave();
    this.notify();
  }

  // -- the video's start and end
  get isTrimmed() {
    return this.inSeconds > 0.001 || this.outSeconds < this.songSeconds - 0.001;
  }

  /** How long the rendered video is. */
  get videoSeconds() {
    return this.outSeconds - this.inSeconds;
  }

  /** The video starts here (on a frame; at least MIN_VIDEO_SECONDS before the end). */
  setInPoint(seconds) {
    if (this.syncMode) return;
    const onFrame = Math.round(seconds / FRAME_SECONDS) * FRAME_SECONDS;
    this.inSeconds = Math.max(0, Math.min(onFrame, this.outSeconds - MIN_VIDEO_SECONDS));
    this.scheduleSave();
    this.notify();
  }

  /** The video ends here (on a frame; at least MIN_VIDEO_SECONDS after the start). */
  setOutPoint(seconds) {
    if (this.syncMode) return;
    const onFrame = Math.round(seconds / FRAME_SECONDS) * FRAME_SECONDS;
    this.outSeconds = Math.min(this.songSeconds, Math.max(onFrame, this.inSeconds + MIN_VIDEO_SECONDS));
    this.scheduleSave();
    this.notify();
  }

  /** The whole song again. */
  clearTrim() {
    this.inSeconds = 0;
    this.outSeconds = this.songSeconds;
    this.scheduleSave();
    this.notify();
  }

  // -- the story's start and end, and its framing
  /** Where the story starts and ends: its own, else 30 s from the video's start. */
  get storyInSeconds() {
    return this.storyRange ? this.storyRange[0] : this.inSeconds;
  }

  get storyOutSeconds() {
    return this.storyRange ? this.storyRange[1] : Math.min(this.outSeconds, this.inSeconds + DEFAULT_STORY_SECONDS);
  }

  get storySeconds() {
    return this.storyOutSeconds - this.storyInSeconds;
  }

  /** The story starts here (on a frame; at least MIN_VIDEO_SECONDS before its end). */
  setStoryIn(seconds) {
    const onFrame = Math.round(seconds / FRAME_SECONDS) * FRAME_SECONDS;
    const out = this.storyOutSeconds;
    this.storyRange = [Math.max(0, Math.min(onFrame, out - MIN_VIDEO_SECONDS)), out];
    this.scheduleSave();
    this.notify();
  }

  /** The story ends here (on a frame; at least MIN_VIDEO_SECONDS after its start). */
  setStoryOut(seconds) {
    const onFrame = Math.round(seconds / FRAME_SECONDS) * FRAME_SECONDS;
    const start = this.storyInSeconds;
    this.storyRange = [start, Math.min(this.songSeconds, Math.max(onFrame, start + MIN_VIDEO_SECONDS))];
    this.scheduleSave();
    this.notify();
  }

  /** Where the slice sits in this camera's picture, 0 (left edge) .. 1 (right edge). */
  setFraming(cameraId, position) {
    this.storyFraming = { ...this.storyFraming, [cameraId]: Math.round(Math.min(1, Math.max(0, position)) * 10000) / 10000 };
    this.scheduleSave();
    this.notify();
  }

  /** In the story, dissolve at this cut (true) or glide the slice across (false). Undoable, like the cut. */
  setStoryDissolve(cutIndex, on) {
    this.setStoryDissolveAt([cutIndex], on);
  }

  /** The same for every cut inside the story. */
  setStoryDissolveEverywhere(on) {
    this.setStoryDissolveAt(cutsInStory(this.cuts, this.storyInSeconds, this.storyOutSeconds).map(({ index }) => index), on);
  }

  setStoryDissolveAt(indexes, on) {
    this.beginChange();
    const cuts = this.cuts.map((cut, index) => {
      if (!indexes.includes(index)) return cut;
      const { storyGlide, ...rest } = cut;            // dissolve is the default: only a glide is marked
      return on ? rest : { ...rest, storyGlide: true };
    });
    this.setCuts(cuts);
  }

  // -- the start and end the timeline shows and the keys I and O set: the story's in the
  //    Story step, the video's everywhere else
  get markIn() {
    return this.step === "story" ? this.storyInSeconds : this.inSeconds;
  }

  get markOut() {
    return this.step === "story" ? this.storyOutSeconds : this.outSeconds;
  }

  setMarkIn(seconds) {
    if (this.step === "story") this.setStoryIn(seconds); else this.setInPoint(seconds);
  }

  setMarkOut(seconds) {
    if (this.step === "story") this.setStoryOut(seconds); else this.setOutPoint(seconds);
  }

  setDissolve(seconds) {
    this.dissolveSeconds = seconds;
    this.scheduleSave();
    this.notify();
  }

  // -- Sync mode: move the bounce against the video
  /** Show this camera in the video window (Sync mode shows one camera, no cut). */
  showCamera(camera) {
    this.cuts = [{ atSeconds: 0, cameraId: camera.id, by: "user" }];
    this.notify();
  }

  /** Where the bounce starts on the hub clock, in nanoseconds (a JS number: ~256 ns steps). */
  setBounceStartNs(startNs) {
    this.bounceStartNs = startNs;
    this.notify();
  }

  /** Move the bounce later (positive) or earlier (negative) by `seconds`. */
  moveBounceBy(seconds) {
    this.setBounceStartNs(this.bounceStartNs + seconds * NS_PER_SECOND);
  }

  /** How far the bounce has moved since Sync mode opened, in seconds. */
  get bounceMovedSeconds() {
    return (this.bounceStartNs - this.syncStartNs) / NS_PER_SECOND;
  }

  /** Save the placement as the bounce's sync (by hand). Resolves when the hub has it. */
  async saveBouncePlacement() {
    const { take_id: takeId, song, version } = this.project;
    this.syncSaveState = "saving";
    this.notify();
    try {
      await saveSync(takeId, song.id, version, this.bounceStartNs);
      this.syncStartNs = this.bounceStartNs;
      this.syncSaveState = "idle";
      this.notify();
      return true;
    } catch (error) {
      this.syncSaveState = "error";
      this.showToast(`The sync wasn't saved: ${error.message}`);
      return false;
    }
  }

  // -- saving: the working copy on the hub, shortly after each change
  scheduleSave() {
    if (this.syncMode) return;                 // Sync mode doesn't change the edit
    this.saveState = "unsaved";
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.saveNow(), SAVE_DELAY_MS);
  }

  async saveNow() {
    clearTimeout(this.saveTimer);
    this.saveState = "saving";
    this.notify();
    const { take_id: takeId, song, version } = this.project;
    try {
      await saveEdit(takeId, song.id, version, { cuts: cutsToFile(this.cuts), dissolve_s: this.dissolveSeconds, look: this.look,
                      branding: this.branding, branding_colour: this.brandingColour, title: this.title,
                      trim_ns: this.isTrimmed ? [Math.round(this.inSeconds * NS_PER_SECOND), Math.round(this.outSeconds * NS_PER_SECOND)] : null,
                      story: { framing: this.storyFraming,
                               range_ns: this.storyRange ? this.storyRange.map(seconds => Math.round(seconds * NS_PER_SECOND)) : null },
                      ai_run: this.aiRunId });
      this.saveState = this.saveState === "saving" ? "saved" : this.saveState;
      this.saveProblem = null;
    } catch (error) {
      this.saveState = "error";
      this.saveProblem = error.message;
    }
    this.notify();
  }

  // -- the view: zoom and scroll
  setTimelineWidth(width) {
    this.timelineWidth = width;
    if (this.fitWholeSong) this.pixelsPerSecond = width / this.songSeconds;
    this.clampView();
  }

  get minimumPixelsPerSecond() {
    return this.timelineWidth / this.songSeconds;
  }

  clampView() {
    this.pixelsPerSecond = Math.max(this.minimumPixelsPerSecond || this.pixelsPerSecond, Math.min(MAX_PIXELS_PER_SECOND, this.pixelsPerSecond));
    const visibleSeconds = this.timelineWidth / this.pixelsPerSecond;
    this.viewStartSeconds = Math.max(0, Math.min(this.songSeconds - visibleSeconds, this.viewStartSeconds));
  }

  /** Zoom to `pixelsPerSecond`, keeping the point at `anchorSeconds` where it is on screen. */
  zoomTo(pixelsPerSecond, anchorSeconds = this.playheadSeconds) {
    const anchorX = (anchorSeconds - this.viewStartSeconds) * this.pixelsPerSecond;
    this.pixelsPerSecond = pixelsPerSecond;
    this.clampView();
    this.viewStartSeconds = anchorSeconds - anchorX / this.pixelsPerSecond;
    this.clampView();
    this.fitWholeSong = this.pixelsPerSecond <= this.minimumPixelsPerSecond * 1.0001;
    this.notify();
  }

  /** The zoom slider's position, 0 (whole song) .. 1 (closest), on a log scale. */
  get zoomPosition() {
    const minimum = this.minimumPixelsPerSecond;
    if (!minimum) return 0;
    return Math.log(this.pixelsPerSecond / minimum) / Math.log(MAX_PIXELS_PER_SECOND / minimum);
  }

  setZoomPosition(position) {
    const minimum = this.minimumPixelsPerSecond;
    this.zoomTo(minimum * Math.exp(position * Math.log(MAX_PIXELS_PER_SECOND / minimum)));
  }

  scrollBy(seconds) {
    this.viewStartSeconds += seconds;
    this.clampView();
  }

  xOf(songSeconds) {
    return (songSeconds - this.viewStartSeconds) * this.pixelsPerSecond;
  }

  secondsAtX(x) {
    return Math.max(0, Math.min(this.songSeconds, this.viewStartSeconds + x / this.pixelsPerSecond));
  }
}
