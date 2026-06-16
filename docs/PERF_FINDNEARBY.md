# find_nearby Performance — Profiling Results & Recommendations

Record of the 2026-06-09 performance investigation into `darksky.find_nearby`.
Reproduce with the tooling in `scripts/` (see bottom).

## TL;DR

`find_nearby` was network-bound on cold searches: ~85–95% of wall time was reverse
geocoding + an Overpass call that always timed out. Two bugs were fixed and two
optimizations shipped; a real worker run then showed the bottleneck has **moved to
cold-start CPU cost** (PAD-US index load), not the network.

## Instrumentation (kept, opt-in)

- `cache.stats` — hit/miss counter at the `cache.get` chokepoint (`cache.py`).
- Per-phase profiler in `find_nearby`, enabled with `PYNIGHTSKY_PROFILE=1`; logs each
  phase's wall time + cache hit/miss delta (`darksky.py`).

## Baseline (local backend, radius 60 mi, city-centre origins)

| Origin | Wall | cache hit-rate | Note |
|---|---:|---:|---|
| Los Angeles | 28.9 s | 0% | cold |
| New York | 26.4 s | 9% | |
| Chicago | 27.5 s | 8% | |
| Denver | 1.3 s | 50% | Overpass result cached |
| Phoenix | 62.0 s | 2% | 42 geocode misses |
| Atlanta | 36.0 s | 5% | |
| LA (repeat, warm) | 1.3 s | 92% | everything cached |

Representative phase breakdown (LA cold, 28.9 s): `overpass join` **15007 ms** (timeout
every call), `jit geocode candidates` **9421 ms** (9 Nominatim misses × ~1.1 s),
`padus index load` 2306 ms (one-time), `light dome detection` 937 ms, raster reads ~280 ms.

### Root causes
1. **Dead Overpass endpoint.** `overpass.private.coffee` (in the working tree) was
   unreachable → a guaranteed 15 s join timeout, and zero natural-area names. Only
   `overpass-api.de` of the mirrors tested responded (~7.6 s).
2. **Duplicate-dominated reverse geocoding.** Nominatim is 1.1 s/miss and serial; on
   Phoenix **36 of 43 probes were duplicate town names** (adjacent dark pixels → same
   settlement, median 5.3 mi apart).

## Fixes shipped (Tier 0)

| Change | Effect |
|---|---|
| Revert Overpass URL to `overpass-api.de` | 15 s timeout → ~7.6 s, real names (local/CLI backend only; aws disables Overpass) |
| 8-mi geocode pre-dedup (`_NAME_DEDUP_MILES`) | Phoenix cold probes 43 → 25 (−42%) |
| **A:** parallel reverse-geocode on aws backend | ~4× (see below); forbidden on Nominatim by policy, so aws-only |
| **B:** pooled, reused boto3 `location` client | no per-call client rebuild / dropped connection pool |

## Real worker validation (aws backend, in-region Lambda)

Built the worker image from the working tree, deployed an isolated
`pynightsky-worker-proftest` Lambda (x86_64, 2 GB, reused worker role,
`PYNIGHTSKY_PROFILE=1`), invoked via synthetic SQS event, read CloudWatch, tore down.

| Run | `jit geocode candidates` | cold misses | effective/call |
|---|---:|---:|---:|
| Serial (Phoenix, workers=1) | 1746 ms | 20 | ~87 ms |
| Parallel (Dallas, workers=8) | 339 ms | 16 | ~21 ms |

- **AWS Location ≈ 87 ms/call in-region** (vs 1.1 s on public Nominatim).
- **Parallel geocode ≈ 4×**, matching the offline 4.8× (`scripts/profile_parallel_geocode.py`).
- **New dominant cold cost: `padus index load` = 5–24 s per cold container** (parquet→dict
  build, CPU-bound; ~2.3 s on a laptop, much slower on throttled Lambda vCPU). Once per
  container.
- The 10 s init pre-warm (`_prewarm_rasters`) **timed out**, so the first job still pays
  cold S3 COG reads.
- Net: with parallel geocode in, `find_nearby` on the worker is **cold-start/CPU-bound**.

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

## Recommendations (prioritized)

**Tier 0 — done (this change):** Overpass fix, 8-mi dedup, A (parallel geocode), B (pooled client).

**Tier 1 — high impact, low effort**
1. Bump worker Lambda memory (2 GB → 3–4 GB): vCPU scales with memory; cuts the CPU-bound
   PAD-US load, dome detection, numpy, and init. One-line CDK change.
2. Pre-build the PAD-US index into a load-optimized format (pickled dict / int-keyed H3
   cells) so cold load is a deserialize, not a CPU loop. Biggest cold-start win.

**Tier 2 — high impact, medium effort**
3. Load only the regional PAD-US subset covering the search bbox (partitioned parquet or
   DynamoDB-by-cell) instead of the whole US index per container.
4. Resolve the init pre-warm timeout: drop the prewarm and rely on VSI cache after job 1,
   or use provisioned concurrency if job latency is user-visible.

**Tier 3 — medium**
5. Speed up scipy dome detection (2–4.5 s): downsample the window, or cache per coarse origin.
6. Pre-warm the geocode cache for popular origins (reuse the EventBridge warmer pattern).
7. Request an AWS Location TPS quota increase before scaling parallel geocode widely
   (~50 req/s account default; adaptive retries already cushion bursts).

## Benchmark log

One variable at a time; numbers kept for later reference.

### Tier 1 · Item 2 — PAD-US index: dict build → columnar uint64 + binary search (2026-06-09)

The cold load built a ~1.37M-entry Python dict from string-keyed parquet
(`to_pylist` ×3 → `dict(zip(...))`). Replaced with a sorted `uint64` cell array +
parallel name/blacklist arrays, looked up via `np.searchsorted`; names stay in Arrow
and are materialised only on a hit. Parquet regenerated to uint64-sorted (build +
`scripts/migrate_padus_uint64.py`).

Local benchmark (`scripts/bench_padus_load.py`, 5 cold iters, M-series laptop):

| Metric | Before (dict) | After (columnar) |
|---|---:|---:|
| Index load, median | **1787.7 ms** | **53.7 ms** (~33× faster) |
| Index load, min | 1645.0 ms | 52.5 ms |
| Lookup | 0.85 µs/pt | 2.54 µs/pt (≈0.3 ms total per search — negligible) |
| Parquet size | 10.6 MB | 3.5 MB |
| Correctness | — | 0 mismatches over 50,006 checks ✅ |

**In-region confirmation** (throwaway worker, x86_64/2 GB — same config as the baseline,
so only the index-load variable changed). Cold `padus index load`, 4 samples:

| Cold sample | padus index load |
|---|---:|
| 1 — first-ever container | 17178 ms (one-time Lambda image lazy-load tax) |
| 2 | 463.8 ms |
| 3 | 454.9 ms |
| 4 | 424.4 ms |

Steady cold-container load **≈ 450 ms, down from the baseline 15–24 s (~35×)** — matches
the laptop ratio. The first container after a fresh image deploy still pays a one-time
~17 s image-load tax (Lambda fetches/decompresses layers on first touch; the old build
paid this *plus* its dict build). Residual fix for that tax if ever needed: provisioned
concurrency. Tests: 390 passed. This likely makes Tier 2 item 3 (regional subset) unnecessary.

Not yet benchmarked: Item 1 (memory bump) — deferred (single-threaded load already has
≥1 vCPU at 2 GB, so it won't help this phase; revisit for Init + dome detection).

### Tier 3 · scipy dome detection — vectorise the per-blob centroid loop (2026-06-09)

Profiling `_find_light_domes_from_array` on real 150-mile VIIRS windows
(`scripts/bench_dome_detection.py`) showed **97–98% of the time was the centroid loop**,
not the land mask as assumed: it did `labeled == i` + `.sum()` + `center_of_mass(...)`
per blob — O(blobs × pixels), ~500–1300 blobs over ~1.3M pixels. Replaced with batched
ops: `np.bincount` for sizes + scipy `center_of_mass`/`maximum` with `index=` (each a
few full-array passes). **Output-identical** to the per-blob loop.

| Origin (≈1.3M-px window) | Before | After | Speedup |
|---|---:|---:|---:|
| Los Angeles (315 domes) | 928 ms | **79 ms** | ~12× |
| New York (829 domes) | 2547 ms | **84 ms** | ~30× |

Correctness: new vs original loop = identical dome lists on both windows; 390 tests pass
(incl. `test_light_dome_array.py`). Structural win (removes a blobs×pixels factor), so the
ratio holds on Lambda's slower vCPU too — laptop absolute 0.9–2.5 s → ~0.08 s.

**In-region confirmation** (throwaway 2 GB worker, cold), `light dome detection` phase vs
the OLD-code baselines on the same origins:

| Origin | OLD (baseline) | NEW | Speedup |
|---|---:|---:|---:|
| Phoenix | ~1726 ms | 207 ms | ~8× |
| Dallas | ~4516 ms | 185 ms | ~24× |

`find_nearby` returned cleanly both times. Confirmed → shipped.

### Tier 3 · dome naming parallelised + B8–9 suppression (2026-06-09)

A 5-city AWS funnel/profile run (`docs/perf_runs/findnearby_funnel_2026-06-09.*`, captured
with the funnel logging + `PYNIGHTSKY_NO_CACHE`) exposed two dome-stage costs:
1. **`dome naming` was serial** — `_settlement` per dome in a loop (~1.45 s uncached for
   dark origins with domes). Parallelised on the aws backend (`ThreadPoolExecutor`, same
   pattern as `_parallel_prefetch_settlements`); local stays serial per Nominatim policy.
2. **Bright origins waste the dome pipeline** — a dome must be ≥ origin+2 Bortle, and the
   brightest blob is Bortle 9, so for origin Bortle ≥ 8 no dome can ever qualify. Now
   gated by `origin_bortle <= 7` → detection + naming skipped (output unchanged: was empty).

In-region confirmation (throwaway 2 GB worker, uncached):

| City | phase | before | after |
|---|---|---:|---:|
| Sedona (B7) | dome naming | 1451 ms | 214 ms (~7×) |
| Knolls (B1) | dome naming | 1417 ms | 217 ms (~7×) |
| Atlantic City (B9) | dome detection + naming | 204 ms | 0 ms (suppressed) |

Both output-preserving; 390 tests pass.

### Worker cold start · non-blocking prewarm + memory A/B (2026-06-10)

The worker ran `_prewarm_rasters()` **synchronously at module init**, blowing Lambda's 10 s
init budget (`INIT_REPORT … Status: timeout`) — wasted init that re-ran into the first
invoke. Moved the warm-up to a **daemon thread** (mirrors the API's lifespan prewarm) and
extended it to warm rasters + PAD-US index + DynamoDB pool + ephemeris in the background.

In-region (throwaway 2 GB worker, cold containers):
- **`INIT` no longer times out: 10 s timeout → ~4.4–5.8 s.**
- **PAD-US load in-job → 0 ms** (background-prewarmed; was 0.45 s columnar / 5–24 s as the old dict).
- First cold-job `[profile] TOTAL` ~3.1–3.5 s (was dominated by the wasted init + cold PAD-US).

**Memory A/B (2 GB vs 3 GB; account caps Lambda at 3008 MB so 4 GB N/A):**

| cold sample | init | first-job TOTAL | billed GB-s |
|---|--:|--:|--:|
| 2 GB | ~4.4 s | 3481 ms | 16.0 |
| 3 GB (max) | ~4.6 s | 3117 ms | 23.5 |

3 GB shaved ~0.3 s off the invoke but **init was flat** (the cold-start dominator) at ~47%
more GB-s/invoke. **Decision: keep 2 GB — memory bump is a no-op.** Shipped the prewarm fix
only. (Out of scope per decision: keep-warm ping, provisioned concurrency — the one-time
~17 s first-container image lazy-load tax remains, only addressable by provisioned concurrency.)

## Reproduce

- `scripts/bench_padus_load.py` — PAD-US load + lookup benchmark (`--verify-against` for correctness).
- `scripts/profile_find_nearby.py` — per-phase profile across cities (warm + `--cold`).
- `scripts/diag_geocode_waste.py` — classify reverse-geocode probes (kept/duplicate/water).
- `scripts/profile_parallel_geocode.py` — offline serial-vs-parallel A/B (stubbed latency).
- `scripts/profile_aws.sh` + `scripts/aws_one_search.py` — run one search against the real
  aws backend with profiling (needs an authenticated session).

## Rasterio → tiled-grid lookup latency (2026-06-11)

Removing rasterio/GDAL replaced the COG `/vsis3` reads with our own S3 byte-range reads of
the tiled raw-binary grids (`gridraster`). A/B over **40 identical random points**, host →
S3 (us-east-1) over WAN — relative numbers are the signal; absolute is WAN-inflated vs an
in-region Lambda:

| | cold open | warm sample (median / p90 / max / mean) |
|---|---|---|
| OLD rasterio `/vsis3` COG | 368 ms | 154 / 292 / 562 / 164 ms |
| NEW gridraster S3 (host) | ~310 ms* | 181 / 277 / 360 / 195 ms |
| NEW in throwaway container | 313 ms | 198 / 333 / 589 / 224 ms |

\* A first run showed a 6.3 s cold open; decomposition (boto3 import 130 ms, client create
46 ms, cold-TLS GET 202 ms, `open_s3` w/ reused client 59 ms) proved it a one-off region/IMDS
resolution stall, not reproducible. Production reuses one S3 client and warms both grids in
`_prewarm`.

**Verdict: no meaningful lookup slowdown.** Single-pixel reads are S3-RTT-bound (~150–200 ms
over WAN; <5 ms in-region), so old vs new is a wash — new reads 4 bytes vs a whole COG tile,
and has a better tail. Cold open is equal/better. Correctness: all 10 test cities
(VIIRS + Falchi paths) returned identical SQM/Bortle/source vs local-grid == rasterio.
Image: worker **1.28 GB** (rasterio/GDAL absent). Harness: `out/profile_baseline.py` (host
old-vs-new A/B), `out/city_probe.py` (profiled 10-city probe; run in-container with
`PYNIGHTSKY_BACKEND=aws`).
