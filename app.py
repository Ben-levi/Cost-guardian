#!/usr/bin/env python3
import os

import aws_cdk as cdk

from cost_guardian.cost_guardian_stack import CostGuardianStack  


app = cdk.App()
CostGuardianStack(
    app,
    "CostGuardianStack",
    # env=cdk.Environment(
    #     account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    #     region=os.getenv("CDK_DEFAULT_REGION"),
    # ),
)

app.synth()
