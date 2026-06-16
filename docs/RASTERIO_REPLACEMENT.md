# Replacing rasterio/GDAL — starting context

Seed for a focused initiative to replace (or slim) the rasterio/GDAL dependency. Read
this + `docs/PYNIGHTSKY.md` first. (Earlier drafts pointed at `CLAUDE.md` /
`docs/PADUS_INDEX.md`, which do not exist — see `docs/PADUS_INDEX.md`'s actual home and
the project reference `docs/PYNIGHTSKY.md`.)

> **STATUS 2026-06-10 — approach chosen + verified facts (read before the rest):**
> Both datasets are **already EPSG:4326** — the local tifs *and* both S3 COGs. The claim
> below that Falchi is non-4326 and triggers a runtime `reproject` is **false for the
> current data**; the `needs_reproject` branch in `_load_raster_window` is dead code. No
> runtime warp is needed. Chosen replacement: **fixed-size tiled raw-binary grids** (built
> offline from the GeoTIFFs) read with pure `numpy`+`boto3` via S3 byte-range reads — no
> GDAL. See `scripts/build_raster_grid.py`, `PyNightSkyPredictor/gridraster.py`,
> `scripts/validate_grid_vs_rasterio.py`, and the `rasterio_replacement` memory note.
> Verified dims: VIIRS 86400×33600 f32 (~11.6 GB raw); Falchi 43200×17406 f32 (~3.0 GB raw).

## Why

`rasterio==1.5.0` pulls the **~335 MB GDAL stack** — by far the heaviest dependency. It
drives the worker/API image size, the **one-time ~17 s first-container image lazy-load
tax** (the last unaddressed cold-start cost; see `docs/PERF_FINDNEARBY.md`), and a chunk
of cold import time. Replacing it with a lightweight COG reader is the lever for image
size + cold start. (It's also why the warmer is a separate rasterio-free zip Lambda.)

## What rasterio is used for (the full surface to replace)

All in `PyNightSkyPredictor/darksky.py` unless noted. Two operations + the path adapter:

1. **`_sample_tif(path, lat, lon)`** (~line 273) — **single-pixel** read. Opens the raster;
   if `ds.crs.to_epsg() != 4326`, `warp_transform` the one (lon,lat) to native CRS; then
   `ds.sample([(x,y)])`. nodata→0.0, negatives→0, `None` on any error. Used by `lookup()`
   (the `/night` light-pollution value).
2. **`_load_raster_window(source_key, min_lat, max_lat, min_lon, max_lon, out_shape=None)`**
   (~line 611) — **bbox window → float64 ndarray**, row 0 = north (max_lat), col 0 = west.
   Two paths:
   - **EPSG:4326 (VIIRS):** `windows.from_bounds` + `ds.read(1, window=..., boundless=True,
     fill_value=0.0)`, with optional `out_shape` + `Resampling.bilinear` (used to align
     Falchi to the VIIRS pixel grid).
   - **non-4326 (Falchi):** transform the 4 bbox corners to native CRS, read that native
     window, then `reproject(... dst_crs="EPSG:4326", Resampling.bilinear)` onto a
     `transform.from_bounds` dst grid. nodata→0.0, negatives→0.
   Used by `find_nearby` (the two window reads; Falchi is read at `out_shape=viirs.shape`).
3. **`LocalRasterSource` / `S3RasterSource.path_for`** (`ports.py` seam, ~line 201/227) —
   returns something `rasterio.open` accepts: a local `Path`, or `"/vsis3/{bucket}/{key}"`
   for S3. `S3RasterSource.__init__` sets GDAL `/vsis3` tuning: `VSI_CACHE=TRUE`,
   `VSI_CACHE_SIZE=52428800`, `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR`,
   `CPL_VSIL_CURL_ALLOWED_EXTENSIONS=.tif`, `GDAL_HTTP_MERGE_CONSECUTIVE_RANGES=YES`.
   **A replacement needs its own S3 byte-range read path** (boto3 `get_object` Range, or
   obstore/async-tiff) — this is where the "no download, range-read only the tiles needed"
   property lives.
4. **`apps/worker/handler.py` `_prewarm`** — opens both COGs + samples one pixel to warm
   the reader. Update to the new reader's warm-up.
5. **`_download` / `_download_viirs` / `_download_falchi`** (~line 144) — local backend
   only: downloads + unzips the raw GeoTIFFs. Not S3-path; lower priority.

## The two datasets

| | VIIRS 2025 (Black Marble) | Falchi 2016 (World Atlas) |
|---|---|---|
| Units | radiance nW/cm²/sr | artificial luminance mcd/m² |
| CRS | **EPSG:4326** | **EPSG:4326** (current data — see STATUS; the reproject path below is dead code) |
| S3 COG key | `viirs_2025_cog.tif` | `world_atlas_2016_cog.tif` |
| Raw source | lightpollutionmap.info zip (~986 MB) | GFZ Potsdam zip (~2.8 GB) |
| Backends | S3 COG (`/vsis3`, range reads) on aws; local raw `.tif` under `~/.pynightsky-predictor` on local | same |

The COGs on S3 are read in place via GDAL `/vsis3` range reads (nothing downloaded). The
COG-build process is **not in the repo** — confirm how the COGs were produced (likely
`rio cogeo` / `gdal_translate`) before changing the build.

## Invariants any replacement MUST preserve (correctness gate)

- `_sample_tif`: identical value at (lat,lon); nodata→0.0; negatives→0; `None` on error.
- `_load_raster_window`: **exact array orientation** (row 0 = max_lat, col 0 = min_lon),
  `boundless` fill 0.0, the `out_shape` bilinear resample (Falchi → VIIRS shape), and the
  Falchi native→4326 bilinear reproject. Downstream numpy (`_extract_dark_sky_candidates`,
  SQM/Bortle, haversine mask, dome detection) depends on exact orientation + values.
- Works on **both backends** through the ports seam (keep `path_for`, or introduce a new
  `RasterSource.read_window/sample` abstraction).
- Graceful degradation: return `None`/skip when the reader is unavailable (mirror the
  current rasterio-missing behavior).

## Strong simplifying idea to evaluate first

The **only** thing forcing GDAL's warp machinery at runtime is the Falchi **reproject**
(VIIRS is already 4326). If Falchi is **re-projected to EPSG:4326 once at COG-build time**,
*both* datasets become the simple 4326 window-read path — then a lightweight pure-Python/
Rust COG tile reader (no GDAL) is sufficient, and the runtime reproject code path is
deleted. That likely turns "replace rasterio" into "build a 4326 Falchi COG + a small COG
window/sample reader." Worth validating before picking a library.

## Candidate directions to investigate (user has ideas — these are just leads)

- Pure-Python COG: `tifffile` + `imagecodecs` (tile decode) over boto3-range / `fsspec`.
- Rust-backed: `cog3pio` (returns numpy), `async-tiff` + `obstore`.
- Coordinate transforms without GDAL: `pyproj` (lightweight) covers `warp_transform`;
  resampling/reproject is the hard part GDAL does — hence the build-time-reproject idea.
- Keep GDAL but slim the image (multi-stage, drop unused drivers) — smaller win.

## How to validate

1. **Correctness:** compare the new reader vs rasterio for `_sample_tif` over many
   (lat,lon) and `_load_raster_window` over several bboxes — **both datasets**, incl. the
   Falchi reproject + `out_shape` alignment. Define a tolerance for bilinear differences,
   or match exactly if the same algorithm. Then confirm `find_nearby` results are unchanged
   (reuse the funnel logging + `scripts/bench_*`).
2. **The win:** measure image size + cold-start delta (import time, the ~17 s first-container
   tax) using the throwaway test-worker recipe in `CLAUDE.md` + `PYNIGHTSKY_PROFILE`.
3. Ship one variable at a time; record in `docs/PERF_FINDNEARBY.md`.

## Test touchpoints

`tests/test_darksky_formulas.py`, `tests/test_light_dome_array.py` (pure-array, no I/O),
`tests/test_aws_smoke.py` (`darksky.lookup` via real S3 — the `aws`-marked integration).
