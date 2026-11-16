#!/usr/bin/env python3
"""Stands in for rpicam-still in tests: writes a tiny JPEG-looking blob to stdout,
or fails if the lens position is 99, or sleeps if it is 98 (to test the busy state)."""
import sys, time
a = sys.argv
lp = a[a.index("--lens-position") + 1]
if lp == "99":
    sys.stderr.write("ERROR: no cameras available\n")
    sys.exit(1)
if lp == "98":
    time.sleep(1.0)
if lp == "97":                 # hangs past the snapshot timeout
    time.sleep(30)
if "--metadata" in a:
    auto = "--gain" not in a
    with open(a[a.index("--metadata") + 1], "w") as f:
        f.write('{"AnalogueGain": %s, "ColourGains": [1.9, 2.6], "Lux": 30.0}' % ("12.5" if auto else a[a.index("--gain") + 1]))
sys.stdout.buffer.write(b"\xff\xd8fakejpeg lens=" + lp.encode() + b"\xff\xd9")
