#!/usr/bin/env python3
"""CDK app entry point for the PyNightSky API infrastructure."""
import os

import aws_cdk as cdk

from pynightsky_stack import PyNightSkyStack

app = cdk.App()
PyNightSkyStack(
    app, "PyNightSkyApi",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)
app.synth()
