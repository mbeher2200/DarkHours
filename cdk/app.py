#!/usr/bin/env python3
"""CDK app entry point for the PyNightSky API infrastructure."""
import os

import aws_cdk as cdk

from lambda_api_stack import LambdaApiStack

app = cdk.App()
_env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

# Lambda + CloudFront is the live compute platform.
LambdaApiStack(app, "PyNightSkyLambda", env=_env)

# NOTE: the App Runner deployment (PyNightSkyStack in pynightsky_stack.py) was retired
# and destroyed once the Lambda+CloudFront path was verified (M4.7). The class is kept
# as reference but is intentionally NOT instantiated, so `cdk deploy` won't resurrect it.

app.synth()
