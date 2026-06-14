#!/usr/bin/env python3
"""CDK アプリのエントリ（T033 / 原則IV）。"""

import os

import aws_cdk as cdk

from stacks.night_ramen_stack import NightRamenStack

app = cdk.App()
NightRamenStack(
    app,
    "NightRamenStack",
    env=cdk.Environment(
        account=os.getenv("CDK_DEFAULT_ACCOUNT"),
        region=os.getenv("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)
app.synth()
