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
  - the look's effects are applied per frame in numpy (look_render.py);
  - frames go to one x264 encoder, which also takes the bounce as the only sound.

Without effects the frames stay YUV (no conversion); a dissolve mixes YUV frames, which
is the same as mixing RGB (the conversion is linear). Memory: at most two decoders plus
the encoder (spike E3 measured ~340 MB for the encoder, ~90 MB per decoder at 2 threads).

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
    """One shot of the cut, in output frames, with the frames its decoder must deliver."""

    def __init__(self, camera_id, first_frame, end_frame):
        self.camera_id = camera_id
        self.first_frame, self.end_frame = first_frame, end_frame       # what's on screen
        self.decode_from, self.decode_to = first_frame, end_frame       # plus dissolve overlap
        self.process = None


def plan_shots(edit, take_doc, dissolve_s):
    """The shots of the edit, each widened by half the dissolve on both sides where the
    camera has those frames. Returns (shots, total frames)."""
    cameras = render.cameras(take_doc)
    range_start, range_end = edit["range_ns"]
    total = int(round((range_end - range_start) * FPS / NS))
    bounds = [int(round(cut["at_ns"] * FPS / NS)) for cut in edit["cuts"]] + [total]
    shots = [Shot(cut["camera"], first, end) for cut, first, end in zip(edit["cuts"], bounds, bounds[1:]) if end > first]
    half = int(round(dissolve_s * FPS / 2))
    for i, shot in enumerate(shots):
        camera = cameras.get(shot.camera_id)
        if camera is None:
            raise ValueError("camera %s isn't in this take" % shot.camera_id)
        # Frames this camera has (output frame numbers), with a frame of margin at each end.
        has_from = int(np.ceil((camera["start_ns"] - range_start) * FPS / NS)) + 1
        has_to = int(np.floor((camera["end_ns"] - range_start) * FPS / NS)) - 1
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
    return ["ffmpeg", "-nostdin", "-v", "error", "-threads", "2", "-ss", "%.6f" % max(0.0, start_s), "-i", source,
            "-vf", filters, "-frames:v", str(frames), "-f", "rawvideo", "-pix_fmt", pixel_format, "-"]


# The finished video is the one that goes to YouTube (user, 2026-11-16: "high quality,
# ready to push up"), so it follows YouTube's recommended upload settings and spends
# encoding time on quality: H.264 High profile, preset slow, CRF 16, a keyframe every
# half second (closed GOP, half the frame rate), 2 B-frames, Rec. 709 colour tags, AAC-LC
# 384 kbit/s 48 kHz stereo, moov at the front (faststart).
FINAL_PRESET = "slow"
FINAL_CRF = "16"
AUDIO_BITRATE = "384k"


def encoder_command(bounce, audio_start_s, frames, out, pixel_format, preset=FINAL_PRESET, crf=FINAL_CRF):
    command = render.encoder_cmd(bounce, audio_start_s, frames, out, preset=preset, crf=crf)
    command[command.index("-pix_fmt") + 1] = pixel_format             # the raw input's format
    command[command.index("-b:a") + 1] = AUDIO_BITRATE
    if pixel_format == "rgb24":                                       # effects ran in RGB: back to Rec. 709 YUV
        command[command.index("-map"):command.index("-map")] = ["-vf", "scale=out_color_matrix=bt709:out_range=tv"]
    quality = ["-profile:v", "high", "-g", str(FPS // 2), "-bf", "2", "-flags", "+cgop",
               "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
               "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
    at = command.index("-c:a")
    command[at:at] = quality
    return command


class EditRenderer:
    """Runs the pipe for one edit. busy(): suspend everything while true (a take or live
    session gets the hub). should_abort(): stop (raises render.Aborted)."""

    def __init__(self, edit, take_doc, video_dir, bounce, look, looks, work_dir,
                 busy=lambda: False, should_abort=lambda: False, progress=lambda done, total: None):
        self.edit, self.take_doc, self.video_dir, self.bounce = edit, take_doc, video_dir, bounce
        self.busy, self.should_abort, self.progress = busy, should_abort, progress
        self.dissolve_s = float(edit.get("dissolve_s") or 0)
        self.shots, self.total_frames = plan_shots(edit, take_doc, self.dissolve_s)
        self.half_dissolve = int(round(self.dissolve_s * FPS / 2))
        self.effects = look_render.EffectProcessor(look_render.active_effects(look), WIDTH, HEIGHT, FPS)
        self.lut_path = look_render.write_look_lut(os.path.join(work_dir, "look.cube"), look, looks)
        self.pixel_format = "rgb24" if self.effects.effects else "yuv420p"
        self.frame_bytes = WIDTH * HEIGHT * (3 if self.pixel_format == "rgb24" else 1.5)
        self.frame_bytes = int(self.frame_bytes)
        self.cameras = render.cameras(take_doc)
        self.processes = []
        self.suspended = False

    def audio_start_s(self):
        return (self.edit["range_ns"][0] - self.edit["bounce_start_ns"]) / NS

    def file_seconds(self, camera_id, output_frame):
        hub_ns = self.edit["range_ns"][0] + output_frame * NS // FPS
        return (hub_ns - self.cameras[camera_id]["origin_ns"]) / NS

    def start_decoder(self, shot):
        command = decoder_command(os.path.join(self.video_dir, shot.camera_id + ".mov"),
                                  self.file_seconds(shot.camera_id, shot.decode_from),
                                  shot.decode_to - shot.decode_from, self.lut_path, self.pixel_format)
        shot.process = self.spawn(command, stdout=subprocess.PIPE)

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

    def read_frame(self, shot):
        data = shot.process.stdout.read(self.frame_bytes)
        if len(data) != self.frame_bytes:
            error = shot.process.stderr.read().decode(errors="replace")[-300:]
            raise RuntimeError("decoding %s ended early: %s" % (shot.camera_id, error))
        return data

    def as_array(self, data):
        if self.pixel_format == "rgb24":
            return np.frombuffer(data, np.uint8).reshape(HEIGHT, WIDTH, 3)
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
                                             self.pixel_format), stdin=subprocess.PIPE)
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
                if upcoming and upcoming.process is None and frame_number >= upcoming.decode_from:
                    # Normally started exactly at decode_from; after very short shots it can be
                    # later: then it starts at this frame (never delivering frames from too early).
                    upcoming.decode_from = frame_number
                    self.start_decoder(upcoming)
                picture = self.as_array(self.read_frame(shot))
                if upcoming and upcoming.process is not None and frame_number >= upcoming.decode_from:
                    incoming = self.as_array(self.read_frame(upcoming))
                    mix = dissolve_mix(frame_number, upcoming.first_frame, self.half_dissolve)
                    picture = (picture.astype(np.float32) * (1 - mix) + incoming.astype(np.float32) * mix + 0.5).astype(np.uint8)
                elif current > 0 and frame_number < shot.first_frame + self.half_dissolve:
                    previous = self.shots[current - 1]
                    if previous.process is not None and frame_number < previous.decode_to:
                        outgoing = self.as_array(self.read_frame(previous))
                        mix = dissolve_mix(frame_number, shot.first_frame, self.half_dissolve)
                        picture = (outgoing.astype(np.float32) * (1 - mix) + picture.astype(np.float32) * mix + 0.5).astype(np.uint8)
                if self.pixel_format == "rgb24":
                    picture = self.effects.process(picture, frame_number)
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
