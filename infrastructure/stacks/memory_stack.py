"""
MemoryStack — AgentCore Memory store with all four long-term strategies
plus short-term (raw event) storage.
"""
from aws_cdk import Stack, CfnOutput
from constructs import Construct
import boto3
import json


class MemoryStack(Stack):
    """
    AgentCore Memory is not yet in CDK L1 constructs, so we create it via
    boto3 in a custom resource pattern executed at deploy time.
    The memory ARN is exported for the AgentStack to consume.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # Memory creation is handled in deploy.sh post-CDK because the
        # bedrock-agentcore control plane API is not yet in CDK.
        # AgentStack reads the ARN from SSM Parameter Store after memory is created.
        pass


def create_memory_store(region: str = "us-east-1") -> dict:
    """
    Creates the AgentCore Memory store with all four long-term strategies.
    Called from deploy.sh after CDK deploy, stores ARN in SSM.

    Returns dict with memoryId and memoryArn.
    """
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    memory_config = {
        "name": "census-chat-memory",
        "description": "Short and long-term memory for census-chat agent",
        "eventExpiryDuration": "P7D",   # 7-day TTL for raw short-term events
        "memoryStrategies": [
            # 1. SUMMARIZATION — session summaries per actor
            {
                "memorySummaryStrategy": {
                    "name": "session-summarization",
                    "description": "Creates concise summaries of each session for future context",
                    "configuration": {
                        "consolidation": {
                            "customPrompt": (
                                "Summarize the key topics the user explored in this census data session. "
                                "Focus on geographies of interest, demographic variables discussed, "
                                "and any comparisons made. Be concise (3-5 sentences)."
                            )
                        }
                    }
                }
            },
            # 2. USER_PREFERENCE — capture stated preferences
            {
                "memoryExtractionStrategy": {
                    "name": "user-preferences",
                    "description": "Captures user preferences about data granularity and topics",
                    "configuration": {
                        "extraction": {
                            "customPrompt": (
                                "Extract any user preferences related to census data exploration. "
                                "Look for: preferred geographic granularity (state/county/tract), "
                                "topics of interest (income, housing, demographics), comparison styles, "
                                "or any stated goals for their research."
                            )
                        }
                    },
                    "type": "USER_PREFERENCE"
                }
            },
            # 3. SEMANTIC — factual knowledge from sessions
            {
                "semanticMemoryStrategy": {
                    "name": "census-facts",
                    "description": "Retains factual census statistics discussed across sessions",
                }
            },
            # 4. EPISODIC — significant exploration episodes
            {
                "memoryExtractionStrategy": {
                    "name": "research-episodes",
                    "description": "Records significant census research episodes by the user",
                    "configuration": {
                        "extraction": {
                            "customPrompt": (
                                "Identify and record significant research episodes from this conversation. "
                                "An episode is a meaningful exploration such as: comparing income across states, "
                                "investigating poverty in a region, or analyzing demographic shifts. "
                                "Record what was explored, key findings, and geographic focus."
                            )
                        }
                    },
                    "type": "EPISODIC"
                }
            },
        ]
    }

    try:
        response = client.create_memory(**memory_config)
        memory_id = response["memory"]["memoryId"]
        memory_arn = response["memory"]["memoryArn"]
        print(f"✅ Memory store created: {memory_id}")
        print(f"   ARN: {memory_arn}")
        return {"memoryId": memory_id, "memoryArn": memory_arn}
    except client.exceptions.ConflictException:
        # Already exists — list and return existing
        memories = client.list_memories()["memories"]
        for m in memories:
            if m["name"] == "census-chat-memory":
                print(f"ℹ️  Memory store already exists: {m['memoryId']}")
                return {"memoryId": m["memoryId"], "memoryArn": m["memoryArn"]}
        raise


if __name__ == "__main__":
    result = create_memory_store()
    print(json.dumps(result, indent=2))
