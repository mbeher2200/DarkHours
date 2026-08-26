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
import subprocess
import sys

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


# The only third-party package the warmer path imports. tle_provider renders the
# filtered Starlink rows from OMM back into TLE line pairs with sgp4.exporter, so
# the group refresh raises ModuleNotFoundError without it — caught, logged, and
# reported as a warm failure, which is how this was found rather than by anything
# silently degrading.
_WARMER_PIP_DEPS = ("sgp4==2.27",)

# Matches the Function's architecture and runtime below. Wheels are downloaded for
# the target platform rather than this host's, so no Docker and no cross-compile —
# which is what keeps this stack deployable from a laptop.
_WARMER_PLATFORM       = "manylinux2014_x86_64"
_WARMER_PYTHON_VERSION = "3.13"


def _stage_warmer_code() -> str:
    """Stage the source the warmer needs, plus its one dependency, into a clean dir.

    boto3 comes from the Lambda runtime; rasterio/skyfield are never imported by the
    warmer path, so nothing else is shipped. de421.bsp (16 MB ephemeris) and
    apps/api (FastAPI) are intentionally left out — the warmer imports neither.

    sgp4 is installed with --platform/--only-binary so pip fetches the Linux wheel
    regardless of the host: a macOS wheel here would import fine locally and fail in
    Lambda, which is the failure mode worth designing out.
    """
    stage = _REPO / "cdk" / ".warmer_build"
    if stage.exists():
        shutil.rmtree(stage)
    _ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "de421.bsp", ".DS_Store")
    shutil.copytree(_REPO / "darkhours", stage / "darkhours", ignore=_ignore)
    (stage / "apps").mkdir(parents=True)
    shutil.copy(_REPO / "apps" / "__init__.py", stage / "apps" / "__init__.py")
    shutil.copytree(_REPO / "apps" / "warmer", stage / "apps" / "warmer", ignore=_ignore)

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--target", str(stage),
         "--platform", _WARMER_PLATFORM, "--python-version", _WARMER_PYTHON_VERSION,
         "--only-binary=:all:", *_WARMER_PIP_DEPS],
        check=True,
    )
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
