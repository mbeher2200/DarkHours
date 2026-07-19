# PAD-US H3 Spatial Index

A pre-built H3 index of US public lands used as **Tier 1** of `find_nearby`'s naming
pipeline: a fast, offline, no-network first pass that either names a dark-sky candidate
(public land) or eliminates it (restricted land) before any Overpass/Nominatim call.

- **Source of truth:** USGS PAD-US 4.1 Geodatabase.
- **Runtime artifact:** `cache/darkhours_padus_h3.npz` — **committed** (~3.1 MB,
  1,374,391 cells; staged into the API and worker Lambda zips). The intermediate
  `cache/darkhours_padus_h3.parquet` (~3.5 MB) is also committed for
  re-conversion via `scripts/convert_padus_parquet_to_npz.py`.
- **Builder:** `scripts/build_padus_index.py` (offline; needs `requirements-build.txt`).
- **Runtime:** `darksky._load_padus_h3_index` / `_padus_h3_lookup` — a numpy-only
  `.npz` read (no pyarrow at runtime).

## Why it exists

Overpass is slow/unreliable and public Nominatim is rate-limited (1 req/s, no
parallelism). For a US dark-sky candidate, the PAD-US cell answers two questions with
zero network:
- **Blacklisted cell** → discard the candidate (military/tribal/private/no-access).
- **Non-blacklisted with a good `Unit_Nm`** → use that name; skip Overpass *and* the
  reverse geocoder entirely.
- **No cell hit** → fall through to Tier 2/3 (Overpass/geocoder).

## File format (current — columnar uint64 `.npz`)

| Column | Type | Notes |
|---|---|---|
| `h3_cell` | **uint64**, **sorted ascending** | H3 cell at resolution 7 (~5 km hex), as the native 64-bit id |
| `Unit_Nm` | string array | Park/unit name |
| `Mang_Name` | string array | Manager code (NPS, USFS, BLM, FWS, …) |
| `is_blacklisted` | bool | True = restricted → eliminate the candidate |

`scripts/convert_padus_parquet_to_npz.py::encode_padus_npz` is the single source of
truth for the on-disk layout (shared by the builder).

Counts: **1,374,391** unique cells — 1,297,603 viable, 76,788 blacklisted.

> **Format invariant:** the runtime loader reads `h3_cell` as a numpy `uint64` array and
> binary-searches it, so it **must be uint64 and sorted ascending**. The build and
> migrate scripts produce exactly this; the loader also has a cheap sort-guard as a
> safety net. (Older builds stored `h3_cell` as a *string* and the loader built a
> ~1.4M-entry dict — that dominated Lambda cold starts; see
> [PERF_FINDNEARBY.md](PERF_FINDNEARBY.md). Don't regress to strings.)

## Runtime path (`PyNightSkyPredictor/darksky.py`)

- `_load_padus_h3_index()` — lazy-loads once per process into a `_PadusIndex`
  (sorted `uint64` cell array + parallel name/blacklist arrays) from the `.npz` with
  numpy only. Cached in the `_padus_h3_cache` module global; returns `None` (cached)
  if h3/numpy is missing or the file can't be read → callers degrade to Tier 2/3.
- `_padus_h3_lookup(lat, lon, index)` — `np.searchsorted` on the cell array; returns
  `(Unit_Nm, is_blacklisted)` or `None`. Names are materialised from Arrow only on a hit.
- `_is_good_padus_name(unit_nm)` — rejects empty, `< 5` chars, and names containing
  `unknown`/`unnamed`/`office`, and pure-numeric ids. A non-blacklisted cell with a junk
  name still counts as "PAD-US-verified land" but falls to Tier 3 for naming.
- File resolution order (`_padus_h3_path`): `PYNIGHTSKY_PADUS_H3_PATH` env override →
  `<repo>/cache/darkhours_padus_h3.npz` → the Lambda-zip layout path.

## Blacklist rules (`_blacklisted` in the build script)

A feature is blacklisted (→ `is_blacklisted=True`) if **any** of:
- `Pub_Access == 'XA'` (no public access)
- `Mang_Name` in: DOD, DOE, BIA, BOP, ARS, NASA, USCG, NGO, NRCS, UNK, UNKL
- `Mang_Type` in: TRIB, PVT
- `Des_Tp` in: "Conservation Easement", MIL, TRIBL, CONE

`Pub_Access == 'UK'` (unknown) is deliberately **not** blacklisted (wide-net intent).
On a cell with conflicting sources, **blacklisted wins** (dedup keeps the blacklisted row).

## Rebuilding from source (full refresh, e.g. PAD-US 4.2)

1. Download the **PAD-US Combined Feature Class Geodatabase** from USGS ScienceBase
   (4.1: <https://www.sciencebase.gov/catalog/item/652d4fc5d34e44db0e2ee45e>, ~700 MB).
2. Unzip into `Temp/` so the path is `Temp/PADUS4_1Geodatabase/PADUS4_1Geodatabase.gdb`
   (the `Temp/` raw data is gitignored — do not commit it). Update `GDB_PATH`/`LAYER` in
   the build script if the version/layer name changes.
3. `pip install -r requirements-build.txt` (geopandas, pyarrow, h3).
4. `python scripts/build_padus_index.py` → writes `cache/darkhours_padus_h3.npz`
   (uint64, sorted). Pipeline: read `PADUS4_1Fee` → flag blacklist → reproject to
   EPSG:4326 → H3 polyfill at res 7 (`h3.geo_to_cells`, centroid fallback for sub-hex
   polygons) → dedup (blacklist wins) → encode `h3_cell` to uint64 + sort → write.
5. Verify + benchmark: `python scripts/bench_padus_load.py` (load time + sample lookups).
6. Commit the regenerated index files (tracked `.gitignore` exceptions). Pre-commit
   `check-added-large-files` allows up to 17 MB.

## Migrating an existing string-keyed parquet (no source GDB needed)

If you only have an old string-`h3_cell` parquet:

```
python scripts/migrate_padus_uint64.py <old_string.parquet> cache/darkhours_padus_h3.parquet
```

Converts cells via `h3.str_to_int`, sorts, and rewrites in the columnar format.

## Verifying correctness after a change

```
python scripts/bench_padus_load.py --verify-against <reference_string.parquet>
```

Cross-checks every lookup against a reference dict built from the original parquet
(used when the uint64 migration shipped: 0 mismatches over 50,006 checks).

## Related

- Cold-start performance impact and benchmarks: [PERF_FINDNEARBY.md](PERF_FINDNEARBY.md).
- The index is one tier of `_jit_geocode_candidates` / `_offline_tier_name`; Tier 2 is
  Overpass natural areas, Tier 3 is the reverse geocoder (Nominatim or AWS Location).
