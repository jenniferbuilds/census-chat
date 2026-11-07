"""Unit tests for mandatory input and output Bedrock Guardrails."""

from unittest.mock import MagicMock, patch

import pytest

from guardrails import (
    GuardrailBlockedError,
    GuardrailServiceError,
    apply_guardrail,
)


def test_on_topic_input_passes(mock_guardrail_pass):
    with patch("guardrails._get_client") as get_client:
        client = MagicMock()
        client.apply_guardrail.return_value = mock_guardrail_pass
        get_client.return_value = client
        text = "What is the population of Cook County?"
        assert apply_guardrail(text, "INPUT") == text


def test_off_topic_input_is_blocked(mock_guardrail_block):
    with patch("guardrails._get_client") as get_client:
        client = MagicMock()
        client.apply_guardrail.return_value = mock_guardrail_block
        get_client.return_value = client
        with pytest.raises(GuardrailBlockedError):
            apply_guardrail("Tell me a joke", "INPUT")


def test_guardrail_api_error_fails_closed():
    with patch("guardrails._get_client") as get_client:
        client = MagicMock()
        client.apply_guardrail.side_effect = RuntimeError("API unavailable")
        get_client.return_value = client
        with pytest.raises(GuardrailServiceError):
            apply_guardrail("What is the poverty rate in Texas?", "INPUT")


def test_output_includes_grounding_qualifiers(mock_guardrail_pass):
    with patch("guardrails._get_client") as get_client:
        client = MagicMock()
        client.apply_guardrail.return_value = mock_guardrail_pass
        get_client.return_value = client
        answer = "Cook County had 5,169,517 residents."
        assert apply_guardrail(
            answer,
            "OUTPUT",
            grounding_source="Cook County total population: 5,169,517",
            query="What is Cook County's population?",
        ) == answer
        content = client.apply_guardrail.call_args.kwargs["content"]
        qualifiers = [item["text"].get("qualifiers", []) for item in content]
        assert ["grounding_source"] in qualifiers
        assert ["query"] in qualifiers
        assert ["guard_content"] in qualifiers
