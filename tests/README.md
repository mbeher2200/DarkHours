# Test Suite

## How to run

```bash
# CI baseline — hermetic, no external dependencies
python3 -m pytest -q -m "not eph and not aws"

# Full local run (needs de421.bsp in the package directory)
python3 -m pytest -q -m "not aws"

# Coverage report
python3 -m pytest --cov=PyNightSkyPredictor --cov-report=term-missing -q -m "not aws"

# Real AWS integration tests (opt-in, requires credentials + env vars)
PYNIGHTSKY_BACKEND=aws python3 -m pytest -q -m aws
```

## Marker guide

| Marker | When to skip | What it needs |
|--------|-------------|---------------|
| *(none)* | Never — always safe | Nothing external |
| `eph` | In CI (auto-skipped) | `de421.bsp` ephemeris file in the package directory |
| `aws` | Always unless explicitly opted in | `PYNIGHTSKY_BACKEND=aws` + live DynamoDB/S3 + credentials |

Unmarked tests are hermetic: no network, no ephemeris, no rasters.

## Test inventory

| File | Module(s) tested | Markers | What it validates |
|------|-----------------|---------|-------------------|
| `test_scoring.py` | `scoring.py` | — | `rate_night()` weighted geometric mean, weight redistribution; `weighted_weather_score()` 3× dark weighting |
| `test_weather_conditions.py` | `weather.py` | — | `rate_conditions()` all formula branches (cloud, seeing, wind, humidity, precip cap); `_parse_open_meteo_hourly()` precip_type derivation; `_merge_7timer()` 90-min tolerance |
| `test_weather_fallback.py` | `weather.py` | — | Provider selection and fallback |
| `test_moonlight.py` | `moonlight.py` | — | `ks_delta_mag()` (distance/separation/altitude), `ks_moon_credit()` (monotonicity/bounds), `moon_wash_severity()` (all categories) |
| `test_moon_events.py` | `moon_events.py` | — / `eph` | `classify_full_moon()` boundary conditions; `find_lunar_eclipses()` and `eclipses_for_night()` |
| `test_sky_events.py` | `sky_events.py` | — / `eph` | `dark_moon_intervals()`, `find_event()`, `find_last_event()`, `moon_phase_info()` |
| `test_milky_way.py` | `milky_way.py` | — / `eph` | `gal_to_radec()`, `mw_theoretical_core_max()`, `mw_max_visible()`, 5-latitude regression; `milky_way_arch_summary()` score formula, moon penalty, arch window |
| `test_mw_geometry.py` | `milky_way.py` | `eph` | 5-latitude geometry regression (NH/equatorial/SH) |
| `test_targets_helpers.py` | `targets.py` | — | `_parse_ra()`, `_parse_dec()` string parsing; `_find_windows()` segment detection |
| `test_darksky_formulas.py` | `darksky.py` | — | `radiance_to_sqm()`, `luminance_to_sqm()` (VIIRS & Falchi formulas); `sqm_to_bortle()` all class thresholds; `sqm_to_zone()` djlorenz zones |
| `test_predictor_formulas.py` | `predictor.py` | — | Moon score formula (K&S-weighted), crescent exemption threshold, Bortle→score conversion |
| `test_tle_provider.py` | `tle_provider.py` | — | `_parse_mean_motion()`, `_parse_launch_date()`, `_filter_train_tles()` (MM + launch-date filter); `get_tle()` 4-state machine (hit / miss+fetch / stale / no-data) |
| `test_adapters.py` | `cache.py` / `ports.py` | — | Contract tests for LocalFileCache, DynamoCache, LocalGeocodeStore, DynamoGeocodeStore, S3RasterSource |
| `test_aws_location.py` | `location.py` | — | Forward/reverse geocoding (mocked boto3), backend dispatch, cache-hit bypass |
| `test_api.py` | `apps/api/main.py` | — / `eph` | All endpoints, input validation, DoS guards, 400/422 paths |
| `test_jobs.py` | `apps/jobs.py` | — | Inline/SQS job lifecycle, worker handler, 202→done flow |
| `test_warmer.py` | Warmer handler | — | TLE warm-all-ok, failure reporting, stale detection |
| `test_aws_smoke.py` | `darksky.py` / `cache.py` | `aws` | Real DynamoDB cache round-trip; real S3 raster lookups (VIIRS + Falchi) |

## Known gaps (future work)

- **`satellites.py`** — `satellite_passes()` staleness guard (requires real TLE + Skyfield), `starlink_train_passes()` grouping (rise-time / azimuth logic), `_visible_window()` shadow detection
- **`predictor.py`** — `assemble_night()` integration (needs heavy mocking or `eph` + rasters)
- **`trip.py`** — `plan_trip()` ranking and dual-TTL caching
- **Rendering** — `render_report.py`, `render_trip.py`, `format_ctx.py` pure-function helpers
