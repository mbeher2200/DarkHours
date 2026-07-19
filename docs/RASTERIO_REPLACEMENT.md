# Raster Grid Pipeline (rasterio/GDAL replaced — shipped)

The light-pollution rasters (VIIRS 2025 radiance, Falchi 2016 World Atlas) are served
at runtime by a **pure-numpy tiled raw-binary grid reader** — no rasterio, no GDAL, no
tifffile. rasterio survives only as a **build-time** dependency
(`requirements-build.txt`) that reads the source GeoTIFFs when the grids are
(re)generated. This document describes the shipped pipeline; the initiative history
that led here is in the appendix.

## Current architecture

```
build time (offline, rasterio):
  source GeoTIFF ──scripts/build_raster_grid.py──▶ <prefix>.bin + <prefix>.json
                    (thin CLI over PyNightSkyPredictor/gridbuild.py)

runtime (numpy only, via the ports.py RasterSource seam):
  local backend:  LocalRasterSource → gridraster.open_local()  → memmap reads
                  (auto-builds the grid from the downloaded GeoTIFF on first use)
  aws backend:    S3RasterSource    → gridraster.open_s3()     → S3 byte-range GETs
                  (nothing is ever downloaded; the .bin is range-read in place)
```

- **Runtime reader:** `PyNightSkyPredictor/gridraster.py` (`GridArray` with
  `sample(lat, lon)` and `read_window(bbox, out_shape=None)`).
- **Builder:** `PyNightSkyPredictor/gridbuild.py` (`build()`, tile size default 512),
  wrapped by `scripts/build_raster_grid.py`. This is the only rasterio import in the
  package, and it raises a clear "install requirements-build.txt" error if rasterio
  is absent.
- **Seam:** `ports.py` selects `LocalRasterSource` / `S3RasterSource`
  (`PYNIGHTSKY_BACKEND`); the S3 bucket comes from `PYNIGHTSKY_RASTER_BUCKET`
  (never committed — public repo).
- **Consumers:** `darksky.lookup()` (single-pixel sample for the `/night`
  light-pollution value) and `darksky.find_nearby()` (two bbox window reads);
  `apps/worker/handler.py::_prewarm` warms both grids on container start.

## Grid format

A grid is a `<prefix>.json` (affine + tiling metadata) plus a `<prefix>.bin` of
fixed `tile_size × tile_size` tiles in row-major tile order, edge tiles
zero-padded. Every tile is exactly `tile_size² × itemsize` bytes, so any pixel's
byte offset is pure arithmetic — no offset index exists or is needed:

```
tile_id  = ty * tiles_x + tx
elem_off = tile_id * tile_size² + row_in_tile * tile_size + col_in_tile
```

- `sample()` is a single `itemsize`-byte ranged GET on S3 (memmap slice locally).
- `read_window()` fetches every tile intersecting the bbox as an independent
  ranged GET, dispatched concurrently (`PYNIGHTSKY_GRID_WORKERS` threads,
  default 8), then crops/assembles.

Shipped grid parameters (hardcoded in `darksky.S3RasterSource._GRID_META` to skip
the JSON GET on cold start — regenerate if the source GeoTIFFs change):

| | VIIRS 2025 (Black Marble) | Falchi 2016 (World Atlas) |
|---|---|---|
| Units | radiance nW/cm²/sr | artificial luminance mcd/m² |
| Dimensions | 86400 × 33600 float32 | 43200 × 17406 float32 |
| Tile size | 512 (169 × 66 tiles) | 512 (85 × 34 tiles) |
| `.bin` size | ~11.7 GB | ~3.0 GB |
| CRS | EPSG:4326, north-up | EPSG:4326, north-up |
| S3 key prefix | `viirs_2025` | `world_atlas_2016` |
| Raw source | lightpollutionmap.info zip (~986 MB) | GFZ Potsdam zip (~2.8 GB) |

Both datasets are EPSG:4326, so there is **no runtime reprojection anywhere** —
the one operation that would have required GDAL's warp machinery.

## Invariants (the correctness gate the replacement had to pass)

Preserved exactly from the old rasterio paths, because downstream numpy
(`_extract_dark_sky_candidates`, SQM/Bortle conversion, haversine masking, dome
detection) depends on orientation and values:

- `sample()` ≡ old `_sample_tif`: nodata → 0.0, negatives → 0, out-of-raster → 0.0,
  `None` only on read error.
- `read_window()` ≡ old `_load_raster_window`: row 0 = max_lat (north),
  col 0 = min_lon (west); boundless fill 0.0; window origin/extent rounded to
  nearest so arrays are pixel-identical to the rasterio path; optional bilinear
  `out_shape` resample (half-pixel aligned, used to put Falchi on the VIIRS pixel
  grid) matching GDAL bilinear to within tolerance.
- Works through the ports seam on both backends; degrades gracefully (returns
  `None` / skips) when the grid is unavailable.

Verified by `scripts/validate_grid_vs_rasterio.py` (A/B against rasterio over many
points and bboxes on both datasets) and the ongoing suites
`tests/test_gridraster.py`, `tests/test_darksky_formulas.py`,
`tests/test_light_dome_array.py`, and the `aws`-marked `tests/test_aws_smoke.py`.

## Regenerating the grids

```bash
pip install -r requirements-build.txt   # rasterio lives here, and only here
python scripts/build_raster_grid.py viirs_2025_cog.tif        out/viirs_2025        --dataset viirs
python scripts/build_raster_grid.py world_atlas_2016_cog.tif  out/world_atlas_2016  --dataset falchi
```

Upload the resulting `.bin` + `.json` pairs to the raster bucket
(`PYNIGHTSKY_RASTER_BUCKET`) under the key prefixes above, then update
`_GRID_META` in `darksky.py` with the values printed by the builder. The local
backend needs no upload — `LocalRasterSource` builds the grid automatically from
the downloaded GeoTIFF on first use.

## Outcome

- The Lambda runtime lost the ~335 MB GDAL stack entirely; both the API and the
  worker deploy as **zip Lambdas** with numpy/boto3-only raster access (a container
  image would not even be needed for size). The former "first-container ~17 s
  image lazy-load tax" (see `docs/PERF_FINDNEARBY.md`) went with it.
- Cold-start raster access is now a handful of S3 ranged GETs; window reads
  parallelize across tiles.

---

## Appendix — initiative history (2026-06-10 → shipped)

Kept for context; superseded by the sections above.

- **Why it started:** `rasterio==1.5.0` pulled the ~335 MB GDAL stack — the
  heaviest dependency, driving the then-container image size, a one-time ~17 s
  first-container lazy-load tax, and a chunk of cold import time. (It is also why
  the TLE warmer was built as a separate rasterio-free zip Lambda.)
- **Key discovery (2026-06-10):** both datasets — local GeoTIFFs *and* the S3
  COGs — were already EPSG:4326. The belief that Falchi was non-4326 and needed a
  runtime `reproject` was false for the current data; the `needs_reproject` branch
  in the old `_load_raster_window` was dead code. That collapsed the problem from
  "replace GDAL's warp machinery" to "read 4326 windows without GDAL."
- **Candidates considered:** pure-Python COG readers (`tifffile` + `imagecodecs`
  over ranged reads), Rust-backed readers (`cog3pio`, `async-tiff` + `obstore`),
  `pyproj` for coordinate transforms, or keeping GDAL but slimming the image.
  All were rejected in favor of the simpler custom tiled raw-binary format, which
  needs no TIFF decoding at all and makes every pixel's offset arithmetic.
- **Old rasterio surface that was replaced:** `_sample_tif` (single-pixel read with
  CRS transform fallback), `_load_raster_window` (bbox window with boundless fill +
  bilinear out_shape + the dead reproject branch), the `/vsis3` path adapter with
  its GDAL environment tuning (`VSI_CACHE`, `GDAL_DISABLE_READDIR_ON_OPEN`, …), and
  the worker prewarm that opened both COGs.
- **Validation approach:** A/B the new reader against rasterio for samples and
  windows on both datasets (`scripts/validate_grid_vs_rasterio.py`), confirm
  `find_nearby` output unchanged via the funnel logging + `scripts/bench_*`, then
  measure the size/cold-start win with the throwaway test-worker recipe in
  `CLAUDE.md` and `PYNIGHTSKY_PROFILE=1`.
