# Rate limiting for 3rd-party provider calls

Implemented in `darkhours/rate_limiter.py`. Caps how fast/how many outbound calls
DarkHours itself makes to a third-party provider, independent of whether that
provider is currently healthy — the preventive counterpart to
[`docs/CIRCUIT_BREAKER.md`](CIRCUIT_BREAKER.md)'s reactive protection.

## Relationship to the circuit breaker

The circuit breaker is **reactive**: it stops calling a provider only after
`FAILURE_THRESHOLD` consecutive failures. It does nothing to cap the *rate* of
calls while a provider is healthy. The rate limiter is **preventive**: it caps
DarkHours' own call rate/concurrency to a provider regardless of health, so the
app's own fan-out (`trip.plan_trip()`'s up to 20 concurrent nights x
`predictor.assemble_night()`'s up to 9 concurrent provider calls per night) can
never look like a burst/DoS to a healthy public provider.

The two modules never call into each other. Call sites compose them explicitly,
always in the same order: `circuit_breaker.allow(provider)` first,
`rate_limiter.acquire(provider)` second — wrapping only the network call, strictly
after the allow-check — so a breaker-open skip never pays a pacing cost.

## Why it exists

Before this, nothing capped DarkHours' own outbound rate to a healthy provider.
Two concrete gaps existed:

1. `darkhours/location.py`'s Nominatim calls (forward geocode + typeahead suggest)
   had zero pacing, and didn't coordinate with `darkhours/darksky.py`'s own
   sleep-based Nominatim throttle — two independent, uncoordinated rate states
   hitting the same host (nominatim.openstreetmap.org, published ~1 req/sec usage
   policy).
2. `darkhours/tle_provider.py` had no single-flight lock. Satellites are a
   single-night feature — `fetch_satellites` is only ever `True` for the
   `/night?satellites=true` endpoint or the CLI's `--satellites` flag; it is
   never passed on the trip/calendar multi-night fan-out path (`trip.py` never
   sets it), so a trip/calendar build never touches Celestrak at all. Within one
   `assemble_night()` call, predictor.py's 9-worker pool fetches up to 4 distinct
   Celestrak resources concurrently (3 individual NORAD ids + 1 Starlink group) —
   pacing alone already bounds that to ~6-8s regardless of dedup, since they're 4
   different resources. The dedup lock's real value is a different case: N
   *overlapping* single-night satellite requests for the *same* resource
   (multiple browser tabs, a script issuing several `--satellites` CLI runs, or
   several users' `/night?satellites=true` calls landing on one warm Lambda
   container within the same window) would otherwise each independently miss the
   cache and fire their own redundant Celestrak fetch. In the deployed AWS
   backend this is further reduced by `PyNightSkyWarmer` (`apps/warmer/handler.py`,
   `cdk/warmer_stack.py`), which runs on a 6-hour EventBridge schedule matching
   `TLE_TTL` and proactively refreshes all 4 resources into the shared global
   DynamoDB cache, so the request path is almost always a warm hit there. The
   **local/CLI backend** has no warmer — TLE cache is a local file cache each CLI
   process refills itself every 6h — so this is primarily a local/CLI concern,
   plus cheap defense-in-depth on AWS for warmer-downtime windows. Don't overstate
   the production risk here.

## How it works

Two mechanisms, generalizing patterns that already existed ad hoc in the codebase
(sleep-based min-interval pacing for Nominatim/Overpass in `darksky.py`, and
`darksky.py`'s `_GEOCODE_MAX_WORKERS` concurrency cap for AWS Location fan-out) —
call sites use one uniform API and never need to know which mechanism a given
provider uses:

- **`pace(provider)`** — min-interval serialization, one lock per provider (never
  a single lock shared across providers — a provider that has to sleep must not
  block an unrelated provider's pacing decision). Blocks `__enter__` until
  `interval` seconds have passed since the last call *start* for this provider,
  across every caller/module sharing the key. `__exit__` is a no-op: the gate is
  on when a call is allowed to start, not held for the call's duration.
- **`limit(provider)`** — concurrency-cap serialization via a `threading.Semaphore`.
  A permit is held for the call's full duration and released on `__exit__`
  (success or exception).
- **`acquire(provider)`** — the one call-site API: `with rate_limiter.acquire(provider):
  <do the call>`. Dispatches to `pace()` or `limit()` per the provider's
  configuration; a no-op for any provider with no entry.
- **`reset()`** — test isolation, mirrors `circuit_breaker.reset()`: resets every
  pace provider's last-call clock and rebuilds every semaphore fresh.

All locks/semaphores are built once at import — the provider set is a fixed set of
constants, not dynamic, so (unlike `circuit_breaker.py`'s DynamoDB table handle)
there's no need for lazy double-checked-locking init here.

## Provider configuration

| Provider | Mechanism | Default | Env var override | Why |
|---|---|---|---|---|
| `nominatim` | pace | 1.1 s | `PYNIGHTSKY_RATE_LIMIT_NOMINATIM_INTERVAL` | Matches OSM's ~1 req/sec usage policy — same value `darksky.py` already used, now shared with `location.py`. |
| `celestrak` | pace | 2.0 s | `PYNIGHTSKY_RATE_LIMIT_CELESTRAK_INTERVAL` | Pairs with `circuit_breaker.py`'s own extra-cautious Celestrak override (threshold 1, 300s cooldown) — same "be extra careful with this one" posture. |
| `overpass` | pace | 1.0 s | `PYNIGHTSKY_RATE_LIMIT_OVERPASS_INTERVAL` | Matches overpass-api.de's existing self-throttle value from `darksky.py`. Note: overpass has no circuit-breaker gate at all (`"overpass"` isn't in `circuit_breaker._ALL_PROVIDERS`) — out of scope here, this module just preserves that as-is. |
| `open_meteo` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_OPEN_METEO_MAX_CONCURRENT` | Shared by the forecast and recent-past providers (same host, same breaker key). No documented strict per-IP throttle, but still capped so a 20-way trip fan-out never bursts all at once. |
| `open_meteo_archive` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_OPEN_METEO_ARCHIVE_MAX_CONCURRENT` | Separate host (archive-api.open-meteo.com), separate breaker key already — same posture. |
| `open_meteo_air_quality` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_OPEN_METEO_AIR_QUALITY_MAX_CONCURRENT` | Own subdomain, own breaker key, 1:1 per-night cardinality with `open_meteo`. |
| `seven_timer` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_SEVEN_TIMER_MAX_CONCURRENT` | Free public API, no documented budget in this codebase — same conservative posture. |
| `waqi` | limit | 10 concurrent | `PYNIGHTSKY_RATE_LIMIT_WAQI_MAX_CONCURRENT` | Free-tier WAQI tokens carry an account-level budget; capping burst concurrency protects the shared `AQICN_TOKEN`. |

`swpc`, `aws_location`, `aws_georoutes` deliberately have **no** entry — confirmed
non-gaps, already protected by other means:
- `swpc` (`darkhours/aurora.py`) already has a single **global** fetch lock (SWPC
  products are location-independent) that collapses concurrent cache misses to ~1
  real call.
- `aws_location`/`aws_georoutes` are already capped via `_GEOCODE_MAX_WORKERS`
  (`darksky.py`), and AWS is a quota-managed paid service, not a public-DoS
  concern.

`acquire()` on an unconfigured key is a pure no-op — no special-casing needed at
those call sites.

## Flags

Read once at import, same idiom as `circuit_breaker.py`:

- `PYNIGHTSKY_RATE_LIMIT_ENABLED` — kill switch, **default enabled**.
- `PYNIGHTSKY_RATE_LIMIT_<PROVIDER>_DISABLE` — per-key opt-out (key uppercased).
- `PYNIGHTSKY_RATE_LIMIT_<PROVIDER>_INTERVAL` — override a `pace()`-type provider's
  min interval, in seconds.
- `PYNIGHTSKY_RATE_LIMIT_<PROVIDER>_MAX_CONCURRENT` — override a `limit()`-type
  provider's concurrency cap.

No CDK wiring — same pattern as the existing `PYNIGHTSKY_GEOCODE_WORKERS`
concurrency-cap precedent, a pure runtime env-var override with a safe baked-in
default.

## The shared Nominatim key

`location.py`'s `_geocode_via_nominatim`/`_suggest_via_nominatim` (geopy) and
`darksky.py`'s `_nominatim_settlement` (raw HTTP) both call
`rate_limiter.acquire("nominatim")` — one shared pacer, one shared clock, for
every call this process makes to nominatim.openstreetmap.org, regardless of
which module or HTTP mechanism initiates it. Neither module imports the other for
this purpose; the coordination happens entirely through `rate_limiter.py`'s
module-global state, the same way both modules already share one
`circuit_breaker.py` `"nominatim"` breaker key.

## TLE single-flight dedup

Satellites are a single-night feature: `fetch_satellites` is only ever `True` for
`/night?satellites=true` or the CLI's `--satellites` flag, never on the
trip/calendar multi-night fan-out path (`trip.py` never sets it — a trip/calendar
build never touches Celestrak). Within one `assemble_night()` call,
predictor.py's 9-worker pool fetches up to 4 *distinct* Celestrak resources
concurrently (3 individual NORAD ids + 1 Starlink group) — `pace("celestrak")`
alone already bounds that case to ~6-8s, since dedup doesn't help when every
concurrent call is for a different resource.

`_lock_for(key)`'s real value is a different case: N *overlapping* single-night
satellite requests for the *same* resource (several browser tabs, a script
running `--satellites` in a loop, or several users' `/night?satellites=true`
calls landing on one warm Lambda container within the same window). Without a
lock, `tle_provider.py` has no shared/global cache the way `weather.py`'s and
`aqicn.py`'s providers do (TLE cache is per-resource, keyed by NORAD id or the
Starlink group) — every such overlapping caller would independently miss the
cache and fire its own redundant Celestrak fetch for the same resource.
`_lock_for(key)` (mirroring `weather.py`/`aqicn.py`'s `lock_for` idiom, keyed by
the same string already used as the cache key) makes `get_tle()` and
`get_starlink_train_tles()` single-flight per resource: a thread that waits for
the lock re-checks the cache first and finds it warm if another thread already
fetched it — so N overlapping requests for one resource cost exactly 1 real
Celestrak fetch, not N.

## Known limits (accepted)

Same accepted scope as the circuit breaker's own "Known limits" — see
[`docs/CIRCUIT_BREAKER.md`](CIRCUIT_BREAKER.md#known-limits-accepted):

- **State is per-container/in-process only.** A cold-start fan-out of many
  concurrent Lambda containers each gets its own independent pacer state — no
  cross-container budget. This is the same "deferred storm-throttling work" the
  circuit breaker doc already calls out as future scope, not something this
  module attempts.

## Tests

`tests/test_rate_limiter.py` — `pace()`/`limit()` mechanics (including thread-safe
atomicity via a barrier, and permit release on exception), kill switch/per-provider
disable, `reset()` isolation, env-var parsing, provider-coverage assertion, and the
cross-module Nominatim-sharing regression test (the concrete proof that
`location.py` and `darksky.py` now pace through one shared clock). `tests/conftest.py`
resets rate-limiter state around every test, mirroring the circuit breaker's own
fixture. All hermetic — no network, no AWS, no real sleeping (`pace()` tests use a
fake clock; `limit()` tests use small real sleeps since no interval math is
involved there).
