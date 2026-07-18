#!/usr/bin/env python3
"""Build the Milky Way band texture served to the web sky-dome renderer.

Input:  the ESO/S. Brunier GigaGalaxy Zoom all-sky panorama (eso0932a) —
        an equirectangular 360°×180° image in galactic coordinates, credit
        ESO/S. Brunier, licensed CC BY 4.0 (https://www.eso.org/public/images/eso0932a/).
        Downloaded on demand (~8 MB JPEG); the source image is NOT committed.
Output: apps/web/public/mw.v1.png — 1024×256 RGB, galactic longitude l = 0→360°
        left→right (wrapping), latitude b = +45° (top) → −45° (bottom).
        Regenerate with:

    python scripts/build_mw_texture.py -o apps/web/public/mw.v1.png

The output filename is versioned (mw.v1.png): CloudFront serves apps/web/public/
assets with a long-TTL cache policy, so any format/mapping change must bump the
version suffix rather than rewrite the same path.

Requires: pillow, numpy (pip install pillow numpy — dev-only, not in requirements).

Processing pipeline (tuned for the renderer, not photometry):
  1. Remap longitude: the source follows the astronomical convention (galactic
     center at image center, l increasing LEFTWARD) — verified against the
     LMC/SMC and M31 positions. The texture stores l increasing rightward from 0.
  2. Crop to b ∈ [−45°, +45°] (band + bulge; the renderer treats outside as 0).
  3. Downsample with an intermediate median filter to suppress bright point
     sources (the renderer draws stars separately from the HYG catalog — leaving
     Sirius-class stars in the texture would double them as fuzzy blobs).
  4. Subtract the sky floor (airglow/scatter), normalize to a high percentile,
     mild gamma to keep the faint outer band, desaturate 50% toward luma so the
     band stays in the app's cool-silver/warm-cream duotone aesthetic.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

SOURCE_URL = "https://www.eso.org/public/archives/images/large/eso0932a.jpg"
CACHE = Path.home() / ".cache" / "pynightsky" / "eso0932a.jpg"

B_MAX = 45.0          # texture latitude coverage (degrees, symmetric)
OUT_W, OUT_H = 1024, 256
MID_W, MID_H = 2048, 512     # median-filter resolution (~0.18°/px)
FLOOR_PCT = 10.0      # luma percentile treated as the empty-sky floor
NORM_PCT = 99.7       # luma percentile mapped to full scale
GAMMA = 0.85          # <1 lifts the faint outer band slightly
DESAT = 0.5           # 0 = original color, 1 = grayscale


def fetch_source(path: Path) -> Path:
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {SOURCE_URL} -> {path}")
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "pynightsky-build/1.0"})
    with urllib.request.urlopen(req) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def build(src_path: Path, out_path: Path) -> None:
    im = Image.open(src_path).convert("RGB")
    w, h = im.size
    if abs(w / h - 2.0) > 0.01:
        sys.exit(f"expected a 2:1 equirectangular source, got {w}x{h}")
    arr = np.asarray(im)

    # 1. Longitude remap: source x_src = (w/2 − l·sx) mod w  →  texture x_tex ∝ l.
    sx = w / 360.0
    l_out = (np.arange(w) + 0.5) / sx                       # degrees, 0→360
    x_src = (np.round(w / 2 - l_out * sx).astype(int)) % w
    arr = arr[:, x_src]

    # 2. Crop to |b| ≤ B_MAX (b = +B_MAX at the top row).
    sy = h / 180.0
    y0 = int(round(h / 2 - B_MAX * sy))
    y1 = int(round(h / 2 + B_MAX * sy))
    arr = arr[y0:y1]

    # 3. Downsample via a median-filtered intermediate to kill point sources.
    mid = Image.fromarray(arr).resize((MID_W, MID_H), Image.LANCZOS)
    mid = mid.filter(ImageFilter.MedianFilter(3))
    out = mid.resize((OUT_W, OUT_H), Image.LANCZOS)
    v = np.asarray(out).astype(np.float64) / 255.0

    # 4. Floor subtraction + normalize + gamma + desaturate.
    luma = v @ [0.2126, 0.7152, 0.0722]
    floor = np.percentile(luma, FLOOR_PCT)
    v = np.clip((v - floor) / (1.0 - floor), 0.0, None)
    luma = v @ [0.2126, 0.7152, 0.0722]
    scale = np.percentile(luma, NORM_PCT)
    v = np.clip(v / scale, 0.0, 1.0)
    v = v ** GAMMA
    luma = (v @ [0.2126, 0.7152, 0.0722])[..., None]
    v = v * (1.0 - DESAT) + luma * DESAT

    img = Image.fromarray((np.clip(v, 0, 1) * 255).astype(np.uint8))
    # Palette PNG halves the size vs RGB (405→184 KB) with no visible loss on
    # this mostly-monochrome glow; the renderer reads it back via canvas anyway.
    img = img.quantize(colors=256, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, optimize=True)
    kb = out_path.stat().st_size / 1024
    print(f"wrote {out_path} ({OUT_W}x{OUT_H}, {kb:.0f} KB)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", type=Path, default=None,
                   help=f"path to eso0932a.jpg (default: download to {CACHE})")
    p.add_argument("-o", "--output", type=Path,
                   default=Path("apps/web/public/mw.v1.png"))
    args = p.parse_args()
    src = args.source if args.source else fetch_source(CACHE)
    build(src, args.output)


if __name__ == "__main__":
    main()
