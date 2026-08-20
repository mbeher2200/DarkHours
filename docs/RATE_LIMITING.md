# Rate limiting for 3rd-party provider calls

Implemented in `darkhours/rate_limiter.py`. Caps outbound call rate/concurrency
per provider, independent of provider health — the preventive counterpart to
[`docs/CIRCUIT_BREAKER.md`](CIRCUIT_BREAKER.md)'s reactive protection.

## Relationship to the circuit breaker

The two modules never call into each other. Call sites compose them explicitly,
always in the same order: `circuit_breaker.allow(provider)` first,
`rate_limiter.acquire(provider)` second — wrapping only the network call,
strictly after the allow-check.

## How it works

- **`pace(provider)`** — min-interval serialization, one lock per provider.
  Blocks `__enter__` until `interval` seconds have passed since the last call
  *start* for this provider, across every caller/module sharing the key.
  `__exit__` is a no-op.
- **`limit(provider)`** — concurrency-cap serialization via a
  `threading.Semaphore`. A permit is held for the call's full duration and
  released on `__exit__` (success or exception).
- **`acquire(provider)`** — the call-site API: `with rate_limiter.acquire(provider):
  <do the call>`. Dispatches to `pace()` or `limit()` per the provider's
  configuration; a no-op for any unconfigured provider.
- **`reset()`** — test isolation, mirrors `circuit_breaker.reset()`: resets every
  pace provider's last-call clock and rebuilds every semaphore fresh.

All locks/semaphores are built once at import.

## Provider configuration

| Provider | Mechanism | Default | Env var override |
|---|---|---|---|
| `nominatim` | pace | 1.1 s | `PYNIGHTSKY_RATE_LIMIT_NOMINATIM_INTERVAL` |
| `celestrak` | pace | 2.0 s | `PYNIGHTSKY_RATE_LIMIT_CELESTRAK_INTERVAL` |
| `overpass` | pace | 1.0 s | `PYNIGHTSKY_RATE_LIMIT_OVERPASS_INTERVAL` |
| `open_meteo` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_OPEN_METEO_MAX_CONCURRENT` |
| `open_meteo_archive` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_OPEN_METEO_ARCHIVE_MAX_CONCURRENT` |
| `open_meteo_air_quality` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_OPEN_METEO_AIR_QUALITY_MAX_CONCURRENT` |
| `seven_timer` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_SEVEN_TIMER_MAX_CONCURRENT` |
| `waqi` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_WAQI_MAX_CONCURRENT` |

`swpc`, `aws_location`, `aws_georoutes` have no entry:
- `swpc` (`darkhours/aurora.py`) has its own global fetch lock.
- `aws_location`/`aws_georoutes` are capped via `_GEOCODE_MAX_WORKERS`
  (`darksky.py`) and are quota-managed paid services.

`acquire()` on an unconfigured key is a no-op — no special-casing needed at those
call sites.

## Flags

- `PYNIGHTSKY_RATE_LIMIT_ENABLED` — kill switch, default enabled.
- `PYNIGHTSKY_RATE_LIMIT_<PROVIDER>_DISABLE` — per-key opt-out (key uppercased).
- `PYNIGHTSKY_RATE_LIMIT_<PROVIDER>_INTERVAL` — override a `pace()`-type
  provider's min interval, in seconds.
- `PYNIGHTSKY_RATE_LIMIT_<PROVIDER>_MAX_CONCURRENT` — override a `limit()`-type
  provider's concurrency cap.

## The shared Nominatim key

`location.py`'s `_geocode_via_nominatim`/`_suggest_via_nominatim` (geopy) and
`darksky.py`'s `_nominatim_settlement` (raw HTTP) both call
`rate_limiter.acquire("nominatim")` — one shared pacer, one shared clock, for
every call this process makes to nominatim.openstreetmap.org. Neither module
imports the other for this purpose; coordination happens through
`rate_limiter.py`'s module-global state, the same way both modules share one
`circuit_breaker.py` `"nominatim"` breaker key.

## TLE single-flight dedup

`tle_provider.py`'s `_lock_for(key)` (mirroring `weather.py`/`aqicn.py`'s
`lock_for` idiom, keyed by the same string already used as the cache key) makes
`get_tle()` and `get_starlink_train_tles()` single-flight per resource: a thread
that waits for the lock re-checks the cache first and finds it warm if another
thread already fetched it — so N overlapping requests for one resource cost
exactly 1 real Celestrak fetch, not N.

## Known limits (accepted)

Same accepted scope as the circuit breaker's own "Known limits" — see
[`docs/CIRCUIT_BREAKER.md`](CIRCUIT_BREAKER.md#known-limits-accepted): state is
per-container/in-process only, no cross-container budget.

## Tests

`tests/test_rate_limiter.py` — `pace()`/`limit()` mechanics (including
thread-safe atomicity via a barrier, and permit release on exception), kill
switch/per-provider disable, `reset()` isolation, env-var parsing,
provider-coverage assertion, and the cross-module Nominatim-sharing regression
test. `tests/conftest.py` resets rate-limiter state around every test. All
hermetic — no network, no AWS, no real sleeping.
