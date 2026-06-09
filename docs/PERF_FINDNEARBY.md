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

## Reproduce

- `scripts/profile_find_nearby.py` — per-phase profile across cities (warm + `--cold`).
- `scripts/diag_geocode_waste.py` — classify reverse-geocode probes (kept/duplicate/water).
- `scripts/profile_parallel_geocode.py` — offline serial-vs-parallel A/B (stubbed latency).
- `scripts/profile_aws.sh` + `scripts/aws_one_search.py` — run one search against the real
  aws backend with profiling (needs an authenticated session).
