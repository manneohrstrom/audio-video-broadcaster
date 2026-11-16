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
sys.stdout.buffer.write(b"\xff\xd8fakejpeg lens=" + lp.encode() + b"\xff\xd9")
