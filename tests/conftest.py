"""
Shared pytest fixtures for census-chat tests.
"""
import os
import sys
import pytest

# Add agent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "etl"))

# Set dummy env vars before any AWS SDK imports
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("KNOWLEDGE_BASE_ID", "test-kb-id")
os.environ.setdefault("GUARDRAIL_ID", "test-guardrail-id")
os.environ.setdefault("GUARDRAIL_VERSION", "1")
os.environ.setdefault("AGENTCORE_MEMORY_ID", "test-memory-id")
os.environ.setdefault("MODEL_ID", "us.anthropic.claude-opus-5-5")


@pytest.fixture
def mock_kb_response():
    """Realistic KB retrieval response for Cook County, IL."""
    return {
        "retrievalResults": [
            {
                "content": {
                    "text": (
                        "GEOGRAPHY: Cook County, IL\n"
                        "FIPS CODE: 17031  |  GEOGRAPHIC LEVEL: County  |  DATA SOURCE: 2020 ACS 5-Year\n"
                        "\nPOPULATION & DEMOGRAPHICS:\n"
                        "  Total Population: 5,275,541\n"
                        "  Median Age: 37.2 years\n"
                        "\nINCOME & POVERTY:\n"
                        "  Median Household Income: $68,427\n"
                        "  Poverty Rate: 14.2%\n"
                        "\nHOUSING:\n"
                        "  Homeownership Rate: 47.8%\n"
                        "  Median Home Value: $305,000\n"
                    )
                },
                "score": 0.92,
                "location": {"s3Location": {"uri": "s3://census-chat-kb/documents/IL_Cook_County_17031.txt"}},
            }
        ]
    }


@pytest.fixture
def mock_guardrail_pass():
    """Guardrail response that passes through."""
    return {"action": "NONE", "outputs": [], "assessments": []}


@pytest.fixture
def mock_guardrail_block():
    """Guardrail response that blocks content."""
    return {
        "action": "GUARDRAIL_INTERVENED",
        "outputs": [{"text": "I'm focused on answering questions about US Census data."}],
        "assessments": [{"topicPolicy": {"topics": [{"name": "non-census-topics", "type": "DENY", "action": "BLOCKED"}]}}],
    }
