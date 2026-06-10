#!/usr/bin/env python3
"""One-off: convert the PAD-US H3 parquet from string cells to sorted uint64 cells.

The runtime loader (_load_padus_h3_index) reads h3_cell as a numpy uint64 array and
binary-searches it, avoiding the ~1.4M-object Python dict build. This rebuilds the
committed parquet in that format from an existing string-keyed parquet.

    python scripts/migrate_padus_uint64.py <in_string.parquet> <out.parquet>
"""
import sys

import h3
import pyarrow as pa
import pyarrow.parquet as pq


def main():
    src, dst = sys.argv[1], sys.argv[2]
    tbl = pq.read_table(src)
    print(f"read {tbl.num_rows:,} rows from {src}; columns={tbl.column_names}")

    cells_str = tbl.column("h3_cell").to_pylist()
    cells_u64 = [h3.str_to_int(c) for c in cells_str]      # exact, lossless

    cols = {"h3_cell": pa.array(cells_u64, type=pa.uint64())}
    for name in ("Unit_Nm", "Mang_Name", "is_blacklisted"):
        if name in tbl.column_names:
            cols[name] = tbl.column(name)
    out = pa.table(cols)

    # Sort ascending by cell so the loader can np.searchsorted without re-sorting.
    out = out.sort_by([("h3_cell", "ascending")])
    pq.write_table(out, dst, compression="zstd")
    print(f"wrote {out.num_rows:,} rows to {dst}; h3_cell dtype={out.schema.field('h3_cell').type}")


if __name__ == "__main__":
    main()
