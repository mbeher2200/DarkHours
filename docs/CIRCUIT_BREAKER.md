# Circuit breaker for 3rd-party provider calls

Implemented in `darkhours/circuit_breaker.py`. Skips calls to a provider that has
failed repeatedly, instead of waiting through its timeout, and surfaces the skip
through the existing "temporarily unavailable" UI messaging.

## Relationship to rate limiting

Reactive (stops calling a provider after it starts failing), not preventive. See
[`docs/RATE_LIMITING.md`](RATE_LIMITING.md) (`darkhours/rate_limiter.py`) for the
preventive half. The two modules never call into each other; call sites use both,
in a fixed order (`circuit_breaker.allow()` first, `rate_limiter.acquire()`
second).

## How it works

Two states per provider key, in-process (per warm Lambda container), thread-safe.

- **CLOSED** — calls proceed. `FAILURE_THRESHOLD` (3) consecutive failures →
  OPEN. Any success resets the count. Celestrak overrides to threshold 1.
- **OPEN** — calls are skipped instantly with `ProviderUnavailableError` (a
  `RuntimeError` subclass; carries `.provider` and `.retry_after_seconds`).

Recovery (OPEN → CLOSED) happens only in `on_success()`.

1. **Monitor-driven** — active only when `PYNIGHTSKY_PROVIDER_HEALTH_TABLE` is set
   AND the provider is one the synthetic ProviderHealth Lambda probes
   (`open_meteo`, `seven_timer`, `swpc`, `waqi`) AND its table entry is fresh
   (≤20 min). Fresh DOWN → block without probing. Fresh UP → grant one probe,
   rate-limited to one per 15s (`_PROBE_GUARD_SECONDS`).
2. **Self-timed** — everything else, and the fallback whenever no fresh monitor
   signal exists: after a cooldown (60s; Celestrak 300s), grant one probe.

Probe grants are atomic.

## Provider keys (per-host)

| Key | Call sites | Notes |
|---|---|---|
| `open_meteo` | weather.py forecast + past providers | api.open-meteo.com |
| `open_meteo_archive` | weather.py historical (ERA5) | archive-api.open-meteo.com — separate host, separate breaker |
| `open_meteo_air_quality` | weather.py `_fetch_air_quality` | own host; skip returns `[]` |
| `seven_timer` | weather.py SevenTimerProvider | |
| `celestrak` | tle_provider.py single + Starlink group | threshold 1 / cooldown 300s; Starlink 403 = "unchanged", not a failure |
| `waqi` | aqicn.py `_fetch_url` | parse-level failures don't count |
| `swpc` | aurora.py `_fetch_url` | covers both Kp products |
| `nominatim` | location.py geocode + suggest (geopy), darksky.py settlement (raw HTTP) | one key across both access mechanisms |
| `aws_location` | location.py aws geocode/suggest, darksky.py settlement | |
| `aws_georoutes` | darksky.py `_aws_drive_times` | one gate per bounded fan-out batch |

Skips preserve each site's existing degrade contract (suggest → `[]`, air quality
→ `[]`, `get_tle()` → stale cache, drive times → `None` fields, reverse geocode →
`None`).

## Timeouts

Urllib/geopy sites: 10–15s. AWS clients (`_location()`/`_georoutes()`):
`connect_timeout=2.0, read_timeout=5.0, retries={"total_max_attempts": 2, "mode":
"adaptive"}`. `tests/test_circuit_breaker.py::test_location_clients_have_bounded_latency`
pins these values. Note: botocore's `Config(retries={"max_attempts": N})` means N
*retries* (N+1 attempts); use `total_max_attempts`.

## Flags

- `PYNIGHTSKY_CIRCUIT_BREAKER_ENABLED` — kill switch, default enabled.
- `PYNIGHTSKY_CIRCUIT_BREAKER_<PROVIDER>_DISABLE` — per-key opt-out (key
  uppercased). Bookkeeping still runs while disabled.
- `PYNIGHTSKY_PROVIDER_HEALTH_TABLE` — ProviderHealth DynamoDB table name. Grants
  the API and worker Lambda roles a scoped `dynamodb:GetItem` on this table only.

## UI surfacing

Single-night `/night`: `NightReport.wx_error` → `ReportCard.tsx`. Calendar:
`NightSummary.wx_error` → `trip._to_dict/_from_dict` → `CalendarNight`
(types.ts) → `OutlookTelemetryRibbon.tsx`. `wx_error` nights cache at the 1h
weather TTL, not the 24h astro TTL. `/healthz` shows the last real observed
status; a skipped call writes no `provider_health.record()`.

## Known limits (accepted)

- **State is per-container.** A cold-start fan-out of N containers each pays its
  own failure streak before tripping locally; state resets on recycle.
  Cross-container shared state is deferred future work.
- **Trip detection is always local** even in monitor-driven mode; only recovery
  defers to the monitor.

## Tests

`tests/test_circuit_breaker.py` — state machine, monitor UP/DOWN/None semantics,
probe-guard thrash bound, probe atomicity, fail-fast on broken table reads, client
Config pins, cross-module integration (shared nominatim key, trip serialization).
Breaker-open short-circuit tests live in each provider's own test file.
`tests/conftest.py` resets breaker state around every test. All hermetic — no
network, no AWS.

## Celestrak 403 handling

Celestrak enforces one download per data update and answers 403 ("GP data has not
updated since your last successful download") where another service would send
304. `tle_provider` treats a 403 with a cached copy present as success and
re-writes the cache entry with a fresh `TLE_TTL`.

Two invariants, both covered by tests:

- **`TLE_TTL` (24h) must stay several warmer cycles (6h) wide.** The TTL is when
  DynamoDB deletes the row, not when the data goes stale.
- **403 with an empty cache must count as a failure.** Retrying cannot help, so it
  has to trip the breaker.

### Fetch timeouts by caller

`_FETCH_TIMEOUT` (5s) applies on the request path; `_WARM_FETCH_TIMEOUT` (30s) is
what the warmer passes. Both are per-socket-operation deadlines, not
total-transfer.
