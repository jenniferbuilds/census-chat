#!/usr/bin/env python3
"""AWS CDK application for Census Chat."""

import os

import aws_cdk as cdk

from stacks.agent_stack import AgentStack
from stacks.data_stack import DataStack
from stacks.frontend_stack import FrontendStack

app = cdk.App()
env = cdk.Environment(account="887083795887", region="us-east-2")

# The Knowledge Base is created after its S3 source and S3 Vectors index.
# Supply its stable ID from the deployment environment for IAM/runtime wiring.
knowledge_base_id = os.environ.get("CENSUS_CHAT_KB_ID", "S2WBI1PIIF")

data_stack = DataStack(app, "CensusChatDataStack", env=env)
agent_stack = AgentStack(
    app,
    "CensusChatAgentStack",
    kb_bucket_name=data_stack.kb_bucket.bucket_name,
    knowledge_base_id=knowledge_base_id,
    guardrail_id=data_stack.guardrail.attr_guardrail_id,
    guardrail_version=data_stack.guardrail_version.attr_version,
    env=env,
)
agent_stack.add_stack_dependency(data_stack)

frontend_stack = FrontendStack(app, "CensusChatFrontendStack", env=env)
frontend_stack.add_stack_dependency(agent_stack)

app.synth()
