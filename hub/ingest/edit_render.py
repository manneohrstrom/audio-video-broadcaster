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
import os
import signal
import subprocess

import numpy as np

import look_render
import render

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

    def __init__(self, camera_id, first_frame, end_frame):
        self.camera_id = camera_id
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
    shots = [Shot(cut["camera"], first, end) for cut, first, end in zip(edit["cuts"], bounds, bounds[1:]) if end > first]
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


def dissolve_mix(frame_number, cut_frame, half_frames):
    """How much of the incoming shot shows (0..1) around a cut: smoothstep over the dissolve."""
    if half_frames <= 0:
        return 1.0 if frame_number >= cut_frame else 0.0
    # Frame times are frame starts; the cut is at cut_frame (same as the preview's song time).
    t = 0.5 + (frame_number - cut_frame) / (2.0 * half_frames)
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def decoder_command(source, start_s, frames, lut_path, pixel_format):
    filters = "fps=%d:round=near,scale=%d:%d" % (FPS, WIDTH, HEIGHT)
    if lut_path:
        filters += ",lut3d=file='%s':interp=trilinear" % lut_path.replace("'", "")
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


def brand_colour_filter(colour):
    """lutrgb turning the brand frame's white into `colour` ("#rrggbb"); alpha untouched.
    The frame is pure white with transparency, so this is the colour exactly."""
    red, green, blue = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return "lutrgb=r=val*%d/255:g=val*%d/255:b=val*%d/255" % (red, green, blue)


def brand_overlay(graph, extra_inputs, brand_frame, colour="#ffffff"):
    """Put the brand frame (border and logo, a white 1920x1080 PNG with transparency, in
    `colour`) on top of the finished picture: the last step, after the look, its effects and
    the grain, so nothing changes it. graph: the effects' filter graph ending in [video], or None.
    One frame, no -loop: overlay repeats a finished input's last frame (eof_action repeat)."""
    brand_input = 2 + sum(1 for part in extra_inputs if part == "-i")
    extra_inputs = extra_inputs + ["-i", brand_frame]
    brand = "[%d:v]" % brand_input
    chains = []
    if colour.lower() != "#ffffff":
        chains.append("%sformat=rgba,%s[brand]" % (brand, brand_colour_filter(colour)))
        brand = "[brand]"
    if graph:
        assert graph.endswith("[video]")
        chains.insert(0, graph[:-len("[video]")] + "[looked]")
        chains.append("[looked]%soverlay=format=auto[video]" % brand)
    else:
        chains.append("[0:v]%soverlay=format=auto[video]" % brand)
    return extra_inputs, ";".join(chains)


def encoder_command(bounce, audio_start_s, frames, out, effects=None, work_dir=".", preset=FINAL_PRESET, crf=FINAL_CRF,
                    brand_frame=None, brand_colour="#ffffff"):
    command = render.encoder_cmd(bounce, audio_start_s, frames, out, preset=preset, crf=crf)
    command[command.index("-b:a") + 1] = AUDIO_BITRATE
    command[command.index("-threads") + 1] = ENCODER_THREADS
    if effects or brand_frame:
        # Inputs: 0 = the frames, 1 = the bounce, 2... = effect masks, then the brand frame.
        extra_inputs, graph = [], None
        if effects:
            extra_inputs, graph = look_render.effect_filter_graph(effects, work_dir, first_extra_input=2)
        if brand_frame:
            extra_inputs, graph = brand_overlay(graph, extra_inputs, brand_frame, brand_colour)
        audio_input_end = command.index(bounce) + 1
        command[audio_input_end:audio_input_end] = extra_inputs
        at = command.index("-map")
        command[at:at + 2] = ["-filter_complex", graph, "-map", "[video]"]
    quality = ["-profile:v", "high", "-g", str(FPS // 2), "-bf", "2", "-flags", "+cgop",
               "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:rc-lookahead=20",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    at = command.index("-c:a")
    command[at:at] = quality
    return command


class EditRenderer:
    """Runs the pipe for one edit. busy(): suspend everything while true (a take or live
    session gets the hub). should_abort(): stop (raises render.Aborted)."""

    def __init__(self, edit, take_doc, video_dir, bounce, look, looks, work_dir,
                 busy=lambda: False, should_abort=lambda: False, progress=lambda done, total: None,
                 brand_frame=None, brand_colour="#ffffff"):
        self.edit, self.take_doc, self.video_dir, self.bounce = edit, take_doc, video_dir, bounce
        self.brand_frame = brand_frame           # the border and logo on top (edit's "branding"), or None
        self.brand_colour = brand_colour         # their colour (edit's "branding_colour")
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
        command = decoder_command(os.path.join(self.video_dir, shot.camera_id + ".mov"),
                                  self.file_seconds(shot.camera_id, first), count, self.lut_path, self.pixel_format)
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

    def run(self, out_path):
        encoder = self.spawn(encoder_command(self.bounce, self.audio_start_s(), self.total_frames, out_path,
                                             self.effects, self.work_dir, brand_frame=self.brand_frame,
                                             brand_colour=self.brand_colour),
                             stdin=subprocess.PIPE)
        try:
            current = 0
            self.start_decoder(self.shots[0])
            for frame_number in range(self.total_frames):
                if frame_number % 15 == 0:
                    self.wait_while_busy()
                    self.progress(frame_number, self.total_frames)
                while self.shots[current].end_frame <= frame_number:
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
                    picture = (picture.astype(np.float32) * (1 - mix) + incoming.astype(np.float32) * mix + 0.5).astype(np.uint8)
                elif current > 0 and frame_number < shot.first_frame + self.half_dissolve:
                    previous = self.shots[current - 1]
                    if previous.process is not None and frame_number < previous.decode_to:
                        outgoing = self.as_array(self.read_frame(previous, frame_number))
                        mix = dissolve_mix(frame_number, shot.first_frame, self.half_dissolve)
                        picture = (outgoing.astype(np.float32) * (1 - mix) + picture.astype(np.float32) * mix + 0.5).astype(np.uint8)
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

    def finish_decoder(self, shot):
        if shot.process is None:
            return
        shot.process.stdout.close()
        shot.process.wait()
        shot.process.stderr.close()
        shot.process = None
