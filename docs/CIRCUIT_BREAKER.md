# Circuit breaker for 3rd-party provider calls

Implemented in `darkhours/circuit_breaker.py` (PR #136). Skips calls to a provider that
has just failed repeatedly, instead of making every user wait through its timeout, and
surfaces the skip to the UI through the existing "temporarily unavailable" messaging.

## Why it exists

Before this, nothing ever decided *not* to call a provider: `darkhours/provider_health.py`
passively recorded outcomes for `/healthz`, but every request always attempted the live
call. A down provider cost each request its full timeout (10–15s for the urllib
providers; **minutes** for the AWS clients, which ran botocore's 60s/60s defaults × 5
adaptive retries) before the app fell back or degraded.

## How it works

Two states per provider key, in-process (per warm Lambda container), thread-safe.

- **CLOSED** — calls proceed. `FAILURE_THRESHOLD` (3) *consecutive* failures → OPEN.
  Any success resets the count. Celestrak overrides to threshold 1 (see below).
- **OPEN** — calls are skipped instantly with `ProviderUnavailableError` (a
  `RuntimeError` subclass, so every existing `except RuntimeError` handles a skipped
  call exactly like a failed one; carries `.provider` and `.retry_after_seconds`).

**Recovery** (OPEN → CLOSED) happens only in `on_success()` — a real call must succeed.
Which calls get attempted while OPEN depends on the mode, chosen per call at runtime:

1. **Monitor-driven** — only when `PYNIGHTSKY_PROVIDER_HEALTH_TABLE` is set AND the
   provider is one of the four the synthetic ProviderHealth Lambda probes
   (`open_meteo`, `seven_timer`, `swpc`, `waqi`) AND its table entry is fresh (≤20 min).
   Fresh DOWN → block, no user request spent probing. Fresh UP → grant one probe,
   rate-limited to one per 15s (`_PROBE_GUARD_SECONDS`) so a monitor false-UP can't
   thrash the breaker open/closed while the provider is really down for us.
2. **Self-timed** — everything else, and the automatic fallback whenever no fresh
   monitor signal exists (env var unset, read error, stale/missing entry): after the
   cooldown (60s; Celestrak 300s), grant one probe. Probe failure re-arms a fresh
   cooldown; the block→probe cycle repeats for as long as the outage lasts.

Probe grants are atomic — granting re-arms the clock inside the state lock, so
concurrent threads (predictor's I/O fan-out) can't all probe at once.

## Provider keys (per-host, because reachability is a host property)

| Key | Call sites | Notes |
|---|---|---|
| `open_meteo` | weather.py forecast + past providers | api.open-meteo.com |
| `open_meteo_archive` | weather.py historical (ERA5) | archive-api.open-meteo.com — different host, fails independently (see OpenMeteoPastProvider docstring) |
| `open_meteo_air_quality` | weather.py `_fetch_air_quality` | own host; skip returns `[]` (never a hard dependency) |
| `seven_timer` | weather.py SevenTimerProvider | |
| `celestrak` | tle_provider.py single + Starlink group | threshold 1 / cooldown 300s: global TLE cache concentrates retries at expiry, and Celestrak punishes exactly that; stale-cache fallback makes patience free. Starlink 403 = "unchanged", **not** a failure |
| `waqi` | aqicn.py `_fetch_url` | parse-level failures (bad JSON, non-ok status) do **not** count — provider was reached |
| `swpc` | aurora.py `_fetch_url` | covers both Kp products |
| `nominatim` | location.py geocode + suggest (geopy), darksky.py settlement (raw HTTP) | one key across both access mechanisms — same upstream |
| `aws_location` | location.py aws geocode/suggest, darksky.py settlement | |
| `aws_georoutes` | darksky.py `_aws_drive_times` | one gate per bounded fan-out batch |

Skips preserve each site's existing degrade contract (suggest → `[]`, air quality →
`[]`, `get_tle()` → stale cache, drive times → `None` fields, reverse geocode → `None`).

## Detection-latency budget

Time-to-trip = threshold × worst-case single-call latency, so every gated call must
fail fast. The urllib/geopy sites were already bounded (10–15s). The AWS clients were
not: `_location()`/`_georoutes()` now run `connect_timeout=2.0, read_timeout=5.0,
retries={"total_max_attempts": 2, "mode": "adaptive"}` (~15s worst case, ~45s to trip;
adaptive kept so `find_nearby`'s fan-out still absorbs ThrottlingException).
`tests/test_circuit_breaker.py::test_location_clients_have_bounded_latency` pins these
values — a future "bump the retries" edit fails a test instead of silently making the
breaker minutes-slow. Note: botocore's `Config(retries={"max_attempts": N})` means N
*retries* (N+1 attempts); use `total_max_attempts`.

## Flags

Read once at import (same idiom as `PYNIGHTSKY_NO_CACHE`):

- `PYNIGHTSKY_CIRCUIT_BREAKER_ENABLED` — kill switch, **default enabled**.
- `PYNIGHTSKY_CIRCUIT_BREAKER_<PROVIDER>_DISABLE` — per-key opt-out (key uppercased,
  e.g. `..._OPEN_METEO_ARCHIVE_DISABLE`). Bookkeeping still runs while disabled.
- `PYNIGHTSKY_PROVIDER_HEALTH_TABLE` — ProviderHealth DynamoDB table name.
  **Currently unset everywhere** — see "Optional follow-up" below.

## UI surfacing

Single-night `/night`: already worked (`NightReport.wx_error` → `ReportCard.tsx`).
This change closed the calendar gap: `NightSummary` now carries `wx_error` through
`trip._to_dict/_from_dict` → `CalendarNight` (types.ts) → `OutlookTelemetryRibbon.tsx`
("weather providers are temporarily unavailable"). `wx_error` nights cache at the 1h
weather TTL, not the 24h astro TTL, so an outage message ages out within the hour.
`/healthz` is unchanged: a skipped call writes no `provider_health.record()`, so it
keeps showing the last *real* observed status.

## Known limits (accepted)

- **State is per-container.** A cold-start fan-out of N containers each pays its own
  failure streak before tripping locally; state resets on recycle. Cross-container
  shared state is the deferred storm-throttling use case (which would also add call
  volume/latency metrics — the `allow`/`on_success`/`on_failure` seam is where both
  hook in).
- **Trip detection is always local** even in monitor-driven mode; only *recovery*
  defers to the monitor.

## Optional follow-up: wire monitor-driven recovery (NOT required)

The feature is complete without this. Wiring it upgrades recovery for the four
monitor-tracked providers: recovery noticed on the monitor's 5-min schedule with zero
user requests spent probing, consistent across all containers, and the basis for
future flap detection. Steps:

1. IAM: grant `dynamodb:GetItem` (only — single-key lookups) on the ProviderHealth
   table to the API and worker Lambda roles.
2. Env: set `PYNIGHTSKY_PROVIDER_HEALTH_TABLE` on both Lambdas via CDK
   context/parameter — **not** a CloudFormation export/import, which would couple the
   independently-deployed `PyNightSkyProviderHealth` (manual) and `PyNightSkyLambda`
   (CI) stacks. Never hardcode the table name (public repo).
3. Deploy order: ProviderHealth stack must exist first.
4. **Post-deploy, verify a real read succeeds** (e.g. trip a breaker in a test
   invoke and confirm monitor-driven behavior, or check debug logs). The read is
   deliberately fail-*safe* (1s timeouts, 1 attempt, broad except → self-timed
   fallback), which means a broken grant is silent — it must be checked for, it will
   never announce itself.
5. Flap detection remains a further step even after wiring: the monitor's table only
   stores latest status (overwritten each run). It needs either a rolling history in
   the table or queries against the `ProviderUp` EMF metric (a real time series).

## Tests

`tests/test_circuit_breaker.py` — state machine, monitor UP/DOWN/None semantics,
probe-guard thrash bound, probe atomicity (16 threads → one grant), fail-fast on broken
table reads, client Config pins, cross-module integration (shared nominatim key, trip
serialization). Breaker-open short-circuit tests live in each provider's own test file.
`tests/conftest.py` resets breaker state around every test (state is module-global;
celestrak trips on a single failure). All hermetic — no network, no AWS.
