"""Find the calibration card in a picture and read its patches (docs/phase4-plan.md, Track B).

The card's layout (marker positions, patches, all in mm) is card.json, made with
hub/calib/make_card.py. The card may be printed at any scale: positions are mapped
from the markers' corners, so only the proportions matter. OpenCV is imported on first
use, so hubd starts without it (and its memory) until a card is measured."""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_cv = None


def _cv2():
    global _cv
    if _cv is None:
        import cv2
        import numpy
        _cv = (cv2, numpy, cv2.aruco.ArucoDetector(
            cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), cv2.aruco.DetectorParameters()))
    return _cv


def load_card(path=None):
    for p in (path, os.path.join(HERE, "card.json"), os.path.join(HERE, "..", "calib", "card.json")):
        if p and os.path.exists(p):
            with open(p) as f:
                return json.load(f)
    raise FileNotFoundError("card.json")


def _inner(rect, keep=0.6):
    """The middle part of a patch: away from its edges and any misregistration."""
    x, y, w, h = rect
    m = (1 - keep) / 2
    return x + w * m, y + h * m, w * keep, h * keep


def measure(jpeg, card):
    """jpeg: bytes. Returns {"found": [marker ids], "marker_px", "grey": [r, g, b], "grey_spread",
    "patches": {name: [r, g, b]}} in 8-bit sRGB as the device encoded it, or found=[] if no card."""
    cv2, np, det = _cv2()
    im = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        raise ValueError("not an image")
    corners, ids, _ = det.detectMarkers(im)
    out = {"found": [], "size": [im.shape[1], im.shape[0]]}
    if ids is None:
        return out
    src, dst, sides = [], [], []
    for c, i in zip(corners, ids.flatten()):
        m = card["markers"].get(str(int(i)))
        if not m:
            continue
        x, y, s = m["x"], m["y"], m["size"]
        src += [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]      # OpenCV order: TL, TR, BR, BL
        dst += [tuple(p) for p in c[0]]
        sides.append(float(np.mean([np.linalg.norm(c[0][k] - c[0][(k + 1) % 4]) for k in range(4)])))
        out["found"].append(int(i))
    if not src:
        return out
    H, _ = cv2.findHomography(np.float32(src), np.float32(dst))
    out["marker_px"] = round(max(sides), 1)
    # Warp the card to 2 px/mm (a flat, square-on view) and average the patch interiors.
    W, Hh = card["page"]
    S = np.array([[2, 0, 0], [0, 2, 0], [0, 0, 1]], np.float64)
    flat = cv2.warpPerspective(im, S @ np.linalg.inv(H), (int(W * 2), int(Hh * 2)), flags=cv2.INTER_AREA)

    def read(rect):
        x, y, w, h = (int(round(v * 2)) for v in _inner(rect))
        p = flat[y:y + h, x:x + w].reshape(-1, 3).astype(np.float64)
        bgr = p.mean(axis=0)
        return [round(float(v), 1) for v in bgr[::-1]], round(float(p.std(axis=0).mean()), 1)

    out["grey"], out["grey_spread"] = read(card["grey"]["rect"])
    out["patches"] = {p["name"]: read(p["rect"])[0] for p in card["patches"]}
    return out
