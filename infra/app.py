#!/usr/bin/env python3
"""CDK アプリのエントリポイント。"""

import aws_cdk as cdk

from stacks.sleeptech_stack import SleepTechStack

app = cdk.App()
SleepTechStack(app, "SleepTechStack")
app.synth()
