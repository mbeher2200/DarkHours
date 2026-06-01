#!/usr/bin/env python3
"""CDK app entry point for the PyNightSky API infrastructure."""
import os

import aws_cdk as cdk

from pynightsky_stack import PyNightSkyStack
from lambda_api_stack import LambdaApiStack

app = cdk.App()
_env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

# App Runner deployment (being retired — kept until the Lambda path is verified).
PyNightSkyStack(app, "PyNightSkyApi", env=_env)

# Lambda + CloudFront deployment (the App Runner successor).
LambdaApiStack(app, "PyNightSkyLambda", env=_env)

app.synth()
