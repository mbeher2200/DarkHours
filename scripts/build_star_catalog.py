#!/usr/bin/env python3
"""Build the compact binary star catalog served to the web sky-dome renderer.

Input:  the HYG v4.x star database CSV (https://github.com/astronexus/HYG-Database,
        file hygdata / hyg_v42.csv — NOT committed to this repo; ~34 MB).
Output: apps/web/public/stars.v1.bin — quantized little-endian binary, ~96 KB
        for 12,000 stars. Regenerate with:

    python scripts/build_star_catalog.py ~/Downloads/hyg_v42.csv \
        -o apps/web/public/stars.v1.bin -n 12000 --names 40

The output filename is versioned (stars.v1.bin): CloudFront serves apps/web/public/
assets with a long-TTL cache policy, so any format change must bump the version
suffix rather than rewrite the same path.

Binary format "NSK1" (all little-endian):

    offset  size  field
    0       4     magic b"NSK1"
    4       2     uint16 version (=1)
    6       2     uint16 recordSize (=8)
    8       4     uint32 count
    12      4     uint32 namesOffset (bytes from file start)
    16      8*N   records, sorted by magnitude ascending (brightest first):
                    uint16 ra   — RA  deg × 65535/360   (~20 arcsec resolution)
                    int16  dec  — Dec deg × 32767/90
                    int16  mag  — visual magnitude × 100
                    uint8  bv   — (clamp(B-V, -0.4, 2.0) + 0.4) / 2.4 × 255
                    uint8  reserved (=0)
    namesOffset   UTF-8 JSON [[recordIndex, "Sirius"], ...] — proper names of
                  the brightest stars only, for on-dome labels.

Mag-sorted records let the client binary-search the cutoff index for any
limiting magnitude. Sol (HYG id 0, mag -26.7) is excluded.
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
from pathlib import Path

MAGIC = b"NSK1"
VERSION = 1
RECORD_SIZE = 8


def read_stars(csv_path: Path) -> list[dict]:
    stars = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("id") == "0":
                continue  # Sol — not a night-sky object
            try:
                mag = float(row["mag"])
                ra_deg = float(row["ra"]) * 15.0  # HYG stores decimal hours
                dec_deg = float(row["dec"])
            except (ValueError, KeyError):
                continue
            try:
                bv = float(row["ci"])
            except (ValueError, KeyError):
                bv = 0.65  # solar-ish default when the color index is missing
            stars.append({
                "ra": ra_deg % 360.0,
                "dec": dec_deg,
                "mag": mag,
                "bv": bv,
                "name": (row.get("proper") or "").strip(),
            })
    return stars


def quantize(star: dict) -> bytes:
    ra_q = min(65535, max(0, round(star["ra"] * 65535.0 / 360.0)))
    dec_q = min(32767, max(-32767, round(star["dec"] * 32767.0 / 90.0)))
    mag_q = min(32767, max(-32768, round(star["mag"] * 100)))
    bv = min(2.0, max(-0.4, star["bv"]))
    bv_q = min(255, max(0, round((bv + 0.4) / 2.4 * 255.0)))
    return struct.pack("<HhhBB", ra_q, dec_q, mag_q, bv_q, 0)


def self_check(payload: bytes, stars: list[dict]) -> None:
    """Round-trip a few records and assert quantization error bounds."""
    (magic, version, rec_size, count, names_off) = struct.unpack_from("<4sHHII", payload, 0)
    assert magic == MAGIC and version == VERSION and rec_size == RECORD_SIZE
    assert count == len(stars)
    for i in (0, count // 2, count - 1):
        ra_q, dec_q, mag_q, bv_q, _ = struct.unpack_from("<HhhBB", payload, 16 + i * RECORD_SIZE)
        assert abs(ra_q * 360.0 / 65535.0 - stars[i]["ra"]) < 0.006, f"ra mismatch at {i}"
        assert abs(dec_q * 90.0 / 32767.0 - stars[i]["dec"]) < 0.003, f"dec mismatch at {i}"
        assert abs(mag_q / 100.0 - stars[i]["mag"]) < 0.006, f"mag mismatch at {i}"
    names = json.loads(payload[names_off:].decode("utf-8"))
    assert all(isinstance(i, int) and isinstance(n, str) for i, n in names)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", type=Path, help="path to HYG v4.x CSV (e.g. hyg_v42.csv)")
    ap.add_argument("-o", "--out", type=Path, required=True, help="output .bin path")
    ap.add_argument("-n", "--count", type=int, default=12000, help="brightest N stars to keep")
    ap.add_argument("--names", type=int, default=40, help="proper names for the brightest N named stars")
    args = ap.parse_args()

    stars = read_stars(args.csv)
    stars.sort(key=lambda s: s["mag"])
    stars = stars[: args.count]
    if len(stars) < args.count:
        print(f"warning: only {len(stars)} usable stars in {args.csv}", file=sys.stderr)

    names: list[tuple[int, str]] = []
    for i, s in enumerate(stars):
        if s["name"]:
            names.append((i, s["name"]))
        if len(names) >= args.names:
            break

    names_json = json.dumps(names, separators=(",", ":")).encode("utf-8")
    names_offset = 16 + len(stars) * RECORD_SIZE
    header = struct.pack("<4sHHII", MAGIC, VERSION, RECORD_SIZE, len(stars), names_offset)
    payload = header + b"".join(quantize(s) for s in stars) + names_json

    self_check(payload, stars)
    args.out.write_bytes(payload)
    print(
        f"wrote {args.out} — {len(stars)} stars, {len(names)} names, "
        f"{len(payload):,} bytes, faintest mag {stars[-1]['mag']:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
