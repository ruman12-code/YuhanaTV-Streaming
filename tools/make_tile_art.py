#!/usr/bin/env python3
"""Draw the home-screen tile backgrounds.

Run once; the PNGs are committed. Nothing in the pipeline depends on Pillow.

    pip install Pillow && python3 tools/make_tile_art.py

The tiles previously used whatever frame sorted first out of the catalogue,
which on public-domain scans meant grainy monochrome stills. These are drawn
instead: a deep diagonal gradient, a soft light source, a large glyph and a
wordmark. 640x360 keeps each file around 20 KB, which matters when a TV loads
ten of them at once.
"""
from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "site" / "art"
W, H = 640, 360

# key: (glyph, caption, top-left colour, bottom-right colour, accent)
TILES = {
    "bangladesh":     ("BD", "BANGLADESH",   (0, 74, 56),   (0, 140, 88),  (240, 66, 54)),
    "movie-channels": ("▶",  "MOVIE CHANNELS", (74, 12, 30), (166, 34, 58), (255, 186, 92)),
    "series":         ("❐",  "TV SERIES",    (48, 16, 74),  (118, 52, 158), (255, 214, 102)),
    "sports":         ("◆",  "SPORTS",       (8, 58, 40),   (22, 138, 88), (198, 255, 120)),
    "hd-movies":      ("HD", "HD MOVIES",    (8, 44, 78),   (26, 118, 176), (126, 226, 255)),
    "top-rated":      ("★",  "TOP RATED",    (74, 54, 6),   (176, 132, 20), (255, 232, 140)),
    "movie-library":  ("🎞", "MOVIE LIBRARY", (62, 10, 24), (150, 34, 64), (255, 168, 120)),
    "kids":           ("☺",  "KIDS",         (120, 56, 4),  (226, 140, 26), (255, 236, 150)),
    "live-tv":        ("◉",  "ALL LIVE TV",  (14, 32, 78),  (40, 84, 176), (140, 190, 255)),
    "4k":             ("4K", "ULTRA HD",     (38, 14, 74),  (96, 44, 158), (196, 156, 255)),
    "news":           ("⬤",  "NEWS",         (74, 14, 14),  (162, 40, 40), (255, 170, 150)),
    "documentary":    ("◍",  "DOCUMENTARY",  (10, 56, 70),  (26, 122, 148), (150, 226, 240)),
    "music":          ("♪",  "MUSIC",        (78, 22, 8),   (176, 62, 24), (255, 190, 140)),
    # Home-screen tiles for the three doors into the catalogue.
    "my-channels":    ("★",  "MY CHANNELS",  (92, 66, 4),   (196, 148, 22), (255, 226, 130)),
    "by-country":     ("◍",  "BY COUNTRY",   (12, 34, 74),  (34, 90, 166),  (150, 198, 255)),
    "a-z":            ("A",  "A TO Z",       (28, 38, 46),  (72, 96, 112),  (196, 218, 236)),
    "favourites":     ("♥",  "FAVOURITES",   (88, 14, 34),  (182, 44, 76),  (255, 170, 190)),
}


def _font(size: int, bold: bool = True):
    for name in ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def gradient(c1, c2) -> Image.Image:
    """Diagonal gradient, computed per row and sheared for the diagonal."""
    base = Image.new("RGB", (W, H))
    px = base.load()
    for y in range(H):
        for x in range(0, W, 4):
            t = (x / W * 0.6 + y / H * 0.4)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))
            for dx in range(1, 4):
                if x + dx < W:
                    px[x + dx, y] = px[x, y]
    return base


def draw_tile(key: str, glyph: str, caption: str, c1, c2, accent) -> Path:
    img = gradient(c1, c2)

    # Soft light source, top-left, so the tile does not read as flat.
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((-160, -220, 420, 240), fill=120)
    img = Image.composite(Image.new("RGB", (W, H), tuple(min(255, c + 46) for c in c2)),
                          img, glow.filter(ImageFilter.GaussianBlur(90)))

    # No text is drawn. SS IPTV writes its own label over the tile, and a baked-in
    # wordmark sat underneath it: two overlapping strings, neither readable. The
    # art is a background now, and the app supplies the only words on the tile.
    #
    # The bottom third is darkened so whatever the app writes there has contrast
    # to sit against, whichever colour it chooses.
    shade = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shade).rectangle((0, int(H * 0.55), W, H), fill=150)
    img = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), img,
                          shade.filter(ImageFilter.GaussianBlur(40)))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, r in enumerate(range(150, 460, 52)):
        d.ellipse((W - 150 - r, H - 60 - r, W - 150 + r, H - 60 + r),
                  outline=(*accent, 24 if i % 2 else 14), width=2)

    # A single large glyph, top-right, well clear of the label area.
    gf = _font(150)
    bbox = d.textbbox((0, 0), glyph, font=gf)
    d.text((W - 70 - (bbox[2] - bbox[0]), 18), glyph, font=gf, fill=(*accent, 70))

    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{key}.png"
    img.save(path, "PNG", optimize=True)
    return path


def main() -> None:
    total = 0
    for key, spec in TILES.items():
        p = draw_tile(key, *spec)
        size = p.stat().st_size
        total += size
        print(f"  {p.name:22} {size // 1024:3} KB")
    print(f"  {len(TILES)} tiles, {total // 1024} KB total")


if __name__ == "__main__":
    main()
