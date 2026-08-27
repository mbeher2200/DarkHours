# Observability — Dashboard, Alarms, Logs, Traces

Reference for what's monitored in this stack, where it notifies, and where to look.

No AWS account id, ARN, S3 bucket name, DynamoDB table name, or notification address appears in
this file — this is a public repo (see CLAUDE.md). Stack names, log group paths, and alarm names
are fixed naming-convention identifiers, not secrets (see CLAUDE.md). Anything account-specific
or randomized is discovered from the environment or a CDK stack output at run time.

## Dashboard

`cloudwatch.Dashboard` **PyNightSky-Overview**, defined in `cdk/lambda_api_stack.py`.
Console link: `aws cloudformation describe-stacks --stack-name PyNightSkyLambda
--query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" --output text`.

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

## Alarms

All alarms notify via **SNS → email**. Find the topic ARN with:
```
aws cloudformation describe-stacks --stack-name <stack> --query \
  "Stacks[0].Outputs[?OutputKey=='AlarmTopicArn'].OutputValue"
```
Subscribe with:
```
aws sns subscribe --topic-arn <arn> --protocol email --notification-endpoint <you> \
  --region us-east-1
```

**`PyNightSkyLambda` stack:**

| Alarm | Condition |
|---|---|
| `UpstreamErrorAlarm` | ≥3 ERROR-level log lines (AWS Location / Celestrak / 7Timer) in 5 min |
| `ApiErrorsAlarm` | ≥3 API Lambda errors in 5 min |
| `WorkerErrorsAlarm` | ≥2 Worker Lambda errors in 5 min |
| `DlqNotEmptyAlarm` | ≥1 message visible in the jobs dead-letter queue |
| `CloudFront5xxErrorRateAlarm` | 5xx rate ≥20% over 2 consecutive 5-min periods |

**`PyNightSkyProviderHealth` stack:**

| Alarm | Condition |
|---|---|
| `open-meteoDownAlarm` / `7timerDownAlarm` | Provider reported DOWN for 3 consecutive 5-min checks |
| `DynamoWriteFailureAlarm` | A health-check write to DynamoDB failed |
| `DeadMansSwitchAlarm` | The monitor itself hasn't run in 6 minutes |

**`PyNightSkyWarmer` stack:**

| Alarm | Condition |
|---|---|
| `TleWarmFailureAlarm` | ≥1 TLE cache key not verifiably written in a 30-min window |
| `TleWarmerErrorsAlarm` | ≥1 warmer Lambda error in 30 min |
| `TleWarmerDeadMansSwitchAlarm` | The warmer hasn't run in 7 hours (schedule is 6 h) |

Separate SNS topic from `PyNightSkyLambda`; subscribe it separately. See
[`TLE_CACHE.md`](TLE_CACHE.md).

Test the notification path:
```
aws cloudwatch set-alarm-state --alarm-name <deployed alarm name> --state-value ALARM \
  --state-reason "manual test" --region us-east-1
```

## Logs

| Log group | Retention | Contents |
|---|---|---|
| `/pynightsky/api` | 14 days | Structured JSON; access-log middleware per request |
| `/pynightsky/worker` | 14 days | Structured JSON, same formatter |
| `aws-waf-logs-pynightsky` | 30 days | Full WAF request log |

`LOG_LEVEL` env var (default `INFO`) controls verbosity on both Lambdas.

## Custom metrics

`PyNightSky/WeatherProviders` namespace (EMF): `ProviderUp`, `HTTPVerificationLatency`,
`DynamoDBWriteFailure` per provider. `PyNightSky/Tle` namespace (EMF, from the warmer):
`TleWarmSuccess`, `TleCachedBytes` per key, `TleWarmFailure`. `PyNightSky` namespace:
`UpstreamErrors`.
