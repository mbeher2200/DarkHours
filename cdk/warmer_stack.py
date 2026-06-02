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
    Stack,
    Duration,
    Tags,
    aws_lambda as lambda_,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
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
    shutil.copytree(_REPO / "PyNightSkyPredictor", stage / "PyNightSkyPredictor", ignore=_ignore)
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
            timeout=Duration.seconds(60),     # Starlink group fetch can take ~30s
            memory_size=256,
            environment={
                "PYNIGHTSKY_BACKEND": "aws",
                "PYNIGHTSKY_CACHE_TABLE": cache_table,
            },
            description="Scheduled TLE cache warmer (Celestrak → DynamoDB).",
        )

        table = dynamodb.Table.from_table_name(self, "CacheTable", cache_table)
        table.grant_read_write_data(fn)       # get/get_stale/set on tle|* keys

        events.Rule(
            self, "Every6h",
            description="Refresh satellite TLEs every 6h (matches tle_provider TLE_TTL).",
            schedule=events.Schedule.rate(Duration.hours(6)),
            targets=[targets.LambdaFunction(fn)],
        )
