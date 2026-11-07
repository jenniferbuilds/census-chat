"""Agent pipeline and Knowledge Base retrieval tests."""

from unittest.mock import MagicMock, patch

import pytest

from guardrails import GuardrailBlockedError
from tools.census_retrieve import CensusRetrievalError, retrieve_census_context


class Context:
    session_id = "12345678-1234-1234-1234-123456789012"


def _invoke(message: str, blocked: bool = False):
    import agent

    guardrail_effect = (
        GuardrailBlockedError("I answer Census questions only.") if blocked else None
    )
    with (
        patch.object(agent, "apply_guardrail", side_effect=guardrail_effect),
        patch.object(agent, "load_session_history", return_value=[]),
        patch.object(agent, "retrieve_long_term_context", return_value=""),
        patch.object(agent, "retrieve_census_context", return_value="Cook County population: 5,169,517"),
        patch.object(agent, "save_event") as save_event,
        patch.object(agent, "Agent") as agent_class,
    ):
        agent_class.return_value.return_value = "Cook County had 5,169,517 residents."
        result = agent.invoke(
            {"message": message, "actor_id": "actor-1"}, Context()
        )
        return result, save_event


def test_census_question_returns_grounded_response_and_saves_memory():
    result, save_event = _invoke("What is Cook County's population?")
    assert "5,169,517" in result["response"]
    assert result["blocked"] is False
    save_event.assert_called_once()


def test_empty_message_returns_validation_error():
    import agent

    result = agent.invoke({"message": "", "actor_id": "actor-1"}, Context())
    assert result["status"] == 400


def test_blocked_input_never_invokes_model():
    result, save_event = _invoke("Tell me a joke", blocked=True)
    assert result["blocked"] is True
    save_event.assert_not_called()


def test_retrieval_formats_qualified_documents(mock_kb_response):
    with patch("tools.census_retrieve._get_client") as get_client:
        client = MagicMock()
        client.retrieve.return_value = mock_kb_response
        get_client.return_value = client
        result = retrieve_census_context("Cook County income")
        assert "Cook County" in result
        assert "$68,427" in result
        assert "Census source" in result


def test_retrieval_returns_empty_for_no_qualified_documents():
    with patch("tools.census_retrieve._get_client") as get_client:
        client = MagicMock()
        client.retrieve.return_value = {"retrievalResults": []}
        get_client.return_value = client
        assert retrieve_census_context("population of Mars") == ""


def test_retrieval_error_is_explicit():
    with patch("tools.census_retrieve._get_client") as get_client:
        client = MagicMock()
        client.retrieve.side_effect = RuntimeError("KB unavailable")
        get_client.return_value = client
        with pytest.raises(CensusRetrievalError):
            retrieve_census_context("population")
