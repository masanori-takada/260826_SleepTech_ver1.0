#!/usr/bin/env python3
"""CDK アプリのエントリポイント。"""

import os

import aws_cdk as cdk

from stacks.sleeptech_stack import SleepTechStack

DEPLOY_REGION = "us-east-1"
DEPLOY_ACCOUNT = "469683586851"

app = cdk.App()
# M6対応: env未指定だとCDKが環境非依存スタック（account/region不定）として
# 合成し、環境依存のコンテキスト解決（AZ列挙等）やdiffの安定性を損なう。
SleepTechStack(
    app,
    "SleepTechStack",
    env=cdk.Environment(
        account=DEPLOY_ACCOUNT,
        region=DEPLOY_REGION,
    ),
)
app.synth()
