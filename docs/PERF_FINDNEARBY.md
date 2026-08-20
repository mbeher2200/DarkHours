# find_nearby Performance

Current performance architecture of `darksky.find_nearby` (the `/nearby` async job).

## Current state

End-to-end `/nearby` is worker-bound: the API side (enqueue + job polls) runs warm in
single-digit milliseconds, and the SQS worker does all the work.

- **Keep-warm on both Lambdas.** EventBridge pings the API and the worker every
  4 minutes (`cdk/lambda_api_stack.py`); a Record-less event makes the worker run
  its prewarm synchronously.
- **Background prewarm** (`apps/worker/handler.py::_prewarm`, daemon thread on
  cold start): warms the raster grids, PAD-US + OSM POI indexes, the DynamoDB
  connection pool, and the ephemeris.
- **Columnar PAD-US index.** Sorted-uint64 `.npz` + `np.searchsorted`
  (`darksky._load_padus_h3_index`). See `docs/PADUS_INDEX.md`.
- **Rasterio-free raster reads.** Window reads are tiled-grid S3 byte-range GETs,
  tiles fetched concurrently (`gridraster.py`; see `docs/RASTERIO_REPLACEMENT.md`).
- **Right-sized raster windows (conditional dome fetch).** `find_nearby` fetches a
  `radius_miles + 2`-sized window instead of a fixed 150-mile one; a VIIRS-only
  150-mile fetch is issued only when the origin resolves Bortle ≤ 6, submitted
  right after the origin lookup so it overlaps the extraction/clustering CPU
  phases. Bright origins (B7–9) skip the outer portion of the fetch entirely. The
  known-dark repeat-origin peek path still pulls Falchi at the full 150 miles
  alongside VIIRS. Kill switch: `PYNIGHTSKY_SMALL_WINDOW=0`.
- **S3 client pool sized to the tile fan-out.** `max_pool_connections` default 32
  (`PYNIGHTSKY_S3_POOL`).
- **Vectorized dome detection.** Batched `bincount`/`center_of_mass(index=)` ops;
  the whole dome pipeline is skipped when the origin is Bortle ≥ 7.
- **Reverse-geocode discipline.** 8-mile pre-dedup of candidate probes
  (`_NAME_DEDUP_MILES`), POI/PAD-US-index-first naming, a 16-point-compass
  directional pre-dedup of dome candidates before naming
  (`_dedup_domes_by_direction`), and a lazy, backend-unified batch resolver for
  dark-candidate Tier-3 naming (`_lazy_batch_settlements`): both backends run
  the identical scan/gate/resolve loop, resolving only as far ahead as the next
  batch, never the whole candidate pool. Batch width is the only backend
  difference: 1 (serial) on local, per Nominatim's no-parallel policy;
  `_GEOCODE_MAX_WORKERS` (parallel) on aws.
- **Absolute-grid-anchored pixel labels.** Dome and dark-candidate pixel lat/lon
  labels are built from the raster's fixed absolute grid origin
  (`_window_pixel_grid`) instead of each window's own bounds, so the
  reverse-geocode cache key for a given real-world light source is stable across
  different search origins.
- **Shared `/suggest` cache.** A short-TTL shared cache tier behind `/suggest`'s
  per-container LRU.
- **Drive times via `CalculateRoutes`, per-leg, in parallel.** One point-to-point
  call per cache-missing leg (bounded thread pool). Each leg is cached 24 h
  (`_DRIVE_CACHE_TTL`). Ferry / unpaved-tail warnings come from the route legs.
- **Memory:** both Lambdas run at 3008 MB (the account cap).

Representative warm-container, cache-warm total: **~1–3 s** per search, dominated
by first-visit drive-time legs and the two raster window reads.

## Instrumentation (opt-in, kept)

- `PYNIGHTSKY_PROFILE=1` — per-phase wall time + cache hit/miss delta logged from
  `find_nearby` (`[profile]` lines; `darksky._Profiler`).
- `cache.stats` — hit/miss counter at the `cache.get` chokepoint (`cache.py`).
- `scripts/profile_aws.sh` + `scripts/aws_one_search.py` — one profiled search
  against the real aws backend (resource names from env; needs an authenticated
  session).
- The throwaway in-region test-worker recipe in `CLAUDE.md` for validating changes
  on real infra without touching the deployed worker.

## Provider latency reference

| Provider | Use | Observed latency |
|---|---|---|
| AWS Location (SearchPlaceIndexForPosition) | reverse geocode (aws) | ~87 ms/call in-region |
| Nominatim | reverse/forward geocode (local) | ~1.1 s/call (self-throttled; policy: 1 req/s, no parallel) |
| Overpass (`overpass-api.de`) | natural-area names (local) | ~7.6 s/query |
| Open-Meteo / 7Timer | weather | sub-second |
| Celestrak | TLEs | sub-second |

Live connectivity for every provider is covered by `tests/test_provider_smoke.py`
(`PYNIGHTSKY_LIVE=1 pytest -m live`).

## Open items

- Re-profile drive times in-region since the routing API switch (N client-parallel
  calls, ≤11/search). Repeat-area searches are cache-served either way.
- GeoRoutes cost parity between matrix pairs and per-leg requests is likely but
  unconfirmed numerically.
- AWS Location's account TPS quota should be raised before scaling parallel
  geocode wider; adaptive retries cushion bursts today.

## Reproduce

- `scripts/bench_padus_load.py` — PAD-US load + lookup benchmark (`--verify-against`).
- `scripts/profile_find_nearby.py` — per-phase profile across cities (warm + `--cold`).
- `scripts/diag_geocode_waste.py` — classify reverse-geocode probes (kept/duplicate/water).
- `scripts/profile_parallel_geocode.py` — offline serial-vs-parallel A/B (stubbed latency).
- `scripts/bench_dome_detection.py` — dome-detection benchmark on real windows.
- `scripts/profile_aws.sh` + `scripts/aws_one_search.py` — real-backend profiled search.
