#!/usr/bin/env python3
"""Make the calibration card PDF from card.json (docs/phase4-plan.md, Track B, B2).

usage: make_card.py [card.json] [out.pdf]
Print A4 landscape at 100% (no "fit to page"), in colour, on matte paper; glue it flat.
Standard library only; the marker bits in card.json came from OpenCV's DICT_4X4_50."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PT = 72 / 25.4          # points per mm


def pdf(card):
    W, H = card["page"]
    ops = []

    def rect(x, y, w, h, rgb):      # card coordinates: mm, origin top-left
        ops.append("%.4f %.4f %.4f rg %.2f %.2f %.2f %.2f re f" % (
            rgb[0] / 255, rgb[1] / 255, rgb[2] / 255, x * PT, (H - y - h) * PT, w * PT, h * PT))

    for m in card["markers"].values():
        n = len(m["bits"])
        c = m["size"] / n
        for r, row in enumerate(m["bits"]):
            for k, v in enumerate(row):
                if not v:
                    rect(m["x"] + k * c, m["y"] + r * c, c, c, (0, 0, 0))
    rect(*card["grey"]["rect"], card["grey"]["srgb"])
    for p in card["patches"]:
        rect(*p["rect"], p["srgb"])
    ops.append("BT /F1 8 Tf 0 0 0 rg %.2f %.2f Td (Studio Multicam calibration card v%d - print A4 landscape, 100%%"
               " scale, colour, matte paper; glue flat. Hold where the performers will be.) Tj ET"
               % (12 * PT, 8 * PT, card["version"]))
    stream = "\n".join(ops).encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Contents 4 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >> >>" % (W * PT, H * PT),
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "card.json")
    dst = sys.argv[2] if len(sys.argv) > 2 else "calibration-card.pdf"
    with open(src) as f:
        card = json.load(f)
    with open(dst, "wb") as f:
        f.write(pdf(card))
    print(dst)
