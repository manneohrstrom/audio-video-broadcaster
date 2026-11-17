"""Make the brand frame: the white border and the Brockley Live logo for the YouTube video
(user decision 2026-11-17, option A of the mock-ups).

One 1920x1080 PNG, transparent except for the border and the logo. The editor lays it over
the video window and the render overlays it last (after the look, its effects and grain),
so the preview and the master can't differ. Run on the Mac (needs Pillow) after changing
the logo or the numbers below, and commit the result:

    python3 hub/tools/make_brand_frame.py
"""
import os

from PIL import Image

WIDTH, HEIGHT = 1920, 1080
BORDER_PX = 24          # white border on every side; covers the picture's edges
LOGO_WIDTH_PX = 345     # the logo's width (its empty margin trimmed off), 18 % of the frame
LOGO_GAP_PX = 32        # from the border's inner edge to the logo, right and below

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGO = os.path.join(REPO, "hub", "brand", "brockley-live-logo.png")
OUT = os.path.join(REPO, "hub", "hubd", "static", "editor", "brand", "brand-frame.png")


def make_frame(logo_path=LOGO):
    frame = Image.new("RGBA", (WIDTH, HEIGHT), (255, 255, 255, 0))
    white = Image.new("RGBA", (WIDTH, HEIGHT), (255, 255, 255, 255))
    inner = Image.new("L", (WIDTH - 2 * BORDER_PX, HEIGHT - 2 * BORDER_PX), 0)
    mask = Image.new("L", (WIDTH, HEIGHT), 255)
    mask.paste(inner, (BORDER_PX, BORDER_PX))
    frame.paste(white, (0, 0), mask)

    logo = Image.open(logo_path).convert("RGBA")
    logo = logo.crop(logo.getbbox())
    height = round(logo.height * LOGO_WIDTH_PX / logo.width)
    logo = logo.resize((LOGO_WIDTH_PX, height), Image.LANCZOS)
    at = (WIDTH - BORDER_PX - LOGO_GAP_PX - LOGO_WIDTH_PX, HEIGHT - BORDER_PX - LOGO_GAP_PX - height)
    frame.alpha_composite(logo, at)
    return frame


if __name__ == "__main__":
    make_frame().save(OUT, optimize=True)
    print("wrote", OUT)
