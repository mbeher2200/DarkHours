# Test Suite

874 tests across 39 files (as of 2026-07-22). A default run is offline and
deterministic — everything external is opt-in via markers.

## How to run

```bash
# Default run — hermetic apart from the bundled ephemeris; aws/live auto-skip
python3 -m pytest -q

# CI-style fast suite — also skip ephemeris-dependent tests
python3 -m pytest -q -m "not eph"

# Coverage report
python3 -m pytest --cov=darkhours --cov-report=term-missing -q

# Real AWS integration tests (opt-in, requires credentials + resource env vars)
PYNIGHTSKY_BACKEND=aws python3 -m pytest -q -m aws

# Live provider smoke tests (opt-in, hits real third-party APIs)
PYNIGHTSKY_LIVE=1 python3 -m pytest -q -m live
```

## Marker guide (`pytest.ini`)

| Marker | When it runs | What it needs |
|--------|-------------|---------------|
| *(none)* | Always | Nothing external |
| `eph` | Locally by default | `de421.bsp` ephemeris file in the package directory |
| `aws` | Only when explicitly opted in | `PYNIGHTSKY_BACKEND=aws` + live DynamoDB/S3 + credentials + cache/raster env vars |
| `live` | Only with `PYNIGHTSKY_LIVE=1` | Network access to the real providers (Open-Meteo, 7Timer, Celestrak, Nominatim, AWS Location) |

Unmarked tests are hermetic: no network, no ephemeris, no rasters.

## Test inventory

**Engine formulas & models**

| File | Module(s) tested | Markers | What it validates |
|------|-----------------|---------|-------------------|
| `test_scoring.py` | `scoring.py` | — | `rate_night()` weighted geometric mean, weight redistribution; `weighted_weather_score()` 3× dark weighting |
| `test_moonlight.py` | `moonlight.py` | — | `ks_delta_mag()` (distance/separation/altitude/AOD), `ks_moon_credit()`, `moon_wash_severity()` |
| `test_predictor_formulas.py` | `predictor.py` | — | Moon score formula, crescent exemption threshold, Bortle→score conversion |
| `test_condition_vectors.py` | `predictor.py` | — | Per-target viability vectors: cloud/transparency blocks, light-dome and lunar-proximity blockers, effective windows, rollup |
| `test_assemble_night_cycle_window.py` | `predictor.py` / `sky_events.py` | — / `eph` | Lunar-cycle dark-analysis window wiring in `assemble_night` |
| `test_date_tz.py` | `predictor.py` / `targets.py` | — | Date/timezone correctness (incl. the UTC-vs-local night_date regression) |
| `test_weather_conditions.py` | `weather.py` | — | `rate_conditions()` all branches (cloud, seeing, wind, humidity, AOD/PM2.5, precip cap); Open-Meteo parsing; 7Timer merge tolerance |
| `test_weather_fallback.py` | `weather.py` | — | Provider selection and fallback |
| `test_moon_events.py` | `moon_events.py` | — / `eph` | `classify_full_moon()` boundaries; lunar-eclipse detection |
| `test_sky_events.py` | `sky_events.py` | — / `eph` | `dark_moon_intervals()`, event finders, `moon_phase_info()` |
| `test_milky_way.py` | `milky_way.py` | — / `eph` | `gal_to_radec()`, core geometry, arch-summary score/moon penalty/window |
| `test_mw_geometry.py` | `milky_way.py` | `eph` | 5-latitude geometry regression (NH/equatorial/SH) |
| `test_mw_brightness.py` | `milky_way.py` | — | Milky Way brightness/visibility factors |
| `test_targets_helpers.py` | `targets.py` | — | RA/dec parsing; visibility-window segment detection |
| `test_meteor_shower_decay.py` | `targets.py` | — | `effective_zhr()` IMO decay model, half-window solver, catalog constants |
| `test_aurora_model.py` | `aurora.py` | — | Geomagnetic latitude, Kp viewline, visibility tiers, look bearing |
| `test_aurora_provider.py` | `aurora.py` | — | SWPC 3-day/27-day fetch, parse, cache, night rollup |
| `test_tle_provider.py` | `tle_provider.py` | — | TLE parsing, Starlink train filter, `get_tle()` state machine |
| `test_aqicn.py` | `aqicn.py` | — | WAQI haze cross-check: station distance filter, thresholds, caching |

**Dark-sky search, rasters & indexes**

| File | Module(s) tested | Markers | What it validates |
|------|-----------------|---------|-------------------|
| `test_darksky_formulas.py` | `darksky.py` | — | SQM conversions (VIIRS & Falchi), Bortle class thresholds, djlorenz zones |
| `test_gridraster.py` | `gridraster.py` / `gridbuild.py` | — | Tiled-grid `sample()`/`read_window()` semantics, bilinear resample, tile math |
| `test_light_dome.py` | `light_dome.py` | — | Directional dome scores, Walker kernel, summarize/glow_toward |
| `test_light_dome_array.py` | `darksky.py` | — | Dome blob detection on pure arrays (vectorized path) |
| `test_landscape_prominence.py` | `light_dome.py` | — | Dome prominence gating and sky-state classification |
| `test_lightdome_index.py` | `light_dome.py` | — | Precomputed light-dome H3 index round-trip and lookup |
| `test_poi_index.py` | `darksky.py` | — | OSM POI index loader/encoder, POI-first candidate surfacing, naming gates |
| `test_water_prefilter.py` | `darksky.py` | — | Land-mask pre-filter of dark candidates |
| `test_jit_geocoding.py` | `darksky.py` | — | Reverse-geocode dedup and just-in-time naming |
| `test_perf_changes.py` | `darksky.py` | — | Output-preserving guarantees of shipped perf optimizations |
| `test_small_window.py` | `darksky.py` | — | Right-sized raster windows: fetch-path selection (peek/two-step), VIIRS-only dome fetch, kill switch, degradation, output equivalence vs the legacy always-big path |

**Apps, adapters & integration**

| File | Module(s) tested | Markers | What it validates |
|------|-----------------|---------|-------------------|
| `test_adapters.py` | `cache.py` / `location.py` / `ports.py` | — | Local vs Dynamo cache/geocode contract parity (moto), `S3RasterSource` |
| `test_aws_location.py` | `location.py` / `darksky.py` | — | AWS Location geocoding + GeoRoutes drive times (mocked boto3), per-leg cache |
| `test_api.py` | `apps/api/main.py` | — / `eph` | All endpoints, input validation, DoS guards, error paths |
| `test_jobs.py` | `apps/jobs.py` | — | Inline/SQS job lifecycle, worker handler, 202→done flow |
| `test_warmer.py` | `apps/warmer` | — | TLE warmer handler: warm-all-ok, failure reporting, stale detection |
| `test_aws_smoke.py` | `darksky.py` / `cache.py` | `aws` | Real DynamoDB cache round-trip; real S3 grid lookups (VIIRS + Falchi) |
| `test_provider_smoke.py` | providers | `live` | Live connectivity: Open-Meteo, 7Timer, Celestrak, Nominatim, AWS Location |

## Known gaps (future work)

- **`satellites.py`** — `satellite_passes()` staleness guard and
  `starlink_train_passes()` grouping still lack direct coverage (need real TLEs +
  Skyfield).
- **`trip.py`** — `plan_trip()` ranking and dual-TTL caching.
- **Rendering** — `render_report.py`, `render_calendar.py`, `render_trip.py`,
  `format_ctx.py` pure-function helpers.
