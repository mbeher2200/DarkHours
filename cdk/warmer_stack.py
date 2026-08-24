"""Scheduled TLE cache warmer (M6.2).

A tiny zip Lambda (just our source — no rasterio/GDAL, boto3 comes from the Lambda
runtime) on a 6-hourly EventBridge schedule. It refreshes the satellite TLEs into
the shared DynamoDB cache so user /night?satellites requests are cache hits and the
app is decoupled from Celestrak's availability/rate limits. TLE is global, so there
is nothing per-region to warm.

The cache table is referenced (never managed); its name comes from the environment
so the public repo carries no identifiers.
"""
import os
import pathlib
import shutil

from aws_cdk import (
    CfnOutput,
    Stack,
    Duration,
    Tags,
    aws_lambda as lambda_,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_sns as sns,
)
from constructs import Construct

_REPO = pathlib.Path(__file__).resolve().parents[1]


def _stage_warmer_code() -> str:
    """Stage just the source the warmer needs into a clean dir (no Docker, no deps).

    boto3 comes from the Lambda runtime; rasterio/skyfield are never imported by the
    warmer path, so only the source tree is shipped. de421.bsp (16 MB ephemeris) and
    apps/api (FastAPI) are intentionally left out — the warmer imports neither.
    """
    stage = _REPO / "cdk" / ".warmer_build"
    if stage.exists():
        shutil.rmtree(stage)
    _ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "de421.bsp", ".DS_Store")
    shutil.copytree(_REPO / "darkhours", stage / "darkhours", ignore=_ignore)
    (stage / "apps").mkdir(parents=True)
    shutil.copy(_REPO / "apps" / "__init__.py", stage / "apps" / "__init__.py")
    shutil.copytree(_REPO / "apps" / "warmer", stage / "apps" / "warmer", ignore=_ignore)
    return str(stage)


class WarmerStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs):
        super().__init__(scope, cid, **kwargs)

        cache_table = os.environ["PYNIGHTSKY_CACHE_TABLE"]
        Tags.of(self).add("Project", "pynightsky")
        Tags.of(self).add("Env", "prod")
        Tags.of(self).add("Component", "warmer")

        fn = lambda_.Function(
            self, "TleWarmer",
            runtime=lambda_.Runtime.PYTHON_3_13,
            handler="apps.warmer.handler.handler",
            code=lambda_.Code.from_asset(_stage_warmer_code()),
            # The warmer now revalidates every key on every run rather than only
            # repairing expired ones, so a run is 4 Celestrak calls paced 2s apart
            # (a 6s floor) plus up to ~30s for the group fetch. Observed max duration
            # under the old, mostly-no-op behaviour was already 33s against a 60s cap.
            timeout=Duration.seconds(120),
            memory_size=256,
            environment={
                "PYNIGHTSKY_BACKEND": "aws",
                "PYNIGHTSKY_CACHE_TABLE": cache_table,
            },
            description="Scheduled TLE cache warmer (Celestrak → DynamoDB).",
        )

        table = dynamodb.Table.from_table_name(self, "CacheTable", cache_table)
        table.grant_read_write_data(fn)       # get/get_stale/set on tle|* keys

        # Revalidate every 6h against a 24h TLE_TTL. The gap is deliberate: the TTL
        # is when DynamoDB deletes the row, so four refresh cycles of margin means
        # three consecutive warm failures still leave a usable copy. Keeping the two
        # equal (both 6h) is what previously let a single missed refresh delete the
        # only copy — after which Celestrak's "unchanged since your last download"
        # 403 had nothing to revalidate and the app re-asked on every request.
        events.Rule(
            self, "Every6h",
            description="Revalidate satellite TLEs every 6h (TLE_TTL is 24h — 4x margin).",
            schedule=events.Schedule.rate(Duration.hours(6)),
            targets=[targets.LambdaFunction(fn)],
        )

        # --- Alarms ---
        # This stack previously had none, which is how it ran for 14 days reporting
        # ok=True on every invocation while the Starlink cache row did not exist and
        # 3 invocations errored outright. The warmer is the only thing that populates
        # these keys — the request path reads them and never fetches — so a warmer
        # that stops working is a silent, total loss of satellite data.
        #
        # Subscribe after deploy:
        #   aws sns subscribe --topic-arn <AlarmTopicArn output> \
        #     --protocol email --notification-endpoint <you>
        alarm_topic = sns.Topic(self, "AlarmTopic", display_name="PyNightSky TLE Warmer Alarms")

        # TleWarmFailure counts keys that were not readable back out of the cache
        # after the write. It is emitted (as 0) on every run, so missing data means
        # the warmer did not run, not that everything is fine.
        warm_failure_alarm = cloudwatch.Alarm(
            self, "TleWarmFailureAlarm",
            alarm_description="A TLE cache key was not verifiably written by the warmer.",
            metric=cloudwatch.Metric(
                namespace="PyNightSky/Tle",
                metric_name="TleWarmFailure",
                statistic="Sum",
                period=Duration.minutes(30),
            ),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        warm_failure_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        warmer_errors_alarm = cloudwatch.Alarm(
            self, "TleWarmerErrorsAlarm",
            alarm_description="The TLE warmer Lambda errored.",
            metric=fn.metric_errors(statistic="Sum", period=Duration.minutes(30)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        warmer_errors_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # Dead man's switch: the schedule is 6h, so a 7h window with no invocation
        # means the rule stopped firing. BREACHING on missing data is the point —
        # "no data" is exactly the failure being detected.
        dead_mans_switch = cloudwatch.Alarm(
            self, "TleWarmerDeadMansSwitchAlarm",
            alarm_description="The TLE warmer has not executed within 7 hours.",
            metric=fn.metric_invocations(statistic="Sum", period=Duration.hours(7)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
        )
        dead_mans_switch.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        CfnOutput(self, "AlarmTopicArn", value=alarm_topic.topic_arn,
                  description="Subscribe an email endpoint to receive TLE warmer alarms.")
        CfnOutput(self, "WarmerFunctionName", value=fn.function_name,
                  description="Invoke manually to fill a cold TLE cache immediately.")
