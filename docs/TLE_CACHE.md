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
| mean motion ≥ | `_STARLINK_TRAIN_MM_MIN` | 15.2 rev/day | OMM `MEAN_MOTION` |
| launch within | `_STARLINK_RECENT_DAYS` | 21 days | SATCAT `LAUNCH_DATE` |

Measured against the 2026-08-26 feed (10,989 objects), the mean-motion gate at 15.2
matches 9,603 — 89.4% of the constellation. Populated shells sit at 15.28, 15.32,
15.33 and 15.34 rev/day (464–482 km), all above the threshold; 8,165 objects
spanning launch years 2019–2026 fall in 15.25–15.35.

Mean motion does not separate raising from decaying: a satellite being lowered for
re-entry gains mean motion the same way a raising one has not yet lost it. Maximum
among pre-2026 launches is 16.49, above the 16.10 maximum of the newest launch in
the feed. `_STARLINK_RECENT_DAYS` is the only filter with separating power.

Launch dates are **not** derivable from a TLE. The International Designator
(line 1, cols 10-17) is `YYNNNPPP` where `NNN` is the launch's sequence number
within its year, not a day of the year: `1998-067A` is the ISS, launched
1998-11-20. `_cospar_designator` returns `"YYYY-NNN"`; the date comes from SATCAT.

An empty launch-date map fails the filter closed (0 trains, ERROR logged,
`service=celestrak`) and skips the group fetch entirely. A body that is not OMM CSV
raises `ValueError` rather than filtering to an empty list.

## Upstream sources

| Data | Endpoint | Update policy |
|---|---|---|
| tracked TLEs | `gp.php?CATNR=<id>&FORMAT=TLE` | 403 = unchanged since last download |
| Starlink group | `gp.php?GROUP=starlink&FORMAT=CSV` | 2 h; 403 = unchanged |
| launch dates | `satcat/records.php?GROUP=starlink&FORMAT=CSV` | no download policy |

Objects catalogued from 2026-07-11 carry 6-digit catalog numbers. Celestrak does
not render those in `FORMAT=TLE`; GP data for them is served in JSON/XML/CSV/KVN
only. A `FORMAT=TLE` request returns the constellation truncated at the last
5-digit launch.

Measured 2026-08-26, same endpoint, same day:

| Request | Objects | Newest launch | 6-digit |
|---|---|---|---|
| `GROUP=starlink&FORMAT=TLE` | 10,738 | `2026-159` | 0 |
| `GROUP=starlink&FORMAT=CSV` | 10,989 | `2026-190` | 253 |

The 2026-08-23 observations that `GROUP=last-30-days` was empty and `INTDES=` /
`CATNR=` returned `No GP data found` were all `FORMAT=TLE` requests.
`CATNR=100001&FORMAT=CSV` returns the object.

Surviving rows are re-encoded as TLE line pairs on the way into the cache; catalog
numbers ≥ 100000 become Alpha-5 (`100001` → `A0001`), which sgp4 and skyfield read
back as the original integer. Nothing on the train path reads the catalog number.

Objects with SpaceX provisional ids (9-digit, e.g. `799501431`) are not in GP and
have no SATCAT row, so they carry no launch date and are filtered out. They appear
once catalogued.

`TleWarmItems` on `Key=starlink` is where a truncated feed shows up.

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
