# Runtime feature flags

Implemented in `darkhours/feature_flags.py`. Lets an operator turn one optional
feature off — or back on — without a code change or redeploy.

## Relationship to the circuit breaker and rate limiter

Feature flags are administrative (an operator decision), distinct from
`darkhours/circuit_breaker.py` (reactive) and `darkhours/rate_limiter.py`
(preventive). See [`docs/CIRCUIT_BREAKER.md`](CIRCUIT_BREAKER.md) and
[`docs/RATE_LIMITING.md`](RATE_LIMITING.md). All three share the same shape: an
optional DynamoDB table, a short in-process TTL cache, and a fail-open default.

## How it works

A single DynamoDB table (`PyNightSkyFeatureFlags` stack,
`cdk/feature_flags_stack.py`) with one item per flag: `{flag_id, enabled,
updated_at, updated_by, note}`. Deployed manually, on its own — never a
dependency of the CI-deployed `PyNightSkyLambda` stack.

`feature_flags.enabled(name, default=True)` read order:

1. `PYNIGHTSKY_FEATURE_FLAGS_ENABLED=0` — global kill switch. Every flag falls
   back to its code default immediately, no DynamoDB read.
2. `PYNIGHTSKY_FEATURE_<NAME>_DISABLE` — instant per-flag override, no AWS call.
3. No table configured (`PYNIGHTSKY_FEATURE_FLAGS_TABLE` unset) — returns the
   default. This is the local/CI/test state.
4. Otherwise, a `GetItem` read, cached in-process for ~30s. A flag with no item
   in the table also returns the default.

## What's flagged today

| Flag | Gates | File |
|---|---|---|
| `satellites` | Satellite pass / Starlink-train computation | `darkhours/predictor.py` |
| `aurora` | Aurora outlook computation | `darkhours/predictor.py` |
| `milky_way` | Milky Way arch summary | `darkhours/predictor.py` |
| `live_haze` | Live PM2.5/PM10 (AQICN) cross-check | `darkhours/predictor.py` |
| `routing` | Drive-time annotation (AWS Location) | `darkhours/darksky.py` |
| `nearby_search` | The whole `/nearby` job type | `apps/api/main.py` |
| `trip_builder` | The whole `/calendar` job type | `apps/api/main.py` |

Turning off a `/night`-report sub-feature (the first five) produces a report with
that field simply absent. Turning off a job-type flag returns `503` with
`Retry-After: 60` before any resolution work or job submission happens.

## Administrative — no write path from the public API

The API and worker Lambdas are granted `dynamodb:GetItem` **only** on this
table — no `PutItem`/`UpdateItem`/`DeleteItem`, enforced at the IAM policy
level. The only writer is an operator's own authenticated AWS credentials, used
locally.

## Operator workflow

Day-to-day control is a small set of local scripts in `scripts/local-flags/` —
**not checked into this repo** (see `.gitignore`).

Setup, once:
1. `cd cdk && cdk deploy PyNightSkyFeatureFlags` — note the
   `FeatureFlagsTableName` output.
2. Create `scripts/local-flags/` locally (`env.sh`, `_common.sh`,
   `_set_flag.sh`, `list_flags.sh`, `flag_status.sh`, `enable_flag.sh`,
   `disable_flag.sh`) and set `PYNIGHTSKY_FEATURE_FLAGS_TABLE` in `env.sh` to
   the real table name from step 1.
3. Add the table's name to `PyNightSkyLambda`'s deploy-time secrets as
   `PYNIGHTSKY_FEATURE_FLAGS_TABLE` and redeploy that stack once.

Day to day:
```bash
scripts/local-flags/list_flags.sh              # everything currently set
scripts/local-flags/flag_status.sh satellites   # one flag
scripts/local-flags/disable_flag.sh satellites "note for future you"
scripts/local-flags/enable_flag.sh satellites
```

Confirm a change took effect via `/healthz` (`"feature_flags"` in the response)
or by watching the field disappear from a live `/night` response — allow up to
the ~30s cache window.

## Rollout

New code that reads a flag ships in two deploys:

1. **Code only** — merge the gate with `PYNIGHTSKY_FEATURE_FLAGS_TABLE` still
   unset on `PyNightSkyLambda`. Every `feature_flags.enabled()` call returns its
   default unconditionally.
2. **Activation** — once step 1 has run cleanly, add the table env var in a
   small, isolated follow-up deploy.

## If something goes wrong

1. **One feature misbehaving** — `disable_flag.sh <name>` (or `enable_flag.sh`).
   No deploy.
2. **The flag system itself misbehaving** — set
   `PYNIGHTSKY_FEATURE_FLAGS_ENABLED=0`. Applying it without a full `cdk deploy`
   means an out-of-band `aws lambda update-function-configuration` on both
   functions; it causes template drift until the next real deploy reconciles it.
3. **A bug in the gating code itself** — standard `git revert` of the merge
   commit through the normal `main` → `deploy.yml` pipeline. Since rollout is
   staged, reverting just the activation deploy is usually enough.
