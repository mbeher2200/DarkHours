"""Weather provider health monitor.

A tiny zip Lambda (just apps/provider_health — boto3 comes from the runtime, no
rasterio/GDAL) on a 5-minute EventBridge schedule. It polls Open-Meteo, 7Timer,
NOAA SWPC, and WAQI (when AQICN_TOKEN is configured) independently, writes
UP/DOWN + latency to its own DynamoDB table, and emits CloudWatch EMF metrics
(ProviderUp, HTTPVerificationLatency, DynamoDBWriteFailure) so SRE alarms can
fire on sustained outage or a missed execution, decoupled from user traffic.

Celestrak is deliberately NOT in this list — it has specific access-timing
expectations and will shut off a client that polls it too regularly; a 5-min
synthetic check would put this app's real TLE access at risk.
"""
import os
import pathlib
import shutil

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    Tags,
    aws_lambda as lambda_,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
)
from constructs import Construct

_REPO = pathlib.Path(__file__).resolve().parents[1]
_NAMESPACE = "PyNightSky/WeatherProviders"
_AQICN_TOKEN = os.environ.get("AQICN_TOKEN", "")
_PROVIDERS = ["open-meteo", "7timer", "swpc"]
if _AQICN_TOKEN:
    _PROVIDERS.append("waqi")


def _stage_provider_health_code() -> str:
    """Stage just apps/provider_health — it imports nothing else from the repo."""
    stage = _REPO / "cdk" / ".provider_health_build"
    if stage.exists():
        shutil.rmtree(stage)
    _ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
    (stage / "apps").mkdir(parents=True)
    shutil.copy(_REPO / "apps" / "__init__.py", stage / "apps" / "__init__.py")
    shutil.copytree(_REPO / "apps" / "provider_health", stage / "apps" / "provider_health", ignore=_ignore)
    return str(stage)


class ProviderHealthStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kwargs):
        super().__init__(scope, cid, **kwargs)

        Tags.of(self).add("Project", "pynightsky")
        Tags.of(self).add("Env", "prod")
        Tags.of(self).add("Component", "provider-health")

        table = dynamodb.Table(
            self, "ProviderHealthTable",
            partition_key=dynamodb.Attribute(name="provider_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,  # rebuilt from the next 5-min poll
        )

        fn = lambda_.Function(
            self, "ProviderHealthMonitor",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.ARM_64,
            handler="apps.provider_health.handler.handler",
            code=lambda_.Code.from_asset(_stage_provider_health_code()),
            timeout=Duration.seconds(30),   # N providers x (4s timeout + 2s retry delay), in parallel
            memory_size=128,
            environment={
                "PROVIDER_HEALTH_TABLE": table.table_name,
                "AQICN_TOKEN": _AQICN_TOKEN,
            },
            description="Polls weather providers every 5 min, writes UP/DOWN to DynamoDB.",
        )
        table.grant_write_data(fn)

        events.Rule(
            self, "Every5min",
            description="Poll weather provider health every 5 minutes.",
            schedule=events.Schedule.rate(Duration.minutes(5)),
            targets=[targets.LambdaFunction(fn)],
        )

        # SNS topic for alarm notifications. Subscribe your email after deploy:
        #   aws sns subscribe --topic-arn <AlarmTopicArn> \
        #     --protocol email --notification-endpoint <your@email.com>
        alarm_topic = sns.Topic(self, "AlarmTopic", display_name="PyNightSky Provider Health Alarms")

        for provider_id in _PROVIDERS:
            # Hard failure: ProviderUp == 0 for 3 consecutive 5-min periods (15 min).
            alarm = cloudwatch.Alarm(
                self, f"{provider_id.replace('-', '')}DownAlarm",
                alarm_description=f"{provider_id} reported DOWN for 3 consecutive checks (15 min).",
                metric=cloudwatch.Metric(
                    namespace=_NAMESPACE,
                    metric_name="ProviderUp",
                    dimensions_map={"Provider": provider_id},
                    statistic="Maximum",
                    period=Duration.minutes(5),
                ),
                threshold=1,
                evaluation_periods=3,
                datapoints_to_alarm=3,
                comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        write_failure_alarm = cloudwatch.Alarm(
            self, "DynamoWriteFailureAlarm",
            alarm_description="A provider health check failed to write to DynamoDB.",
            metric=cloudwatch.Metric(
                namespace=_NAMESPACE,
                metric_name="DynamoDBWriteFailure",
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=0,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        write_failure_alarm.add_alarm_action(cw_actions.SnsAction(alarm_topic))

        # Dead man's switch: the function must invoke at least once every 6 minutes.
        dead_mans_switch = cloudwatch.Alarm(
            self, "DeadMansSwitchAlarm",
            alarm_description="Provider health monitor has not executed within 6 minutes.",
            metric=fn.metric_invocations(statistic="Sum", period=Duration.minutes(6)),
            threshold=1,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
        )
        dead_mans_switch.add_alarm_action(cw_actions.SnsAction(alarm_topic))
