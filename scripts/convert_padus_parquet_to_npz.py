#!/usr/bin/env python3
"""Encode the PAD-US H3 index as a compressed .npz (numpy) instead of parquet, so
the runtime reader (darksky._load_padus_h3_index) needs only numpy — not pyarrow
(~139 MB, the single biggest dep once GDAL is gone). Build-time only.

On-disk format (np.savez_compressed):
  cells       uint64  — H3 cell ids, ascending (binary-searched at runtime)
  name_codes  uint32  — dictionary code per cell, index into the names list
  blacklist   bool    — per cell
  names_blob  uint8   — utf-8 of "\\x00".join(unique_names); reader splits on \\x00

Names are dictionary-encoded because a protected area spans many cells (1.37M cells,
~87k unique names). `encode_padus_npz` is the single source of truth for the layout,
imported by scripts/build_padus_index.py so the format never drifts.

Usage (one-off conversion from the existing committed parquet):
    python scripts/convert_padus_parquet_to_npz.py \
        cache/darkhours_padus_h3.parquet cache/darkhours_padus_h3.npz
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def encode_padus_npz(out_path, cells, names, blacklist) -> dict:
    """Write the .npz. `cells` uint64-able, `names` a sequence of str (one per cell,
    aligned), `blacklist` bool-able. Returns a small stats dict."""
    cells = np.asarray(cells, dtype=np.uint64)
    blacklist = np.asarray(blacklist, dtype=bool)
    names = ["" if n is None else str(n) for n in names]

    # Dictionary-encode: sorted unique names + a uint32 code per cell. Sorted for
    # deterministic output; the reader rebuilds the same list by splitting the blob.
    uniq = sorted(set(names))
    code_of = {n: i for i, n in enumerate(uniq)}
    codes = np.fromiter((code_of[n] for n in names), dtype=np.uint32, count=len(names))
    names_blob = np.frombuffer("\x00".join(uniq).encode("utf-8"), dtype=np.uint8)

    np.savez_compressed(out_path, cells=cells, name_codes=codes,
                        blacklist=blacklist, names_blob=names_blob)
    return {"cells": int(cells.size), "unique_names": len(uniq)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parquet", type=Path, help="input PAD-US H3 parquet")
    ap.add_argument("npz", type=Path, help="output .npz")
    args = ap.parse_args(argv)

    import pyarrow.parquet as pq
    tbl = pq.read_table(str(args.parquet), columns=["h3_cell", "Unit_Nm", "is_blacklisted"])
    cells = tbl.column("h3_cell").to_numpy(zero_copy_only=False)
    names = tbl.column("Unit_Nm").to_pylist()
    blacklist = tbl.column("is_blacklisted").to_numpy(zero_copy_only=False)

    # Ensure ascending (the reader binary-searches); sort all three together if not.
    if cells.size and not bool(np.all(cells[:-1] <= cells[1:])):
        order = np.argsort(cells, kind="stable")
        cells = cells[order]
        names = [names[i] for i in order]
        blacklist = blacklist[order]

    stats = encode_padus_npz(args.npz, cells, names, blacklist)
    size_mb = args.npz.stat().st_size / (1024 * 1024)
    print(f"wrote {args.npz} ({size_mb:.1f} MB) — {stats['cells']:,} cells, "
          f"{stats['unique_names']:,} unique names")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
