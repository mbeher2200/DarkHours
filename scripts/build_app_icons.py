#!/usr/bin/env python3
"""Build the PWA install icons served from the web app's manifest.

Input:  none — the artwork is the score-band mark defined inline below, the same
        geometry and palette as apps/web/public/favicon.svg and
        apps/web/public/apple-touch-icon.png (a 32-unit square: dark rounded
        tile, four rounded score bars).
Output: apps/web/public/icon-192.png          192x192 RGBA, purpose "any"
        apps/web/public/icon-512.png          512x512 RGBA, purpose "any"
        apps/web/public/icon-maskable-512.png 512x512 RGB,  purpose "maskable"
        Regenerate all three with:

    python scripts/build_app_icons.py

The "any" icons reproduce the mark as drawn: rounded tile, transparent corners.

The maskable icon is a different composition, not a rescale. The platform
supplies the silhouette (circle, squircle, rounded square, teardrop) and crops
to it, so the tile is drawn full-bleed and opaque — no rounding, no transparent
corners — and the bars are scaled to sit inside the safe zone, the centred
circle of diameter 80% of the icon that every mask is guaranteed to keep. The
bar block is wider than it is tall, so the binding constraint is its corners:
the scale factor puts the block's half-diagonal exactly on the safe radius,
which lands the bars at ~59% of the icon width. That looks small next to the
"any" icon and is correct — the mask eats the rest.

Requires: pillow (pip install pillow — dev-only, not in requirements).
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

# Artwork in the 32-unit space of apps/web/public/favicon.svg.
TILE_FILL = "#1E1E1E"
TILE_RADIUS = 4.0
# (x, y, w, h, fill) — poor / fair / good / excellent score bands, top to bottom.
BARS = [
    (4.0, 5.0, 14.0, 4.0, "#D9534F"),
    (4.0, 11.0, 20.0, 4.0, "#F0AD4E"),
    (4.0, 17.0, 10.0, 4.0, "#5CB85C"),
    (4.0, 23.0, 24.0, 4.0, "#5BC0DE"),
]
BAR_RADIUS = 1.0
VIEWBOX = 32.0

# Supersampling factor for the draw pass; the result is downsampled with LANCZOS
# so the rounded corners come out antialiased (ImageDraw itself does not).
SS = 8

# Fraction of the icon's size spanned by the maskable safe zone's diameter.
SAFE_ZONE = 0.8


def _draw(size: int, *, maskable: bool) -> Image.Image:
    """Render the mark at `size` px, as the plain tile or the maskable variant."""
    px = size * SS
    scale = px / VIEWBOX

    if maskable:
        img = Image.new("RGB", (px, px), TILE_FILL)
    else:
        img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle(
            (0, 0, px - 1, px - 1), radius=TILE_RADIUS * scale, fill=TILE_FILL,
        )

    # Shrink the bars into the safe circle for the maskable variant. The bar
    # block is centred on the tile, so this is a scale about the centre.
    if maskable:
        xs = [x for x, _, w, _, _ in BARS for x in (x, x + w)]
        ys = [y for _, y, _, h, _ in BARS for y in (y, y + h)]
        half_w = (max(xs) - min(xs)) / 2
        half_h = (max(ys) - min(ys)) / 2
        half_diagonal = (half_w**2 + half_h**2) ** 0.5
        k = (SAFE_ZONE / 2 * VIEWBOX) / half_diagonal
    else:
        k = 1.0

    centre = VIEWBOX / 2
    draw = ImageDraw.Draw(img)
    for x, y, w, h, fill in BARS:
        x0, y0 = centre + (x - centre) * k, centre + (y - centre) * k
        x1, y1 = centre + (x + w - centre) * k, centre + (y + h - centre) * k
        draw.rounded_rectangle(
            (x0 * scale, y0 * scale, x1 * scale, y1 * scale),
            radius=BAR_RADIUS * k * scale,
            fill=fill,
        )

    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    default_out = Path(__file__).resolve().parents[1] / "apps" / "web" / "public"
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "-o", "--out-dir", type=Path, default=default_out,
        help=f"directory to write the icons into (default: {default_out})",
    )
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for name, size, maskable in (
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
    ):
        path = args.out_dir / name
        _draw(size, maskable=maskable).save(path, optimize=True)
        print(f"[icons] wrote {path} ({size}x{size}{', maskable' if maskable else ''})")


if __name__ == "__main__":
    main()
