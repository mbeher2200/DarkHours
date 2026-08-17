# find_nearby Performance

Current performance architecture of `darksky.find_nearby` (the `/nearby` async job),
plus the investigation log that produced it. Facts below verified against the code
and CDK on 2026-07-18; in-region timings are from the dated runs in Appendix A.

## Current state

End-to-end `/nearby` is **worker-bound**: the API side (enqueue + job polls) runs
warm at 2–15 ms, and the SQS worker does all the work. The optimizations that got
here, all shipped and still in effect:

- **Keep-warm on both Lambdas.** EventBridge pings the API and the worker every
  4 minutes (`cdk/lambda_api_stack.py`); a Record-less event makes the worker run
  its prewarm synchronously. Real jobs skip the ~4.6 s cold Init entirely.
- **Background prewarm** (`apps/worker/handler.py::_prewarm`, daemon thread on
  cold start): warms the raster grids, PAD-US + OSM POI indexes, the DynamoDB
  connection pool, and the ephemeris — so those costs are off the job path.
- **Columnar PAD-US index.** Sorted-uint64 `.npz` + `np.searchsorted`
  (`darksky._load_padus_h3_index`): ~450 ms cold load (down ~35× from the original
  dict build), 0 ms in-job thanks to prewarm. See `docs/PADUS_INDEX.md`.
- **Rasterio-free raster reads.** Window reads are tiled-grid S3 byte-range GETs,
  tiles fetched concurrently (`gridraster.py`; see `docs/RASTERIO_REPLACEMENT.md`).
  Typically ~200–400 ms per dataset in-region for the full 150-mile window.
- **Right-sized raster windows (conditional dome fetch).** `find_nearby` fetches a
  `radius_miles + 2`-sized window instead of the unconditional 150-mile one; a
  **VIIRS-only** 150-mile fetch is issued only when the origin resolves Bortle ≤ 6,
  submitted right after the origin lookup so it overlaps the extraction/clustering
  CPU phases (joined just before dome detection — new profile phase
  `dome window read (join)`, ~30–50 ms in-region). Bright origins (B7–9, see the
  2026-08-16 entry for why 7 was folded into the skip) skip the outer ~5/6 of the
  old fetch entirely, and the big
  Falchi window is gone on the two-step path (dome detection is VIIRS-only). The
  known-dark repeat-origin peek path still pulls Falchi at the full 150 miles
  alongside VIIRS (simpler than tracking per-dataset bounds; extraction only
  needs radius_miles of it) — a known, accepted waste on an already-warm,
  already-fast path. An in-process bortle-cache peek picks the right single
  fetch for repeat origins. Kill switch:
  `PYNIGHTSKY_SMALL_WINDOW=0`. Warm in-region Phoenix phase-sum: 434 → 122 ms
  (2026-07-22 entry).
- **S3 client pool sized to the tile fan-out.** `max_pool_connections` 10 → 32
  (`PYNIGHTSKY_S3_POOL`): the two datasets' 8-worker tile pools plus the
  conditional dome fetch exceeded boto3's default 10, logging "Connection pool is
  full" churn. 32 removes the churn and tightens raster-read tails.
- **Vectorized dome detection.** The per-blob centroid loop was replaced with
  batched `bincount`/`center_of_mass(index=)` ops (~12–30× faster, ~80–200 ms), and
  the whole dome pipeline is skipped when the origin is Bortle ≥ 7 — free for B8–9
  (no dome could ever mathematically qualify), a deliberate trade-off for B7 (see
  2026-08-16 entry).
- **Reverse-geocode discipline.** 8-mile pre-dedup of candidate probes
  (`_NAME_DEDUP_MILES`), POI/PAD-US-index-first naming, a 16-point-compass
  directional pre-dedup of dome candidates before naming (`_dedup_domes_by_direction`,
  2026-08-16 entry), a priority-ordered prefix cap on the dark-candidate Tier-3
  prefetch (`_PREFETCH_CANDIDATE_PREFIX`, 2026-08-16 entry), and — on the aws
  backend only — parallel AWS Location calls (~87 ms each in-region) with a
  pooled client. The local backend stays serial per Nominatim's 1 req/s policy.
- **Absolute-grid-anchored pixel labels.** Dome and dark-candidate pixel lat/lon
  labels are built from the raster's fixed absolute grid origin (`_window_pixel_grid`)
  instead of each window's own bounds, so the reverse-geocode cache key for a given
  real-world light source is stable across different search origins (2026-08-16 entry).
- **Shared `/suggest` cache and Location-Service usage monitoring.** A short-TTL
  shared cache tier behind `/suggest`'s per-container LRU, and anomaly monitoring
  scoped to Amazon Location Service so an unexpected jump in call volume is
  caught in hours, not at the end of a reporting cycle (2026-08-16 entry).
- **Drive times via `CalculateRoutes`, per-leg, in parallel.** One point-to-point
  call per cache-missing leg (bounded thread pool), replacing the batched
  `CalculateRouteMatrix` call whose undocumented 60 km `Avoid` cap silently killed
  whole searches (see the 2026-07-12 entry). Each leg is cached 24 h
  (`_DRIVE_CACHE_TTL`); repeat-area searches pay ~0 for this phase. Ferry /
  unpaved-tail warnings come from the route legs.
- **Memory:** both Lambdas run at 3008 MB (the account cap; a 2 GB→3 GB A/B showed
  init flat and only ~0.3 s invoke gain, but the bump shipped later with the
  worker's larger workload).

Representative warm-container, cache-warm total: **~1–3 s** per search, dominated
by first-visit drive-time legs and the two raster window reads (Appendix A,
2026-06-16 table — measured before the CalculateRoutes switch; first-visit
drive-time timing has not been re-profiled in-region since).

## Instrumentation (opt-in, kept)

- `PYNIGHTSKY_PROFILE=1` — per-phase wall time + cache hit/miss delta logged from
  `find_nearby` (`[profile]` lines; `darksky._Profiler`).
- `cache.stats` — hit/miss counter at the `cache.get` chokepoint (`cache.py`).
- `scripts/profile_aws.sh` + `scripts/aws_one_search.py` — one profiled search
  against the real aws backend (resource names from env; needs an authenticated
  session).
- The throwaway in-region test-worker recipe in `CLAUDE.md` for validating changes
  on real infra without touching the deployed worker.

## Provider latency reference (as measured 2026-06)

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

- **Re-profile drive times in-region** since the CalculateRouteMatrix →
  CalculateRoutes switch (N client-parallel calls, ≤11/search): the 2026-06-16
  phase timings predate it. Repeat-area searches are cache-served either way.
- **GeoRoutes cost parity** between matrix pairs and per-leg requests is likely
  but was never confirmed numerically (pricing figures sit behind AWS's
  JS-rendered calculator).
- AWS Location TPS quota (~50 req/s account default) should be raised before
  scaling parallel geocode wider; adaptive retries cushion bursts today.

## Reproduce

- `scripts/bench_padus_load.py` — PAD-US load + lookup benchmark (`--verify-against`).
- `scripts/profile_find_nearby.py` — per-phase profile across cities (warm + `--cold`).
- `scripts/diag_geocode_waste.py` — classify reverse-geocode probes (kept/duplicate/water).
- `scripts/profile_parallel_geocode.py` — offline serial-vs-parallel A/B (stubbed latency).
- `scripts/bench_dome_detection.py` — dome-detection benchmark on real windows.
- `scripts/profile_aws.sh` + `scripts/aws_one_search.py` — real-backend profiled search.

---

## Appendix A — investigation log (2026-06-09 → 2026-07-12)

One variable at a time; before/after numbers preserved.

### 2026-06-09 — baseline & root causes

`find_nearby` was network-bound on cold searches: ~85–95% of wall time was reverse
geocoding + an Overpass call that always timed out.

Local backend, radius 60 mi, city-centre origins:

| Origin | Wall | cache hit-rate | Note |
|---|---:|---:|---|
| Los Angeles | 28.9 s | 0% | cold |
| New York | 26.4 s | 9% | |
| Chicago | 27.5 s | 8% | |
| Denver | 1.3 s | 50% | Overpass result cached |
| Phoenix | 62.0 s | 2% | 42 geocode misses |
| Atlanta | 36.0 s | 5% | |
| LA (repeat, warm) | 1.3 s | 92% | everything cached |

LA cold breakdown (28.9 s): `overpass join` 15007 ms (timeout every call),
`jit geocode candidates` 9421 ms (9 Nominatim misses × ~1.1 s), `padus index load`
2306 ms (one-time), `light dome detection` 937 ms, raster reads ~280 ms.

Root causes: (1) dead Overpass endpoint `overpass.private.coffee` — guaranteed 15 s
join timeout and zero natural-area names; only `overpass-api.de` responded
(~7.6 s). (2) Duplicate-dominated reverse geocoding — on Phoenix, 36 of 43 probes
returned duplicate town names (median 5.3 mi apart).

**Tier-0 fixes shipped:** Overpass URL reverted to `overpass-api.de` (local
backend only); 8-mi geocode pre-dedup (Phoenix cold probes 43 → 25); parallel
reverse-geocode on the aws backend; pooled boto3 `location` client.

**In-region validation** (throwaway `proftest` worker, x86_64/2 GB): AWS Location
≈ 87 ms/call; parallel geocode ≈ 4× (`jit geocode candidates` 1746 → 339 ms).
New dominant cold cost: `padus index load` = 5–24 s per cold container.

### 2026-06-09 — PAD-US index: dict build → columnar uint64 + binary search

Replaced the ~1.37M-entry string-keyed dict build with a sorted `uint64` cell
array + parallel name/blacklist arrays via `np.searchsorted`.

| Metric | Before (dict) | After (columnar) |
|---|---:|---:|
| Index load, median (laptop) | 1787.7 ms | 53.7 ms (~33×) |
| Lookup | 0.85 µs/pt | 2.54 µs/pt (≈0.3 ms/search — negligible) |
| Index size | 10.6 MB | 3.5 MB |
| Correctness | — | 0 mismatches over 50,006 checks |

In-region steady cold-container load ≈ 450 ms (down from 15–24 s, ~35×). The
first-ever container after an image deploy paid a one-time ~17 s image lazy-load
tax — a container-era artifact that disappeared when the worker became a zip
Lambda (see `docs/RASTERIO_REPLACEMENT.md`).

### 2026-06-09 — vectorized dome detection + naming parallelism + B8–9 suppression

97–98% of `_find_light_domes_from_array` was the per-blob centroid loop
(O(blobs × pixels)). Replaced with batched `np.bincount` + scipy
`center_of_mass`/`maximum` with `index=` — output-identical.

| Origin (≈1.3M-px window) | Before | After |
|---|---:|---:|
| Los Angeles (315 domes) | 928 ms | 79 ms |
| New York (829 domes) | 2547 ms | 84 ms |
| Phoenix (in-region) | ~1726 ms | 207 ms |
| Dallas (in-region) | ~4516 ms | 185 ms |

Dome **naming** parallelised on aws (~7×: 1451 → 214 ms on Sedona), and the whole
dome pipeline gated on `origin_bortle <= 7` (a dome must be ≥ origin+2 and the
brightest blob is B9, so B8–9 origins can never qualify — was pure waste).

### 2026-06-10 — non-blocking prewarm + memory A/B

The worker ran `_prewarm_rasters()` synchronously at module init, blowing Lambda's
10 s init budget (`INIT_REPORT … Status: timeout`). Moved to a daemon thread and
extended to rasters + PAD-US + DynamoDB pool + ephemeris. Init 10 s-timeout →
~4.4–5.8 s; PAD-US in-job → 0 ms; first cold-job TOTAL ~3.1–3.5 s.

Memory A/B (2 GB vs 3008 MB): init flat, invoke −0.3 s, +47% GB-s → decision at
the time was to stay at 2 GB. (Both Lambdas later moved to 3008 MB as their
workload grew; that is the deployed config today.)

### 2026-06-11 — rasterio → tiled-grid lookup latency A/B

40 identical random points, host → S3 over WAN (relative numbers are the signal):

| | cold open | warm sample (median / p90 / max / mean) |
|---|---|---|
| OLD rasterio `/vsis3` COG | 368 ms | 154 / 292 / 562 / 164 ms |
| NEW gridraster S3 (host) | ~310 ms | 181 / 277 / 360 / 195 ms |
| NEW in throwaway container | 313 ms | 198 / 333 / 589 / 224 ms |

Verdict: no meaningful lookup slowdown — single-pixel reads are S3-RTT-bound, and
the new path reads 4 bytes instead of a whole COG tile with a better tail.
Correctness: all 10 test cities identical SQM/Bortle/source across old/new/local.

### 2026-06-16 — lifecycle profile, worker keep-warm, drive-time caching

In-region warm profile (throwaway worker cloned from the deployed artifact,
per-phase ms):

| phase | cold+cachecold | warm+cachecold | warm+cachewarm |
|---|--:|--:|--:|
| drive times (aws, uncached) | 1961 | 934 | 1978 |
| viirs window read (S3) | 562 | 928 | 347 |
| falchi window read (S3) | 378 | 371 | 200 |
| extract+cluster+dome-detect (CPU) | 656 | 558 | 543 |
| jit geocode | 82 | 826 | 5 |
| dome naming | 459 | 323 | 62 |
| origin settlement+lookup | 1385 | 377 | 9 |
| **TOTAL handler** | **5499** (+Init 4454) | **4340** | **3158** |

Headline: even fully cache-warm, drive times ≈ 63% of the job. Shipped: the
worker keep-warm EventBridge rule (rate 4 min, synchronous prewarm on warmup
events; verified cold ping Init 4.93 s + 1.56 s prewarm off the user path) and the
per-leg drive-time cache (`route_drive|…` keys) so only cache-missing legs hit
the routing API.

### 2026-07-12 — CalculateRouteMatrix → CalculateRoutes (ferry/dirt-road detection)

The "warn on ferry-bridged / unpaved routes" feature (#107) didn't work in prod.
Two confirmed problems with `CalculateRouteMatrix`, verified with live calls
against the exact Juneau, AK coordinates from the bug report:

1. `RouteMatrixEntry` carries only `{Distance, Duration, Error}` — no `Notices`,
   no `Legs` — so a ferry/dirt-road violation can't be read off it structurally.
2. **The primary cause:** passing `Avoid` with `TravelMode=Car` imposes an
   undocumented **60 km cap** per origin–destination pair; exceeding it raises
   `ValidationException` for the *entire batched request*. At this app's 60/120 mi
   radii that's routine, so every candidate's drive time in the search silently
   failed (blanket `except Exception` + the frontend's ≥1-successful-leg gate) —
   every place fell back to a plain unlabeled Maps link, exactly the prod symptom.

Fix (PR #108): `_aws_drive_times` calls `CalculateRoutes` once per missing leg,
client-parallel (bounded pool, ≤11 legs/search). No 60 km cap (confirmed live: a
77.8 km Juneau leg with the same `Avoid` succeeds and returns the `Ferry`-typed
leg). Ferry detection uses the structural `Legs[].Type == "Ferry"` signal. The
24 h per-leg cache is unchanged. Cost parity with the matrix API is likely
(both bill per route) but unconfirmed numerically; in-region latency for
first-visit searches not yet re-profiled.

### 2026-07-22 — right-sized raster windows + S3 pool bump

Profiling showed the raster window read phase at 57–72% of request compute for
bright origins, yet the window was always sized `max(radius_miles, 150)` for dome
detection — which is skipped entirely at Bortle ≥ 8 (the common urban origin), so
~5/6 of the fetched area was provably discarded there. Root cause: the window is
sized before `origin_bortle` is known, deliberately, to overlap the S3 fetch with
the DynamoDB origin lookup.

Fix shipped: fetch `radius_miles + 2` always (overlap preserved); when the origin
resolves ≤ 7, submit a VIIRS-only 150-mile fetch that overlaps the extraction /
clustering CPU and is joined just before dome detection (`dome window read
(join)` phase). Dome detection never used Falchi, so the big Falchi window is
gone on the two-step (first-visit dark-origin) path. The known-dark repeat-origin
peek path still fetches Falchi at the full 150 miles alongside VIIRS — accepted,
since that path is already fast and warm. In-process bortle-cache peek short-circuits to the right
single fetch for repeat origins in a warm container. Flags:
`PYNIGHTSKY_SMALL_WINDOW` (kill switch), `PYNIGHTSKY_S3_POOL` (default 32).

WAN A/B (laptop → region, fresh process per run, r=60, medians of 3):

| origin | metric | legacy | small window |
|---|---|--:|--:|
| Phoenix (B9) | raster window reads | 3025 ms | 1605 ms |
| Phoenix (B9) | phase-sum TOTAL | 4123 ms | 2662 ms |
| Flagstaff (B7) | raster window reads | 2025 ms | 1183 ms |
| Flagstaff (B7) | dome window read (join) | — | 1111 ms |
| Flagstaff (B7) | phase-sum TOTAL | 3446 ms | 4063 ms |

The apparent Flagstaff regression is a WAN artifact: at laptop RTTs the dome
fetch can't hide behind ~100 ms of CPU. In-region it can — throwaway arm64
container worker (3008 MB), warm containers, medians of 3:

| scenario | legacy | small, pool 10 | small, pool 32 |
|---|--:|--:|--:|
| Phoenix warm: raster reads | 224 ms | 71 ms | 51 ms |
| Phoenix warm: phase-sum TOTAL | 434 ms† | 140 ms | 122 ms |
| Flagstaff two-step: dome join | — | 30 ms | 53 ms |
| Flagstaff two-step: phase-sum TOTAL | 1014 ms† | 793 ms | 753 ms |
| Flagstaff repeat (peek → single big fetch) TOTAL | 655 ms† | 622 ms† | — |
| "Connection pool is full" warnings | 0 | 2 | 0 |

† n=1 (single legacy/scenario sample in that matrix; the pool-10 vs pool-32
columns are n=3).

Headlines: bright-origin warm phase-sum **434 → 122 ms (−72%)**; the dark-origin
two-step is *also* faster in-region (no big Falchi + overlap ≈ free dome fetch);
the repeat-origin peek path matches legacy as designed. The pool bump was
promoted after it eliminated the (organically confirmed) connection-pool churn
and cut the worst warm Phoenix raster sample from 275 → 57 ms.

**Output-parity caveat (accepted):** the extraction meshgrid linspaces over the
*requested* window bounds while `read_window` round()-snaps to pixels, so
shrinking the window shifts assigned pixel coordinates sub-pixel (≤ ~0.3 mi at
the radius edge). Verified on real runs: Flagstaff domes byte-identical across
all runs; Phoenix results 9/10 identical with one band-edge swap (a B2 at
50.8 mi for a B3 at 25.6 mi; `extract_raw` 107 → 113) and 3rd-decimal SQM drift.
Dark-origin (≤ 7) dome inputs are bit-identical by construction. Exact parity
would need a pixel-center meshgrid derived from grid geometry — future work.
**Fixed 2026-08-16, see below** — this sub-pixel drift turned out to be the same
root cause as the cross-search reverse-geocode cache-key instability.
Hermetic coverage: `tests/test_small_window.py` (fetch-path selection, VIIRS-only
dome fetch, kill switch, degradation, flag-off-vs-on output equivalence).

### 2026-08-16 — Location Service call-volume efficiency

A traffic surge (Aug 8–9, see `docs/OBSERVABILITY.md`'s ~60x anomaly entry) pushed
Amazon Location Service call volume far above its normal baseline, with
ReverseGeocode alone accounting for 92% of it (52,213 calls in the surge window).
A usage investigation (Cost Explorer and CloudWatch Logs Insights used purely as
call-volume data sources, cross-referenced against real production origins run
through the actual `find_nearby` pipeline) traced this to several fixable issues,
all shipped here:

1. **Reverse-geocode cache-key instability (root cause, not dome-specific).** The
   "output-parity caveat" immediately above — window-relative `np.linspace` instead
   of pixel-center coordinates derived from the raster's absolute grid — doesn't
   just shift displayed coordinates by up to ~0.3 mi at the window edge; it means
   two different search origins whose windows both cover the *same real-world
   light source* label it with two different (lat, lon) values, so the
   `nominatim_rev|{lat:.3f}|{lon:.3f}` cache key never matches and the same place
   gets a fresh live call from every origin that detects it. Measured directly: 4 real
   origins around Atlanta (Athens, Macon, Chattanooga, Birmingham GA/TN/AL), each
   independently detecting "Atlanta's dome," resolved to 4 different cache keys up
   to 1.4 mi apart — 0 of 4 converged. Fixed with `_window_pixel_grid`
   (`darksky.py`), anchored to the same absolute grid origin `gridraster.read_window`
   already uses internally (`GridArray.west/north/x_res/y_res`, exposed via a new
   `grid_meta()` accessor); `find_nearby`'s shared precompute and the two-step
   dome-window fetch now both build grids through it instead of ad hoc
   per-window `linspace`. 3 of the same 4 Atlanta-area origins converged to
   bit-identical coordinates after the fix (the 4th sits at the edge of the
   150-mile radius — a smaller, separate residual from genuine boundary
   clipping). On a 45-origin random sample, ~16.5% of dome-naming calls were
   redundant re-detections of an already-cached place purely due to this bug — a
   floor, not a ceiling, since real traffic clusters around popular
   destinations far more than a random sample can show. Tests:
   `TestWindowPixelGrid` in `tests/test_light_dome_array.py`.
2. **Dome candidates were named before deduping, not after.** `find_nearby`
   already computes a 16-point compass bearing per dome candidate for the
   `direction` field it displays (`_bearing_label`); it just wasn't using that
   bearing until *after* paying to name all `_MAX_DOMES*2` (20) capped
   candidates. `_dedup_domes_by_direction` now buckets by that same bearing and
   keeps only the nearest candidate per bucket *before* the `_settlement()`
   fan-out, with the existing name-based dedup left in place afterward as a
   free safety net. Measured on 13 real search origins (real S3 raster data,
   real production code): 60.8% fewer dome-naming calls (260 → 102), no change
   to the shown-dome set in any tested case.
3. **Dome detection skip threshold moved from Bortle ≥ 8 to ≥ 7.** The ≥ 8 skip
   was already free (a dome must be ≥ origin+2 Bortle and 9 is the ceiling, so
   an 8–9 origin can never have a qualifying dome — pure waste eliminated, zero
   information loss). Bortle 7 is a genuine trade-off — validated on 10 real B6/7
   origins, both classes still hit the full 20-candidate naming cap today, so
   real dome data does exist at B7 and this suppresses it. Shipped anyway: at
   Bortle 7 the horizon is already washed out by skyglow, so a named dome
   warning doesn't meaningfully change an already-degraded observing plan.
   Affects ~20.5% of real searches; ~18% additional cut to ReverseGeocode call
   volume on its own, ~38% combined with fix #2 above (dome-side only; #1's
   cache-hit-rate benefit is separate and additive). `_dome_search =
   origin_bortle <= 6` (was `<= 7`).
4. **Dark-candidate Tier-3 prefetch capped to a priority-ordered prefix.**
   `_parallel_prefetch_settlements` resolved every Tier-3 representative in the
   entire up-to-60-candidate band-selected list before the main loop in
   `_jit_geocode_candidates` even started walking toward `_MAX_RESULTS=10`.
   Measured on 25 real origins: 50.9% of prefetched calls were never consumed
   (234 prefetched, 115 actually needed), 100% of the waste on international
   searches (0 of 13 US origins in that batch needed any Tier-3 call vs. 12 of
   12 international, averaging 19.5 each). Capped to the first
   `_PREFETCH_CANDIDATE_PREFIX=25` candidates; a candidate beyond the cap that
   the loop still reaches falls back to the existing direct `_settlement()`
   call on a prefetch-map miss, so this is a call-volume cut, not a behavior
   change.
5. **Shared, short-TTL cache for `/suggest`.** `_suggest_cached` was an
   in-process `functools.lru_cache` only — no benefit across different warm
   containers or after a cold start. Added a second tier on the existing
   shared cache (`darkhours.cache`; DynamoDB on aws), a few hours' TTL, keyed
   by the raw query string, checked only on an in-process miss.
6. **Usage anomaly monitoring scoped to Amazon Location Service**
   (`cdk/lambda_api_stack.py`: `LocationServiceUsageMonitor` /
   `LocationServiceUsageAlert`). The existing account-wide AWS Budget
   (`docs/OBSERVABILITY.md`) only evaluates at billing-cycle granularity — it
   would not have flagged this surge until much later. A monitor scoped
   specifically to `SERVICE = Amazon Location Service`, with an immediate SNS
   notification to the existing `AlarmTopic` on a meaningful deviation from
   typical usage. See `docs/OBSERVABILITY.md`'s alarm section.

Not shipped this round (tracked as open items, not silently dropped):
re-enabling Overpass on the aws backend (a fresh live check found it currently
less reliable than the June 2026 baseline — 2/5 requests failed, latency up to
25.2s — needs a properly rate-limiter-paced retest before shipping); and the
global offline settlement-name index that would close the US/international
naming gap for good (0 of 13 US origins in a 25-origin sample needed any live
Tier-3 call vs. 12 of 12 international origins, averaging 19.5 each).

## Appendix B — raw data

`docs/perf_runs/findnearby_funnel_2026-06-09.jsonl` — the 5-city AWS in-region
funnel/profile run (per-phase wall times, candidate-funnel counts, surfaced
coordinates) captured with the funnel logging + `PYNIGHTSKY_NO_CACHE=1` on a warm
2 GB worker. Steady-state per-phase medians from that run: raster window reads
~110–410 ms each, extract ~220–300 ms, dome detection ~180–210 ms, drive times
~180–780 ms, TOTAL 1.5–4.7 s across San Diego / Sedona / Knolls / Roswell /
Atlantic City.
