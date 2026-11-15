#!/usr/bin/env python3
"""Cut decodable snippets around given frame numbers from a raw rpicam-vid H.264 file.

usage: cutframes.py <file.h264> <outdir> <count> N1 [N2 ...]
       writes <outdir>/cut_<N>.h264 and prints "<N> <first_frame_index>" per snippet.

rpicam-vid writes SPS/PPS only at the start (no --inline) and no B-frames, so a
snippet is: header NALs from the file start + bytes from the last IDR at or
before N up to the end of frame N+count-1. Decoded frame k of the snippet is
frame (first_frame_index + k) of the original file. No decoding needed (nodes
have no ffmpeg): frames are counted by slice NALs with first_mb_in_slice == 0.
One pass over the file for all N.
"""
import mmap
import os
import sys

path, outdir, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
targets = sorted(int(x) for x in sys.argv[4:])
SC = b"\x00\x00\x01"

with open(path, "rb") as f:
    m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    header = bytearray()
    frame = -1
    last_idr = (None, None)  # (offset, index)
    pending = []  # [N, idr_off, idr_idx]
    ti = 0
    pos = m.find(SC)
    while pos != -1 and (ti < len(targets) or pending):
        start = pos - 1 if pos > 0 and m[pos - 1] == 0 else pos
        nal_type = m[pos + 3] & 0x1F
        if nal_type in (7, 8) and frame < 0:
            nxt = m.find(SC, pos + 3)
            nxt = len(m) if nxt == -1 else (nxt - 1 if m[nxt - 1] == 0 else nxt)
            header += m[start:nxt]
        elif nal_type in (1, 5) and (m[pos + 4] & 0x80):
            frame += 1
            # close snippets whose last frame has ended
            for p in pending[:]:
                if frame == p[0] + count:
                    with open(os.path.join(outdir, "cut_%d.h264" % p[0]), "wb") as o:
                        o.write(header)
                        o.write(m[p[1]:start])
                    print(p[0], p[2])
                    pending.remove(p)
            if nal_type == 5:
                last_idr = (start, frame)
            while ti < len(targets) and targets[ti] == frame:
                pending.append([targets[ti], last_idr[0], last_idr[1]])
                ti += 1
        pos = m.find(SC, pos + 3)
    for p in pending:  # file ended
        with open(os.path.join(outdir, "cut_%d.h264" % p[0]), "wb") as o:
            o.write(header)
            o.write(m[p[1]:])
        print(p[0], p[2])
