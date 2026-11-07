"""Bedrock Guardrail service for mandatory input and output checks."""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any

import boto3

from settings import AgentSettings

logger = logging.getLogger(__name__)


class GuardrailSource(StrEnum):
    """Content sources accepted by the Bedrock Guardrail API."""

    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


class GuardrailBlockedError(Exception):
    """Raised when Bedrock Guardrails intervenes."""

    def __init__(self, user_message: str, action: str = "GUARDRAIL_INTERVENED") -> None:
        super().__init__(user_message)
        self.action = action
        self.user_message = user_message


class GuardrailServiceError(RuntimeError):
    """Raised when a mandatory guardrail evaluation cannot run."""


class BedrockGuardrail:
    """Evaluate content with one configured Bedrock Guardrail."""

    DEFAULT_BLOCKED_MESSAGE = (
        "I can only answer grounded questions about US Census data. "
        "Please ask about population, demographics, income, housing, or employment."
    )

    def __init__(self, settings: AgentSettings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    def validate(
        self,
        text: str,
        source: GuardrailSource | str = GuardrailSource.INPUT,
        *,
        grounding_source: str | None = None,
        query: str | None = None,
    ) -> str:
        """Return text when allowed; raise when blocked or unavailable."""
        if not self._settings.guardrail_id:
            raise GuardrailServiceError("Guardrail configuration is unavailable")

        source_value = self._normalize_source(source)
        content = self._build_content(
            text=text,
            source=source_value,
            grounding_source=grounding_source,
            query=query,
        )

        try:
            response = self._get_client().apply_guardrail(
                guardrailIdentifier=self._settings.guardrail_id,
                guardrailVersion=self._settings.guardrail_version,
                source=source_value,
                content=content,
                outputScope="INTERVENTIONS",
            )
        except Exception as error:
            logger.exception("Bedrock Guardrail evaluation failed")
            raise GuardrailServiceError(
                "Safety validation is temporarily unavailable"
            ) from error

        action = response.get("action", "NONE")
        if action != "GUARDRAIL_INTERVENED":
            return text

        logger.info("Guardrail intervened on %s content", source_value)
        raise GuardrailBlockedError(
            user_message=self._blocked_message(response),
            action=action,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=self._settings.region,
            )
        return self._client

    @staticmethod
    def _normalize_source(source: GuardrailSource | str) -> str:
        try:
            return GuardrailSource(source).value
        except ValueError as error:
            allowed = ", ".join(item.value for item in GuardrailSource)
            raise ValueError(f"source must be one of: {allowed}") from error

    @staticmethod
    def _build_content(
        text: str,
        source: str,
        grounding_source: str | None,
        query: str | None,
    ) -> list[dict[str, Any]]:
        if source == GuardrailSource.OUTPUT and grounding_source and query:
            return [
                {
                    "text": {
                        "text": grounding_source[:20_000],
                        "qualifiers": ["grounding_source"],
                    }
                },
                {
                    "text": {
                        "text": query[:2_000],
                        "qualifiers": ["query"],
                    }
                },
                {
                    "text": {
                        "text": text[:10_000],
                        "qualifiers": ["guard_content"],
                    }
                },
            ]
        return [{"text": {"text": text[:10_000]}}]

    @classmethod
    def _blocked_message(cls, response: dict[str, Any]) -> str:
        outputs = response.get("outputs", [])
        if outputs and isinstance(outputs[0], dict):
            message = outputs[0].get("text")
            if message:
                return str(message)
        return cls.DEFAULT_BLOCKED_MESSAGE


_DEFAULT_SETTINGS = AgentSettings.from_environment()
_bedrock_client: Any | None = None


def _get_client() -> Any:
    """Return the process-wide Bedrock Runtime client."""
    global _bedrock_client
    if _bedrock_client is None:
        _bedrock_client = boto3.client(
            "bedrock-runtime",
            region_name=_DEFAULT_SETTINGS.region,
        )
    return _bedrock_client


def apply_guardrail(
    text: str,
    source: str = "INPUT",
    *,
    grounding_source: str | None = None,
    query: str | None = None,
) -> str:
    """Compatibility adapter around :class:`BedrockGuardrail`."""
    service = BedrockGuardrail(
        _DEFAULT_SETTINGS,
        client=_get_client() if _DEFAULT_SETTINGS.guardrail_id else None,
    )
    return service.validate(
        text,
        source,
        grounding_source=grounding_source,
        query=query,
    )


__all__ = [
    "BedrockGuardrail",
    "GuardrailBlockedError",
    "GuardrailServiceError",
    "GuardrailSource",
    "apply_guardrail",
]
