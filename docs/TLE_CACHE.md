# TLE Cache

Satellite TLE acquisition (`darkhours/tle_provider.py`). One rule: **the request
path reads the cache and never fetches.**

## API split

| Function | Network | Called by |
|---|---|---|
| `cached_tle(norad_id)` | no | `predictor.assemble_night` |
| `cached_starlink_trains()` | no | `predictor.assemble_night` |
| `refresh_tle(norad_id, timeout)` | yes | `warm_cache` |
| `refresh_starlink_trains(timeout, launch_dates)` | yes | `warm_cache` |
| `_fetch_starlink_launch_dates(timeout)` | yes | `warm_cache`, `refresh_starlink_trains` |
| `warm_cache(timeout, force)` | yes | `apps/warmer/handler.py` (`force=True`), `darkhours.py --satellites` (`force=False`) |
| `get_tle(norad_id, timeout)` | on miss | CLI only |
| `get_starlink_train_tles(timeout)` | on miss | CLI only |

## Cache keys

| Key | Value | Bytes |
|---|---|---|
| `tle\|25544` / `tle\|20580` / `tle\|48274` | raw 3-line TLE text | ~172 |
| `tle\|starlink\|trains` | `list[[name, line1, line2, launch_date]]`, filtered, capped at `_STARLINK_MAX_TRAINS` (400) | 2 – 78,400 |
| `tle\|starlink\|launch_dates` | `{COSPAR designator: "YYYY-MM-DD"}`, launches within `_STARLINK_LAUNCH_DATE_WINDOW_DAYS` (90) | ~30 – 1,500 |

Three-element `tle\|starlink\|trains` rows are read as a train with no launch date.

TTL: `TLE_TTL` = 24 h retention. Warm schedule: 6 h. `warm_cache(force=True)`
re-`set`s every key on every run, so `expires` stays within one interval of 24 h.

The Starlink group response is ~1.8 MB and is never cached. `DynamoCache.set`
refuses any item over `_MAX_ITEM_BYTES` (380,000) and returns `False`.

## Starlink train filter

`_filter_train_tles` keeps a satellite only if both hold:

| Gate | Constant | Value | Source |
|---|---|---|---|
| mean motion ≥ | `_STARLINK_TRAIN_MM_MIN` | 15.2 rev/day | TLE line 2, cols 52-63 |
| launch within | `_STARLINK_RECENT_DAYS` | 21 days | SATCAT `LAUNCH_DATE` |

Every operational Starlink shell is at or below ~15.12 rev/day (530–560 km).

Launch dates are **not** derivable from a TLE. The International Designator
(line 1, cols 10-17) is `YYNNNPPP` where `NNN` is the launch's sequence number
within its year, not a day of the year: `1998-067A` is the ISS, launched
1998-11-20. `_cospar_designator` returns `"YYYY-NNN"`; the date comes from SATCAT.

An empty launch-date map fails the filter closed (0 trains, ERROR logged,
`service=celestrak`) and skips the group fetch entirely.

## Upstream sources

| Data | Endpoint | Update policy |
|---|---|---|
| tracked TLEs | `gp.php?CATNR=<id>&FORMAT=TLE` | 403 = unchanged since last download |
| Starlink group | `gp.php?GROUP=starlink&FORMAT=TLE` | 2 h; 403 = unchanged |
| launch dates | `satcat/records.php?GROUP=starlink&FORMAT=CSV` | no download policy |

GP element sets and SATCAT entries do not appear together. Observed 2026-08-23:
SATCAT listed Starlink launches through 2026-08-12 (designators to `2026-184`,
catalog numbers ≥ 100000), while GP data existed only through `2026-159`
(2026-07-11). `GROUP=last-30-days` was empty, and `INTDES=`/`CATNR=` queries for
the newer launches returned `No GP data found`. No trains are reportable while
the newest GP launch is older than `_STARLINK_RECENT_DAYS`; `TleWarmItems` on
`Key=starlink` is where that shows up.

## Degradation (request path)

| Cache state | Result | Log |
|---|---|---|
| fresh | `stale=False` | DEBUG |
| expired only | `stale=True`, data served | WARNING |
| absent | `lines=None` / `[]`, render shows unavailable | ERROR, `service=celestrak` |

ERROR lines feed `UpstreamErrorAlarm` (`PyNightSkyLambda`).

## Metrics and alarms

Namespace `PyNightSky/Tle`, EMF from the warmer.

| Metric | Dimensions | Meaning |
|---|---|---|
| `TleWarmSuccess` | `Key` | 1 = value read back out of the cache after the write |
| `TleCachedBytes` | `Key` | serialized size of the verified value |
| `TleWarmItems` | `Key` | entries in the verified value (trains, launches) |
| `TleWarmFailure` | — | keys not verified this run; emitted as 0 on a clean run |

`TleWarmSuccess=1` with `TleWarmItems=0` on `Key=starlink` is a verified write of an
empty train list. Normal between launches; sustained over weeks it is not.

`PyNightSkyWarmer` alarms: `TleWarmFailureAlarm`, `TleWarmerErrorsAlarm`,
`TleWarmerDeadMansSwitchAlarm`. See [`OBSERVABILITY.md`](OBSERVABILITY.md).

## Operations

`PyNightSkyWarmer` is deployed manually (`deploy.yml` targets `PyNightSkyLambda` only):

```
cdk deploy PyNightSkyWarmer
aws sns subscribe --topic-arn <AlarmTopicArn output> --protocol email \
  --notification-endpoint <you> --region us-east-1
```

Fill a cold cache immediately:

```
aws lambda invoke --function-name <WarmerFunctionName output> /dev/stdout
```

Inspect the rows (resolve the table name from the deployed Lambda env, never hardcode):

```
aws dynamodb get-item --table-name "$PYNIGHTSKY_CACHE_TABLE" \
  --key '{"cache_key":{"S":"tle|starlink|trains"}}' \
  --projection-expression "cache_key, expires" --region us-east-1
```

`expires` more than 6 h from now-plus-24 h means the warmer is not extending the TTL.
