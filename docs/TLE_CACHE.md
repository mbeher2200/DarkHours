# TLE Cache

Satellite TLE acquisition (`darkhours/tle_provider.py`). One rule: **the request
path reads the cache and never fetches.**

## API split

| Function | Network | Called by |
|---|---|---|
| `cached_tle(norad_id)` | no | `predictor.assemble_night` |
| `cached_starlink_trains()` | no | `predictor.assemble_night` |
| `refresh_tle(norad_id, timeout)` | yes | `warm_cache` |
| `refresh_starlink_trains(timeout)` | yes | `warm_cache` |
| `warm_cache(timeout, force)` | yes | `apps/warmer/handler.py` (`force=True`), `darkhours.py --satellites` (`force=False`) |
| `get_tle(norad_id, timeout)` | on miss | CLI only |
| `get_starlink_train_tles(timeout)` | on miss | CLI only |

## Cache keys

| Key | Value | Bytes |
|---|---|---|
| `tle\|25544` / `tle\|20580` / `tle\|48274` | raw 3-line TLE text | ~172 |
| `tle\|starlink\|trains` | `list[[name, line1, line2]]`, filtered, capped at `_STARLINK_MAX_TRAINS` (400) | 2 – 66,400 |

TTL: `TLE_TTL` = 24 h retention. Warm schedule: 6 h. `warm_cache(force=True)`
re-`set`s every key on every run, so `expires` stays within one interval of 24 h.

The Starlink group response is ~1.8 MB and is never cached. `DynamoCache.set`
refuses any item over `_MAX_ITEM_BYTES` (380,000) and returns `False`.

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
| `TleWarmFailure` | — | keys not verified this run; emitted as 0 on a clean run |

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
