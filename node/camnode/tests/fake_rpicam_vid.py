#!/usr/bin/env python3
"""Stand-in for rpicam-vid in tests. Behaviour via env:
FAKE_SYNC_DELAY (s, default 0.2), FAKE_DROP (comma list of frame indexes to skip),
FAKE_CRASH=1 (on stop: exit 134 without writing --save-pts),
FAKE_DIE_AFTER (s: exit 1 on its own), FAKE_IGNORE_SIGNAL=1."""
import os, signal, sys, time
a = sys.argv
out = {k: a[a.index(k) + 1] for k in ("-o", "--save-pts", "--metadata")}
stop = False
def on_sig(*_):
    global stop
    stop = True
if not os.environ.get("FAKE_IGNORE_SIGNAL"):
    signal.signal(signal.SIGINT, on_sig); signal.signal(signal.SIGUSR2, on_sig)
else:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
drop = {int(x) for x in os.environ.get("FAKE_DROP", "").split(",") if x}
print("Sync mode set to " + a[a.index("--sync") + 1], flush=True)
time.sleep(float(os.environ.get("FAKE_SYNC_DELAY", "0.2")))
print("*** Sync achieved! Difference %sus" % os.environ.get("FAKE_SYNC_DIFF", "3"), flush=True)
die = float(os.environ.get("FAKE_DIE_AFTER", "0") or 0)
t0 = time.time(); wc0 = time.time_ns(); ts0 = 1_000_000_000; pts = []
meta = open(out["--metadata"], "w"); meta.write("[\n"); vid = open(out["-o"], "wb")
i = 0
while not stop:
    if die and time.time() - t0 > die:
        sys.exit(1)
    if i not in drop:
        off = int(i * 1e9 / 30)
        meta.write('%s{\n    "SensorTimestamp": %d,\n    "FrameWallClock": %d,\n    "LensPosition": 1\n}\n'
                   % ("," if pts else "", ts0 + off, wc0 + off))
        meta.flush(); vid.write(b"\0" * 100); pts.append(off / 1e6)
    i += 1
    time.sleep(0.005)   # fast clock: 200 "frames"/s of wall time
meta.write("]\n"); meta.close(); vid.close()
print("Received signal 2", flush=True)
if os.environ.get("FAKE_CRASH"):
    print("double free or corruption (fasttop)", flush=True); os._exit(134)
with open(out["--save-pts"], "w") as f:
    f.write("# timecode format v2\n" + "".join("%.3f\n" % p for p in pts))
