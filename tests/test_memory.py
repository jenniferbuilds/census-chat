"""Unit tests for AgentCore short- and long-term memory."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from memory.long_term import _active_strategies, retrieve_long_term_context
from memory.short_term import load_session_history, save_event


def test_save_event_uses_agentcore_payload_shape():
    with patch("memory.short_term._get_client") as get_client:
        client = MagicMock()
        client.create_event.return_value = {"event": {"eventId": "evt-123"}}
        get_client.return_value = client
        assert save_event("session-1", "actor-1", "Question", "Answer") == "evt-123"
        request = client.create_event.call_args.kwargs
        assert isinstance(request["payload"], list)
        assert request["payload"][0]["conversational"]["role"] == "USER"
        assert request["payload"][1]["conversational"]["role"] == "ASSISTANT"
        assert request["eventTimestamp"].tzinfo is not None


def test_save_event_failure_is_non_fatal():
    with patch("memory.short_term._get_client") as get_client:
        client = MagicMock()
        client.create_event.side_effect = RuntimeError("down")
        get_client.return_value = client
        assert save_event("session-1", "actor-1", "Question", "Answer") is None


def test_load_session_history_parses_event_payloads():
    now = datetime.now(timezone.utc)
    with patch("memory.short_term._get_client") as get_client:
        client = MagicMock()
        client.list_events.return_value = {
            "events": [
                {
                    "eventTimestamp": now,
                    "payload": [
                        {"conversational": {"role": "USER", "content": {"text": "Income?"}}},
                        {"conversational": {"role": "ASSISTANT", "content": {"text": "$77,072."}}},
                    ],
                }
            ]
        }
        get_client.return_value = client
        assert load_session_history("session-1", "actor-1") == [
            {"role": "user", "content": "Income?"},
            {"role": "assistant", "content": "$77,072."},
        ]


def test_load_session_failure_returns_empty_history():
    with patch("memory.short_term._get_client") as get_client:
        client = MagicMock()
        client.list_events.side_effect = RuntimeError("down")
        get_client.return_value = client
        assert load_session_history("session-1", "actor-1") == []


def test_long_term_retrieves_each_active_strategy():
    _active_strategies.cache_clear()
    with (
        patch("memory.long_term._get_control_client") as get_control,
        patch("memory.long_term._get_data_client") as get_data,
    ):
        control = MagicMock()
        control.get_memory.return_value = {
            "memory": {
                "strategies": [
                    {"type": "USER_PREFERENCE", "strategyId": "pref-1", "status": "ACTIVE"},
                    {"type": "SUMMARIZATION", "strategyId": "summary-1", "status": "ACTIVE"},
                ]
            }
        }
        data = MagicMock()
        data.retrieve_memory_records.side_effect = [
            {"memoryRecordSummaries": [{"score": 0.9, "content": {"text": "Prefers county data"}}]},
            {"memoryRecordSummaries": [{"score": 0.8, "content": {"text": "Compared Illinois counties"}}]},
        ]
        get_control.return_value = control
        get_data.return_value = data
        result = retrieve_long_term_context("actor-1", "Illinois income")
        assert "Prefers county data" in result
        assert "Compared Illinois counties" in result
        assert data.retrieve_memory_records.call_count == 2
    _active_strategies.cache_clear()


def test_long_term_failure_returns_empty_context():
    _active_strategies.cache_clear()
    with patch("memory.long_term._get_control_client") as get_control:
        client = MagicMock()
        client.get_memory.side_effect = RuntimeError("down")
        get_control.return_value = client
        assert retrieve_long_term_context("actor-1", "income") == ""
    _active_strategies.cache_clear()
