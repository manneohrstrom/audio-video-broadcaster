"""Hear the beat finder: the song with a click on every beat it found (higher on the first
beat of each bar), to judge by ear whether beats.py follows the band.

Run on the Mac: python3 beat_clicks.py <song> <beats.json> <out.wav> [from_s] [to_s]

The song is turned down 6 dB so the clicks stand out; output is 48 kHz stereo WAV.
"""
import json
import subprocess
import sys

import numpy as np

RATE = 48000


def click(freq_hz, seconds=0.03, level=0.5):
    """A short sine blip with a fast decay."""
    t = np.arange(int(seconds * RATE)) / RATE
    return (level * np.sin(2 * np.pi * freq_hz * t) * np.exp(-t / (seconds / 4))).astype(np.float32)


def main(song, beats_path, out_path, start_s=0.0, end_s=None):
    beats = json.load(open(beats_path))
    command = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start_s)]
    if end_s is not None:
        command += ["-t", str(end_s - start_s)]
    command += ["-i", song, "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-"]
    audio = np.frombuffer(subprocess.run(command, capture_output=True, check=True).stdout, "<f4")
    audio = audio.reshape(-1, 2) * 0.5
    downbeats = set(beats["downbeats_s"])
    bar_click, beat_click = click(1760), click(880)
    for time_s in beats["beats_s"]:
        at = int(round((time_s - start_s) * RATE))
        blip = bar_click if time_s in downbeats else beat_click
        if 0 <= at < len(audio) - len(blip):
            audio[at:at + len(blip)] += blip[:, None]
    np.clip(audio, -1, 1, out=audio)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", str(RATE), "-ac", "2",
                    "-i", "-", out_path], input=audio.astype("<f4").tobytes(), check=True)


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5, 6):
        sys.exit(__doc__.strip().splitlines()[2])
    main(sys.argv[1], sys.argv[2], sys.argv[3], *(float(v) for v in sys.argv[4:]))
