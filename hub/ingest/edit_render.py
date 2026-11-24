"""Renders the editor's edit: the finished video of one song (docs/editor-plan.md section 6).

Input: the edit the user made in the editor (cuts, dissolve, look), the take (camera
files on the STUDIO drive, take.json) and the synced bounce. Output: 1920x1080, exactly
30 fps, H.264, the bounce as AAC 320 kbps, .mp4 with faststart.

How (an extension of the quick mix's pipe, render.py / spike E3):

  - one ffmpeg decoder per shot, reading the full-quality original: input seek, then
    fps=30 (the source frame nearest each output frame's real time), scaled to 1080p,
    with the look's colour table (lut3d) already applied;
  - at each cut the next shot's decoder starts a little early, so for the dissolve both
    are running: this script mixes their frames (smoothstep, centred on the cut, the
    same curve as the editor's preview);
  - frames go, as YUV, to one x264 encoder, whose ffmpeg filter graph adds the look's
    effects (look_render.effect_filter_graph) and which takes the bounce as the only sound.

A dissolve mixes YUV frames, which is the same as mixing RGB (the conversion is linear).
This script only passes frames along (no RGB, no per-frame effects in Python): numpy
effects needed ~270 MB here and ran the 1 GB hub out of memory (2026-11-16).

Small, known difference to the preview: the preview mixes the two pictures and then
applies the colour table; the render applies the table to each shot and then mixes.
Only the dissolve frames are affected, and only by the non-linearity of the look.
"""
import functools
import json
import os
import signal
import subprocess

import numpy as np

import look_render
import render
import title_render

FPS = render.FPS
WIDTH, HEIGHT = render.W, render.H
NS = render.NS


class Shot:
    """One shot of the cut, in output frames, with the frames its decoder must deliver.

    covered_from..covered_to: the output frames the camera recorded at all. Outside them
    (it started late or stopped early) the shot is black, as in the editor's preview,
    and the decoder only runs for the frames it has: seeking before a file's first frame
    would hand over its first frames too early (out of sync), and asking past its last
    frame would end the decoder short (2026-11-17, found with a synthetic take)."""

    def __init__(self, camera_id, first_frame, end_frame, story_dissolve=False):
        self.camera_id = camera_id
        self.story_dissolve = story_dissolve       # the story dissolves into this shot, or glides (story_render)
        self.first_frame, self.end_frame = first_frame, end_frame       # what's on screen
        self.decode_from, self.decode_to = first_frame, end_frame       # plus dissolve overlap
        self.covered_from, self.covered_to = first_frame, end_frame
        self.process = None
        self.started = False
        self.last_picture = None

    def decoded_frames(self):
        """(first output frame, count) the decoder delivers: the shot's frames the camera has."""
        first = max(self.decode_from, self.covered_from)
        return first, max(0, min(self.decode_to, self.covered_to) - first)


def plan_shots(edit, take_doc, dissolve_s):
    """The shots of the edit, each widened by half the dissolve on both sides where the
    camera has those frames. Returns (shots, total frames)."""
    cameras = render.cameras(take_doc, include_phones=True)
    range_start, range_end = edit["range_ns"]
    total = int(round((range_end - range_start) * FPS / NS))
    bounds = [int(round(cut["at_ns"] * FPS / NS)) for cut in edit["cuts"]] + [total]
    # Stories dissolve at a cut unless it's marked story_glide (user, 2026-11-24: dissolve by default).
    shots = [Shot(cut["camera"], first, end, cut.get("story_glide") is not True)
             for cut, first, end in zip(edit["cuts"], bounds, bounds[1:]) if end > first]
    half = int(round(dissolve_s * FPS / 2))
    for i, shot in enumerate(shots):
        camera = cameras.get(shot.camera_id)
        if camera is None:
            raise ValueError("camera %s isn't in this take" % shot.camera_id)
        # Frames this camera has (output frame numbers): at or after its first frame, at or
        # before its last. For the dissolve overlap, a frame of margin at each end.
        shot.covered_from = int(np.ceil((camera["start_ns"] - range_start) * FPS / NS))
        shot.covered_to = int(np.floor((camera["end_ns"] - range_start) * FPS / NS)) + 1
        has_from, has_to = shot.covered_from + 1, shot.covered_to - 2
        if i > 0:
            shot.decode_from = max(shot.first_frame - half, has_from, 0)
        if i + 1 < len(shots):
            shot.decode_to = min(shot.end_frame + half, has_to, total)
        shot.decode_from = min(shot.decode_from, shot.first_frame)
        shot.decode_to = max(shot.decode_to, shot.end_frame)
    return shots, total


def trimmed(edit):
    """The edit cut down to the video's start and end (trim_ns, ns from the start of the
    range; user, 2026-11-21): the range narrowed, the cuts moved to the new start (the camera
    showing at the start becomes the first cut). The bounce and the beats follow from the
    range; the title sequence runs from the video's start anyway. Without trim_ns, the edit as it is."""
    trim = edit.get("trim_ns")
    if not trim:
        return edit
    in_ns, out_ns = int(trim[0]), int(trim[1])
    range_start = edit["range_ns"][0]
    cuts = sorted(edit["cuts"], key=lambda cut: cut["at_ns"])
    showing = ([cut for cut in cuts if cut["at_ns"] <= in_ns] or cuts)[-1]       # the camera on at the start
    moved = [dict(showing, at_ns=0)] + [dict(cut, at_ns=cut["at_ns"] - in_ns)
                                         for cut in cuts if in_ns < cut["at_ns"] < out_ns]
    return dict(edit, range_ns=[range_start + in_ns, range_start + out_ns], cuts=moved, trim_ns=None)


def dissolve_mix(frame_number, cut_frame, half_frames):
    """How much of the incoming shot shows (0..1) around a cut: smoothstep over the dissolve."""
    if half_frames <= 0:
        return 1.0 if frame_number >= cut_frame else 0.0
    # Frame times are frame starts; the cut is at cut_frame (same as the preview's song time).
    t = 0.5 + (frame_number - cut_frame) / (2.0 * half_frames)
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def denoise_filter(strength):
    """hqdn3d before the look (the Denoise effect): the dark room's high-gain noise, mostly
    colour blotches, cleaned so the look's grain reads as film, not as camera noise."""
    return "hqdn3d=%.2f:%.2f:%.2f:%.2f" % (2 + 4 * strength, 3 + 6 * strength, 3 + 6 * strength, 4 + 8 * strength)


# Colour: the Pi cameras' files carry no colour tags, but they are Rec. 709, limited range
# (rpicam-vid at 720p and up), and that is how the browser shows untagged video in the editor's
# preview (Chrome reads it as 709; measured 2026-11-18). A tagged file (a phone's, maybe) is
# shown by its tags. ffmpeg instead assumes Rec. 601 for untagged video whenever it converts to
# RGB (lut3d, the effects) or to the finished video's 709 tags: until 2026-11-18 every render
# showed saturated colours as 601, a warm red (193, 48, 32) in the preview came out
# (180, 34, 34). So the decoders read each file as the browser does and always hand over
# Rec. 709 limited range, and the encoder's raw input says so.
PIPE_COLOUR_OPTIONS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
# ffprobe's colour_space tag -> scale's name for that matrix. Anything else counts as untagged.
SCALE_MATRIX = {"bt709": "bt709", "smpte170m": "smpte170m", "bt470bg": "bt470", "bt2020nc": "bt2020"}


@functools.lru_cache(maxsize=64)
def source_colour(path):
    """(matrix, range) of a video file as the browser reads it: its tags, else Rec. 709 limited."""
    try:
        text = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                               "stream=color_space,color_range", "-of", "default=noprint_wrappers=1", path],
                              capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        text = ""
    tags = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)     # ffprobe's own order
    matrix = SCALE_MATRIX.get(tags.get("color_space", ""), "bt709")
    full = tags.get("color_range") in ("pc", "jpeg", "full")
    return matrix, "pc" if full else "tv"


def decoder_command(source, start_s, frames, lut_path, pixel_format, denoise=0, colour=("bt709", "tv")):
    """colour: the source's (matrix, range), from source_colour(). The output is Rec. 709 limited."""
    matrix, value_range = colour
    filters = "fps=%d:round=near,scale=%d:%d" % (FPS, WIDTH, HEIGHT)
    if (matrix, value_range) != ("bt709", "tv") and not lut_path:
        filters += ",scale=in_color_matrix=%s:in_range=%s:out_color_matrix=bt709:out_range=tv" % (matrix, value_range)
    if denoise:
        filters += "," + denoise_filter(denoise)
    if lut_path:
        # Explicit conversions around the colour table: the automatic ones assume Rec. 601, and
        # ffmpeg 7.1 (the hub's) still lost a couple of levels with them even on tagged frames.
        filters += (",scale=in_color_matrix=%s:in_range=%s,format=gbrp,lut3d=file='%s':interp=trilinear,"
                    "scale=out_color_matrix=bt709:out_range=tv" % (matrix, value_range, lut_path.replace("'", "")))
    return ["ffmpeg", "-nostdin", "-v", "error", "-threads", DECODER_THREADS, "-ss", "%.6f" % max(0.0, start_s), "-i", source,
            "-vf", filters, "-frames:v", str(frames), "-f", "rawvideo", "-pix_fmt", pixel_format, "-"]


# The finished video is the one that goes to YouTube (user, 2026-11-16: "high quality,
# ready to push up"), so it follows YouTube's recommended upload settings: H.264 High
# profile, CRF 16, a keyframe every half second (closed GOP, half the frame rate),
# 2 B-frames, Rec. 709 colour tags, AAC-LC 384 kbit/s 48 kHz stereo, faststart.
#
# Memory decides the rest (the hub has 1 GB; 2026-11-16 the first render was killed for
# memory during a dissolve: encoder "slow" 386 MB + two decoders 123 MB each + hubd 142 MB).
# "medium", 2 threads, lookahead 20: ~334 MB, and at CRF 16 it looks the same as "slow"
# (which mainly makes files smaller; YouTube re-encodes anyway). Decoders: 1 thread each.
FINAL_PRESET = "medium"
FINAL_CRF = "16"
ENCODER_THREADS = "2"
DECODER_THREADS = "1"
AUDIO_BITRATE = "384k"
AUDIO_EDGE_FADE_S = 0.02            # the fade in and out at the very start and end of the sound


def brand_colour_filter(colour):
    """lutrgb turning the brand frame's white into `colour` ("#rrggbb"); alpha untouched.
    The frame is pure white with transparency, so this is the colour exactly."""
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "lutrgb=r=val*%d/255:g=val*%d/255:b=val*%d/255" % (red, green, blue)


BRAND_BORDER = "brand-border.png"
BRAND_LOGO = "brand-logo.png"
BRAND_LOGO_BOX = "brand-logo.json"      # where the logo is in brand-logo.png: {x, y, w, h}


# The border's four edges, as crops of brand-border.png (x, y, w, h), for fading it in: the
# frame is 20 px wide and the middle is fully transparent (checked 2026-11-21), so 40 px
# strips hold all of it without overlapping.
BORDER_EDGES = ((0, 0, 1920, 40), (0, 1040, 1920, 40), (0, 40, 40, 1000), (1880, 40, 40, 1000))


def brand_overlay(graph, extra_inputs, brand_dir, colour="#ffffff", logo_start_s=title_render.LOGO_WITHOUT_TITLE_S,
                  border_start_s=None):
    """Put the brand frame on top of the finished picture, in `colour`: the border (from the
    first frame, or with border_start_s fading in then) and the logo (fading in at
    logo_start_s; seconds from the start of the video), two white 1920x1080 PNGs
    with transparency in brand_dir (hub/tools/make_brand_frame.py). The last step, after the
    look, its effects, the grain and the title, so nothing changes them. graph: the filter
    graph so far, ending in [video], or None.
    Each PNG is one frame, no -loop (a looping image input made ffmpeg decode copies ahead:
    +250 MB, 2026-11-16). The border: overlay repeats a finished input's last frame. The logo:
    the loop filter repeats its one frame (it holds just that one), timed at 30 fps for the fade;
    only the logo's box goes through that (the full frame cost ~40 MB more on the hub). A fading
    border goes the same way as its four edge strips (BORDER_EDGES), not the full frame."""
    with open(os.path.join(brand_dir, BRAND_LOGO_BOX)) as f:
        box = json.load(f)
    first = 2 + sum(1 for part in extra_inputs if part == "-i")
    extra_inputs = extra_inputs + ["-i", os.path.join(brand_dir, BRAND_BORDER), "-i", os.path.join(brand_dir, BRAND_LOGO)]
    tint = "" if colour.lower() == "#ffffff" else "," + brand_colour_filter(colour)
    fade_in = "loop=loop=-1:size=1,setpts=N/%d/TB,fade=t=in:st=%%.4f:d=%.4f:alpha=1" % (FPS, title_render.LOGO_FADE_S)
    chains = ["[%d:v]crop=%d:%d:%d:%d,format=rgba%s,%s[logo]"
              % (first + 1, box["w"], box["h"], box["x"], box["y"], tint, fade_in % logo_start_s)]
    if graph:
        assert graph.endswith("[video]")
        chains.insert(0, graph[:-len("[video]")] + "[looked]")
        picture = "[looked]"
    else:
        picture = "[0:v]"
    if border_start_s is None:
        chains.append("[%d:v]format=rgba%s[border]" % (first, tint))
        chains.append("%s[border]overlay=format=auto[bordered]" % picture)
    else:
        edges = len(BORDER_EDGES)
        chains.append("[%d:v]format=rgba%s,split=%d%s" % (first, tint, edges, "".join("[edge%d]" % i for i in range(edges))))
        for i, (x, y, w, h) in enumerate(BORDER_EDGES):
            chains.append("[edge%d]crop=%d:%d:%d:%d,%s[faded%d]" % (i, w, h, x, y, fade_in % border_start_s, i))
            source = picture if i == 0 else "[edged%d]" % (i - 1)
            target = "[bordered]" if i == edges - 1 else "[edged%d]" % i
            chains.append("%s[faded%d]overlay=x=%d:y=%d:format=auto:eof_action=pass%s" % (source, i, x, y, target))
    chains.append("[bordered][logo]overlay=x=%d:y=%d:format=auto:shortest=1[video]" % (box["x"], box["y"]))
    return extra_inputs, ";".join(chains)


def encoder_command(bounce, audio_start_s, frames, out, effects=None, work_dir=".", preset=FINAL_PRESET, crf=FINAL_CRF,
                    brand_dir=None, brand_colour="#ffffff", downbeats_s=(), title=None,
                    fonts_dir="/opt/hubd/static/shared/vendor/fonts", last_filters=None,
                    audio_fades_s=(AUDIO_EDGE_FADE_S, AUDIO_EDGE_FADE_S)):
    """title: the edit's title (title_render): its sequence goes after the effects and under the brand frame.
    brand_dir: the brand frame's folder (border and logo), or None for no brand frame.
    last_filters: filters on the finished picture, after everything else (the story's slice), or None.
    audio_fades_s: (fade in, fade out) of the sound at the video's start and end."""
    show_title = title_render.is_shown(title)
    command = render.encoder_cmd(bounce, audio_start_s, frames, out, preset=preset, crf=crf)
    pipe = command.index("-")                   # the raw video input: Rec. 709 (CAMERA_COLOUR)
    command[pipe - 1:pipe - 1] = PIPE_COLOUR_OPTIONS
    command[command.index("-b:a") + 1] = AUDIO_BITRATE
    command[command.index("-threads") + 1] = ENCODER_THREADS
    if effects or brand_dir or show_title or last_filters:
        # Inputs: 0 = the frames, 1 = the bounce, 2... = effect masks, then the border and logo.
        extra_inputs, graph = [], None
        if effects:
            extra_inputs, graph = look_render.effect_filter_graph(effects, work_dir, first_extra_input=2,
                                                                  downbeats_s=downbeats_s, audio_start_s=audio_start_s)
        if show_title:
            extra_inputs, graph = title_render.sequence_graph(graph, extra_inputs, title, work_dir, fonts_dir, FPS)
        if brand_dir:
            extra_inputs, graph = brand_overlay(graph, extra_inputs, brand_dir, brand_colour,
                                                title_render.logo_start_s(title),
                                                title_render.border_start_s(title))
        if last_filters:
            graph = (graph[:-len("[video]")] + "[finished];[finished]" if graph else "[0:v]") + last_filters + "[video]"
        audio_input_end = command.index(bounce) + 1
        command[audio_input_end:audio_input_end] = extra_inputs
        at = command.index("-map")
        command[at:at + 2] = ["-filter_complex", graph, "-map", "[video]"]
    quality = ["-profile:v", "high", "-g", str(FPS // 2), "-bf", "2", "-flags", "+cgop",
               "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:rc-lookahead=20",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    # The sound starts and stops wherever the video does (the start may be set just after
    # a clap): a very short fade at both ends, so neither clicks.
    end_s = frames / FPS
    fade_in, fade_out = audio_fades_s
    fades = ["-af", "afade=t=in:d=%.3f,afade=t=out:st=%.6f:d=%.3f" % (fade_in, max(0.0, end_s - fade_out), fade_out)]
    at = command.index("-c:a")
    command[at:at] = quality + fades
    return command


class EditRenderer:
    """Runs the pipe for one edit. busy(): suspend everything while true (a take or live
    session gets the hub). should_abort(): stop (raises render.Aborted)."""

    def __init__(self, edit, take_doc, video_dir, bounce, look, looks, work_dir,
                 busy=lambda: False, should_abort=lambda: False, progress=lambda done, total: None,
                 brand_dir=None, brand_colour="#ffffff", downbeats_s=(), fonts_dir=None):
        edit = trimmed(edit)                     # only the part between the video's start and end
        self.edit, self.take_doc, self.video_dir, self.bounce = edit, take_doc, video_dir, bounce
        self.title = edit.get("title")           # the title and the lines below it (title_render), or None
        self.fonts_dir = fonts_dir or "/opt/hubd/static/shared/vendor/fonts"
        self.brand_dir = brand_dir               # the border and logo on top (edit's "branding"), or None
        self.brand_colour = brand_colour         # their colour (edit's "branding_colour")
        self.downbeats_s = list(downbeats_s)     # the bounce's bars (beats.json), for the beat pulse
        self.busy, self.should_abort, self.progress = busy, should_abort, progress
        self.dissolve_s = float(edit.get("dissolve_s") or 0)
        self.shots, self.total_frames = plan_shots(edit, take_doc, self.dissolve_s)
        self.half_dissolve = int(round(self.dissolve_s * FPS / 2))
        self.effects = look_render.active_effects(look)
        self.work_dir = work_dir
        self.lut_path = look_render.write_look_lut(os.path.join(work_dir, "look.cube"), look, looks)
        self.pixel_format = "yuv420p"
        self.frame_bytes = WIDTH * HEIGHT * 3 // 2
        self.cameras = render.cameras(take_doc, include_phones=True)
        self.processes = []
        self.suspended = False
        self.black = None                        # a black frame through the look (lazily)

    def audio_start_s(self):
        return (self.edit["range_ns"][0] - self.edit["bounce_start_ns"]) / NS

    def file_seconds(self, camera_id, output_frame):
        hub_ns = self.edit["range_ns"][0] + output_frame * NS // FPS
        return (hub_ns - self.cameras[camera_id]["origin_ns"]) / NS

    def start_decoder(self, shot):
        shot.started = True
        first, count = shot.decoded_frames()
        if count <= 0:
            return                               # the camera recorded none of this shot: black
        source = os.path.join(self.video_dir, shot.camera_id + ".mov")
        command = decoder_command(source,
                                  self.file_seconds(shot.camera_id, first), count, self.lut_path, self.pixel_format,
                                  denoise=self.effects.get("denoise", 0), colour=source_colour(source))
        shot.process = self.spawn(command, stdout=subprocess.PIPE)

    def black_frame(self):
        """Where a camera didn't record: black, through the look's colour table (a faded
        look lifts black in the preview too)."""
        if self.black is None:
            command = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                       "color=c=black:s=%dx%d:r=%d" % (WIDTH, HEIGHT, FPS), "-frames:v", "1"]
            if self.lut_path:
                command += ["-vf", "lut3d=file='%s':interp=trilinear" % self.lut_path.replace("'", "")]
            self.black = subprocess.run(command + ["-f", "rawvideo", "-pix_fmt", self.pixel_format, "-"],
                                        capture_output=True, check=True).stdout
        return self.black

    def spawn(self, command, **kwargs):
        def lower_priority():
            try:
                with open("/proc/self/oom_score_adj", "w") as f:       # if memory runs out, the render goes, not the hub
                    f.write("1000")
            except OSError:
                pass
            os.nice(19)
        process = subprocess.Popen(command, stderr=subprocess.PIPE, preexec_fn=lower_priority, **kwargs)
        self.processes.append(process)
        return process

    def read_frame(self, shot, frame_number):
        """The shot's picture at this output frame: decoded, or black where its camera
        didn't record. A decoder may come up a frame or two short at the very end of its
        file (the last frame's rounding): the last picture is held then."""
        first, count = shot.decoded_frames()
        if shot.process is None or not first <= frame_number < first + count:
            return self.black_frame()
        data = shot.process.stdout.read(self.frame_bytes)
        if len(data) == self.frame_bytes:
            shot.last_picture = data
            return data
        if shot.last_picture is not None and frame_number >= first + count - 2:
            return shot.last_picture
        error = shot.process.stderr.read().decode(errors="replace")[-300:]
        raise RuntimeError("decoding %s ended early: %s" % (shot.camera_id, error))

    def as_array(self, data):
        return np.frombuffer(data, np.uint8)

    def wait_while_busy(self):
        """Suspend the decoders and encoder while the rig is busy; raise if asked to stop."""
        while True:
            if self.should_abort():
                raise render.Aborted("stopped")
            busy = self.busy()
            if busy != self.suspended:
                for process in self.processes:
                    if process.poll() is None:
                        process.send_signal(signal.SIGSTOP if busy else signal.SIGCONT)
                self.suspended = busy
            if not busy:
                return
            import time
            time.sleep(1.0)

    def encoder_command(self, out_path):
        """The x264 encoder's command (the story's renderer adds its slice: story_render.py)."""
        return encoder_command(self.bounce, self.audio_start_s(), self.total_frames, out_path,
                               self.effects, self.work_dir, brand_dir=self.brand_dir,
                               brand_colour=self.brand_colour, downbeats_s=self.downbeats_s,
                               title=self.title, fonts_dir=self.fonts_dir)

    def run(self, out_path):
        encoder = self.spawn(self.encoder_command(out_path), stdin=subprocess.PIPE)
        try:
            current = 0
            self.start_decoder(self.shots[0])
            for frame_number in range(self.total_frames):
                if frame_number % 15 == 0:
                    self.wait_while_busy()
                    self.progress(frame_number, self.total_frames)
                while self.shots[current].end_frame <= frame_number:
                    # The outgoing shot's decoder keeps running for the second half of the dissolve
                    # (finished below, once its overlap is read). Until 2026-11-24 it was finished
                    # here, at the cut, so every dissolve stopped half way and jumped to the new shot.
                    if self.shots[current].decode_to <= frame_number:
                        self.finish_decoder(self.shots[current])
                    current += 1
                shot = self.shots[current]
                upcoming = self.shots[current + 1] if current + 1 < len(self.shots) else None
                if upcoming and not upcoming.started and frame_number >= upcoming.decode_from:
                    # Normally started exactly at decode_from; after very short shots it can be
                    # later: then it starts at this frame (never delivering frames from too early).
                    upcoming.decode_from = frame_number
                    self.start_decoder(upcoming)
                picture = self.as_array(self.read_frame(shot, frame_number))
                if upcoming and upcoming.process is not None and frame_number >= upcoming.decode_from:
                    incoming = self.as_array(self.read_frame(upcoming, frame_number))
                    mix = dissolve_mix(frame_number, upcoming.first_frame, self.half_dissolve)
                    picture = self.dissolve(picture, incoming, mix, current + 1)
                elif current > 0 and frame_number < shot.first_frame + self.half_dissolve:
                    previous = self.shots[current - 1]
                    if previous.process is not None and frame_number < previous.decode_to:
                        outgoing = self.as_array(self.read_frame(previous, frame_number))
                        mix = dissolve_mix(frame_number, shot.first_frame, self.half_dissolve)
                        picture = self.dissolve(outgoing, picture, mix, current)
                encoder.stdin.write(picture.tobytes() if isinstance(picture, np.ndarray) else picture)
                # The outgoing shot's decoder is done once its overlap has been read.
                if current > 0 and self.shots[current - 1].process is not None and frame_number + 1 >= self.shots[current - 1].decode_to:
                    self.finish_decoder(self.shots[current - 1])
            for shot in self.shots:
                self.finish_decoder(shot)
            encoder.stdin.close()
            code = encoder.wait()
            if code:
                raise RuntimeError("encoder failed: %s" % encoder.stderr.read().decode(errors="replace")[-300:])
            self.progress(self.total_frames, self.total_frames)
        except BaseException:
            for process in self.processes:
                try:
                    if self.suspended:
                        process.send_signal(signal.SIGCONT)
                    process.kill()
                except OSError:
                    pass
            raise

    def dissolve(self, outgoing, incoming, mix, incoming_index):
        """One frame of the dissolve into shots[incoming_index]: `mix` of the incoming picture
        (the story's renderer lines the two slices up first: story_render.py)."""
        return (outgoing.astype(np.float32) * (1 - mix) + incoming.astype(np.float32) * mix + 0.5).astype(np.uint8)

    def finish_decoder(self, shot):
        if shot.process is None:
            return
        shot.process.stdout.close()
        shot.process.wait()
        shot.process.stderr.close()
        shot.process = None
