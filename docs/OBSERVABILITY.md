# Observability — Dashboard, Alarms, Logs, Traces

Record of the 2026-07-14 observability audit and the M8 change that followed it. Covers what's
monitored, where it notifies, and known gaps left open on purpose.

No AWS account id, ARN, resource name, or notification address appears in this file — this is a
public repo (see CLAUDE.md). Any command below that needs one discovers it from the environment
or a CDK stack output at run time.

## Dashboard

`cloudwatch.Dashboard` **PyNightSky-Overview**, defined in `cdk/lambda_api_stack.py` (M8 block).
Console link: `aws cloudformation describe-stacks --stack-name PyNightSkyLambda
--query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" --output text`.

Rows, top to bottom:

| Row | Covers |
|---|---|
| Alarms | Status of every alarm in the table below |
| API Lambda | Invocations/errors/throttles; duration p50/p99 |
| Worker Lambda | Same, for the SQS-triggered worker |
| DynamoDB | Consumed read/write capacity; throttled requests; user errors |
| Queues | Jobs queue depth + oldest-message age; DLQ depth |
| CloudFront | Requests, 4xx/5xx rate, origin latency, cache hit rate |
| WAF | Blocked requests, per rule (IP reputation, known-bad-inputs, 3 rate limits) |
| Application | `PyNightSky/UpstreamErrors` (log-derived: AWS Location/Celestrak/7Timer failures) |
| X-Ray | Text widget linking to the Service Map console (trace data isn't a native widget type) |

## Alarms

All alarms notify via **SNS → email**. Two separate topics exist (one per stack); find the ARN
for either with `aws cloudformation describe-stacks --stack-name <stack> --query
"Stacks[0].Outputs[?OutputKey=='AlarmTopicArn'].OutputValue"`. Subscribe with:

```
aws sns subscribe --topic-arn <arn> --protocol email --notification-endpoint <you> \
  --region us-east-1
```

then confirm via the emailed link — `aws sns list-subscriptions-by-topic --topic-arn <arn>`
should show `Confirmed`, not `PendingConfirmation`.

**`PyNightSkyLambda` stack** (`AlarmTopic`, in `lambda_api_stack.py`):

| Alarm | Condition |
|---|---|
| `UpstreamErrorAlarm` (M7.3) | ≥3 ERROR-level log lines (AWS Location / Celestrak / 7Timer) in 5 min |
| `ApiErrorsAlarm` | ≥3 API Lambda errors in 5 min |
| `WorkerErrorsAlarm` | ≥2 Worker Lambda errors in 5 min |
| `DlqNotEmptyAlarm` | ≥1 message visible in the jobs dead-letter queue — a job failed 3 retries |
| `CloudFront5xxErrorRateAlarm` | 5xx rate ≥20% over 2 consecutive 5-min periods |

`CloudFront5xxErrorRateAlarm`'s 20% threshold is intentionally loose — at this app's traffic
volume a handful of failed requests can swing a percentage-based rate metric sharply. Tune it
down once a week or two of real baseline is visible on the dashboard (same
profile-before-optimizing discipline as `docs/PERF_FINDNEARBY.md`).

**`PyNightSkyProviderHealth` stack** (its own `AlarmTopic`, in `cdk/provider_health_stack.py`):

| Alarm | Condition |
|---|---|
| `open-meteoDownAlarm` / `7timerDownAlarm` | Provider reported DOWN for 3 consecutive 5-min checks (15 min) |
| `DynamoWriteFailureAlarm` | A health-check write to DynamoDB failed |
| `DeadMansSwitchAlarm` | The monitor itself hasn't run in 6 minutes |

This stack was written well before it was deployed — `cdk/app.py` always instantiated it, but it
was missing from `aws cloudformation list-stacks` until this round. It's deployed the same
one-time-manual way as `PyNightSkyWarmer`/`PyNightSkyCicd` (see CLAUDE.md "Ship flow"): not part
of the CI `deploy.yml` pipeline, so redeploy it manually if its code ever changes.

**Test the notification path without waiting for a real breach:**

```
aws cloudwatch set-alarm-state --alarm-name <deployed alarm name> --state-value ALARM \
  --state-reason "manual test" --region us-east-1
```
Confirm the email arrives, then let it self-clear on the next real evaluation.

## Logs

| Log group | Retention | Contents |
|---|---|---|
| `/pynightsky/api` | 14 days | Structured JSON (python-json-logger); access-log middleware per request |
| `/pynightsky/worker` | 14 days | Structured JSON, same formatter |
| `aws-waf-logs-pynightsky` | 30 days | Full WAF request log (name prefix required for WAF→CW delivery) |

`LOG_LEVEL` env var (default `INFO`) controls verbosity on both Lambdas.

## X-Ray

`Tracing.ACTIVE` on the **API** and **Worker** Lambda only. **Not** enabled on the TLE warmer or
the provider-health monitor (both are simple scheduled pollers with no downstream call chain
worth tracing). `patch_all()` is used rather than a module allowlist — see the M7.3 note in
`cdk/lambda_api_stack.py` for why (`aws-xray-sdk` 2.14/2.15 rejects `"urllib"` as a module name).

## Custom metrics

`PyNightSky/WeatherProviders` namespace (EMF, emitted by `apps/provider_health/handler.py`):
`ProviderUp`, `HTTPVerificationLatency`, `DynamoDBWriteFailure` per provider. `PyNightSky`
namespace: `UpstreamErrors` (log-metric-filter derived, see above).

## Circuit breaker (request-path, not an alarm)

`darkhours/circuit_breaker.py` gates every outbound provider call (weather, TLE, WAQI,
SWPC, Nominatim, AWS Location/GeoRoutes): 3 consecutive failures (Celestrak: 1) open the
breaker and calls are skipped instantly instead of eating the provider's timeout. Recovery
is self-timed (60s cooldown + one probe; Celestrak 300s) — unless
`PYNIGHTSKY_PROVIDER_HEALTH_TABLE` is set, in which case the four providers the synthetic
monitor covers (`open-meteo`, `7timer`, `swpc`, `waqi`) defer to its UP/DOWN signal
instead: fresh DOWN blocks without probing, fresh UP grants a probe (only a real success
closes the breaker). **The env var is not wired in CDK yet** — until that follow-up (IAM
`dynamodb:GetItem` on the ProviderHealth table + the env var on the API/worker Lambdas),
everything self-times, which is safe: the monitor read fails fast (1s timeouts, 1 attempt)
and degrades to self-timed on any error, so a missing/wrong grant can't hang requests.

Flags: `PYNIGHTSKY_CIRCUIT_BREAKER_ENABLED` (default on),
`PYNIGHTSKY_CIRCUIT_BREAKER_<PROVIDER>_DISABLE` per provider key. Breaker state is
per-container and in-memory (same caveat as `darkhours/provider_health.py`); a skipped
call writes no `provider_health.record()`, so `/healthz` keeps showing the last real
observed status. Skips surface to users as the existing `wx_error` "temporarily
unavailable" messaging (single-night report and, since this change, the calendar view).

## Known gap, left open on purpose

**AWS Application Insights** is enabled account-wide for a resource group named after the app
domain (console-managed, outside CDK — `aws application-insights list-applications` shows it).
It auto-generated roughly 20 CloudWatch alarms covering Lambda Duration/Errors/Throttles across
four functions, DynamoDB capacity/errors, and SQS queue depth/age for both the jobs queue and its
DLQ. **Every one of those alarms has empty `AlarmActions`** — they evaluate and hold a state, but
notify nobody, and never will unless reconfigured through Application Insights itself (not CDK).

Decision made 2026-07-14: leave Application Insights enabled rather than disable it as part of
this change. Its silent alarms were a real coverage gap before this M8 change; now that
`ApiErrorsAlarm`/`WorkerErrorsAlarm`/`DlqNotEmptyAlarm` exist with real notifications, they're
redundant noise rather than a hole. Disabling Application Insights would save roughly $2/mo and
could be a follow-up once the new alarms have run for a couple of weeks — not bundled here to
keep this change's blast radius small (same "small blast radius, verify first" style as the rest
of the cloud migration).

## Budget

The AWS Budget itself predates and lives outside CDK (no CloudFormation import path exists for
`aws_budgets` onto an already-console-created budget). Its notification threshold is added via
CLI, not code:

```
aws budgets create-notification \
  --account-id $(aws sts get-caller-identity --query Account --output text) \
  --budget-name "AWS Account Budget" \
  --notification Type=ACTUAL,ComparisonOperator=GREATER_THAN,Threshold=80,ThresholdType=PERCENTAGE \
  --subscribers SubscriptionType=EMAIL,Address=<you>
```

## Capacity limits

Three ceilings govern how much traffic this stack absorbs. Two of them live outside CDK,
so they are invisible in the templates and easy to rediscover the hard way.

**Lambda concurrent executions (account-wide).** This account ran at **10** — AWS's default
is 1000 — until an increase back to 1000 was requested on 2026-08-08. The API and the worker draw from that same pool,
so it is a shared ceiling, not a per-function one. Check and raise it with:

```
aws service-quotas get-service-quota --service-code lambda --quota-code L-B99A9384
aws service-quotas request-service-quota-increase \
  --service-code lambda --quota-code L-B99A9384 --desired-value 1000
```

Watch `AWS/Lambda ConcurrentExecutions` with no dimensions (account-wide, `Maximum`) against
whatever that value currently is. Per-function `Throttles` is the lagging indicator: by the
time it is non-zero, users have already seen 5xx.

**Worker SQS concurrency.** `max_concurrency` on the worker's `SqsEventSource`
(`cdk/lambda_api_stack.py`) is what stops a job backlog from consuming the whole pool and
starving the API. It is sized against the account quota above, so the two must move
together: raising the quota without raising this just leaves jobs queuing, and raising this
without the quota re-opens the starvation path it exists to close. Keep it at roughly half
the quota or below.

**Cache table capacity.** The cache table is created outside CDK and imported by name
(`dynamodb.Table.from_table_name`), so its billing mode is *not* under CloudFormation and a
`cdk deploy` will not reassert it. It ran provisioned at 25 RCU / 25 WCU with no autoscaling
— exactly the legacy always-free-tier allowance, which is why DynamoDB billed ~$0. Under
load, short spikes above 25 survive only on burst credit (~300s of unused capacity); a
sustained overrun throttles. It was switched to on-demand on 2026-08-08 after a surge peaked
at ~2x provisioned read capacity for a full minute; expect ~$2/mo at that traffic level. Inspect and switch with:

```
aws dynamodb describe-table --table-name "$PYNIGHTSKY_CACHE_TABLE" \
  --query 'Table.{Billing:BillingModeSummary.BillingMode,RCU:ProvisionedThroughput.ReadCapacityUnits}'
aws dynamodb update-table --table-name "$PYNIGHTSKY_CACHE_TABLE" --billing-mode PAY_PER_REQUEST
```

Billing mode can only be switched once per 24 hours. Note that the cache does nearly as many
writes as reads, so table load scales close to linearly with traffic rather than flattening
as the cache warms.

## Edge caching and the per-IP rate limit

These two interact, and getting one wrong shows up as the other misbehaving.

**Cache-Control is set at upload**, per `BucketDeployment` pass in `cdk/lambda_api_stack.py`,
not by a CloudFront response-headers policy. Three tiers: `assets/*` is content-hashed so it
is `immutable` for a year; other static files get a one-day TTL; `*.html` is `no-cache` so a
deploy is picked up on the next navigation rather than a day later. Before this was set,
objects landed with no cache header at all and browsers revalidated everything on every
navigation — one session in the WAF logs made 146 favicon round-trips in an hour.

**`RateLimitPerIp` is scoped to the API surface** (`/night`, `/suggest`, `/nearby`,
`/calendar`, `/jobs`, `/healthz`). It must stay scoped. Unscoped it counted every asset
CloudFront served, so an engaged session could spend its 150-request budget on favicons and
bundle fetches and then get 403s on the API calls that actually mattered. If you add a new
Lambda-backed path, add it to the scope-down list too — a path that isn't listed is not rate
limited at all.

To see what the limiter is actually catching, query the WAF log group
(`aws-waf-logs-pynightsky`, 30-day retention) grouped by `terminatingRuleId` and
`httpRequest.uri`. Check whether blocked clients are also making real API calls before
treating them as abusive: a client that fetches `/suggest`, `/night` and then polls
`/jobs/{id}` is a user, not a scraper.
