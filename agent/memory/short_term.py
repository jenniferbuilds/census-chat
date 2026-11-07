"""AgentCore short-term conversation memory service."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import boto3

from settings import AgentSettings

logger = logging.getLogger(__name__)


class ShortTermMemory:
    """Persist and load conversation turns in AgentCore Memory."""

    def __init__(self, settings: AgentSettings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    def save_turn(
        self,
        session_id: str,
        actor_id: str,
        user_message: str,
        assistant_response: str,
        metadata: Mapping[str, object] | None = None,
    ) -> str | None:
        """Persist one user/assistant exchange and return its event ID."""
        if not self._settings.memory_id:
            logger.warning("AGENTCORE_MEMORY_ID is not set; skipping event save")
            return None

        try:
            response = self._get_client().create_event(
                memoryId=self._settings.memory_id,
                actorId=actor_id,
                sessionId=session_id,
                eventTimestamp=datetime.now(timezone.utc),
                payload=[
                    self._conversation_payload("USER", user_message),
                    self._conversation_payload("ASSISTANT", assistant_response),
                ],
                metadata=self._metadata_values(metadata or {}),
            )
        except Exception:
            logger.exception("Failed to save AgentCore short-term memory event")
            return None

        event = response.get("event", {})
        nested_event_id = event.get("eventId") if isinstance(event, Mapping) else None
        event_id = response.get("eventId") or nested_event_id
        logger.info("Saved AgentCore memory event %s", event_id or "(id unavailable)")
        return str(event_id) if event_id else None

    def load_history(
        self,
        session_id: str,
        actor_id: str,
        max_turns: int | None = None,
    ) -> list[dict[str, str]]:
        """Load recent user/assistant messages for one actor and session."""
        if not self._settings.memory_id:
            return []

        turn_limit = (
            self._settings.max_history_turns if max_turns is None else max_turns
        )
        if turn_limit < 1:
            return []

        try:
            response = self._get_client().list_events(
                memoryId=self._settings.memory_id,
                sessionId=session_id,
                actorId=actor_id,
                includePayloads=True,
                maxResults=turn_limit,
            )
            events = sorted(
                response.get("events", []),
                key=self._event_timestamp,
            )
            messages = self._extract_messages(events[-turn_limit:])
            return messages[-turn_limit * 2 :]
        except Exception:
            logger.exception("Failed to load AgentCore short-term memory")
            return []

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "bedrock-agentcore",
                region_name=self._settings.region,
            )
        return self._client

    @staticmethod
    def _conversation_payload(role: str, text: str) -> dict[str, Any]:
        return {
            "conversational": {
                "content": {"text": text},
                "role": role,
            }
        }

    @staticmethod
    def _metadata_values(
        metadata: Mapping[str, object],
    ) -> dict[str, dict[str, str]]:
        return {key: {"stringValue": str(value)} for key, value in metadata.items()}

    @staticmethod
    def _event_timestamp(event: object) -> datetime:
        if isinstance(event, Mapping):
            timestamp = event.get("eventTimestamp")
            if isinstance(timestamp, datetime):
                if timestamp.tzinfo is None:
                    return timestamp.replace(tzinfo=timezone.utc)
                return timestamp
        return datetime.min.replace(tzinfo=timezone.utc)

    @staticmethod
    def _extract_messages(events: list[object]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for event in events:
            if not isinstance(event, Mapping):
                continue
            payload = event.get("payload", [])
            if not isinstance(payload, list):
                continue

            for item in payload:
                if not isinstance(item, Mapping):
                    continue
                conversational = item.get("conversational")
                if not isinstance(conversational, Mapping):
                    continue
                content = conversational.get("content", {})
                text = content.get("text", "") if isinstance(content, Mapping) else ""
                role = str(conversational.get("role", "")).lower()
                text = str(text).strip()
                if role in {"user", "assistant"} and text:
                    messages.append({"role": role, "content": text})
        return messages


_DEFAULT_SETTINGS = AgentSettings.from_environment()
_client: Any | None = None


def _get_client() -> Any:
    """Return the process-wide AgentCore data-plane client."""
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-agentcore",
            region_name=_DEFAULT_SETTINGS.region,
        )
    return _client


def save_event(
    session_id: str,
    actor_id: str,
    user_message: str,
    assistant_response: str,
    metadata: Mapping[str, object] | None = None,
) -> str | None:
    """Compatibility adapter around :class:`ShortTermMemory`."""
    service = ShortTermMemory(
        _DEFAULT_SETTINGS,
        client=_get_client() if _DEFAULT_SETTINGS.memory_id else None,
    )
    return service.save_turn(
        session_id=session_id,
        actor_id=actor_id,
        user_message=user_message,
        assistant_response=assistant_response,
        metadata=metadata,
    )


def load_session_history(
    session_id: str,
    actor_id: str,
    max_turns: int = 10,
) -> list[dict[str, str]]:
    """Compatibility adapter around :class:`ShortTermMemory`."""
    service = ShortTermMemory(
        _DEFAULT_SETTINGS,
        client=_get_client() if _DEFAULT_SETTINGS.memory_id else None,
    )
    return service.load_history(session_id, actor_id, max_turns)


__all__ = ["ShortTermMemory", "load_session_history", "save_event"]
