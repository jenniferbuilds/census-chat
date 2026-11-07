"""Bedrock Knowledge Base service for US Census documents."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

import boto3

from settings import AgentSettings

logger = logging.getLogger(__name__)


class CensusRetrievalError(RuntimeError):
    """Raised when the Census Knowledge Base cannot be queried."""


class CensusKnowledgeBase:
    """Retrieve and format qualified Census passages from Bedrock."""

    def __init__(self, settings: AgentSettings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    def retrieve(self, query: str, top_k: int | None = None) -> str:
        """Retrieve the most relevant qualified Census passages."""
        if not self._settings.knowledge_base_id:
            raise CensusRetrievalError("Knowledge Base configuration is unavailable")

        result_limit = self._bounded_result_limit(top_k)
        try:
            response = self._get_client().retrieve(
                knowledgeBaseId=self._settings.knowledge_base_id,
                retrievalQuery={"text": query[:2_000]},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": result_limit,
                    }
                },
            )
        except Exception as error:
            logger.exception("Bedrock Knowledge Base retrieval failed")
            raise CensusRetrievalError(
                "The Census data index is temporarily unavailable"
            ) from error

        results = response.get("retrievalResults", [])
        qualified = self._qualified_results(results)
        return self._format_results(qualified)

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = boto3.client(
                "bedrock-agent-runtime",
                region_name=self._settings.region,
            )
        return self._client

    def _bounded_result_limit(self, top_k: int | None) -> int:
        requested = self._settings.knowledge_base_top_k if top_k is None else top_k
        return max(1, min(requested, 20))

    def _qualified_results(self, results: object) -> list[Mapping[str, Any]]:
        if not isinstance(results, list):
            return []
        return [
            result
            for result in results
            if isinstance(result, Mapping)
            and self._score(result) >= self._settings.knowledge_base_min_score
        ]

    @classmethod
    def _format_results(cls, results: Sequence[Mapping[str, Any]]) -> str:
        passages: list[str] = []
        for index, result in enumerate(results, start=1):
            content = result.get("content", {})
            text = content.get("text", "") if isinstance(content, Mapping) else ""
            text = str(text).strip()
            if not text:
                continue

            location = result.get("location", {})
            s3_location = (
                location.get("s3Location", {}) if isinstance(location, Mapping) else {}
            )
            uri = s3_location.get("uri", "") if isinstance(s3_location, Mapping) else ""
            filename = str(uri).rsplit("/", 1)[-1] if uri else "census document"
            passages.append(
                f"[Census source {index}: {filename}; "
                f"relevance {cls._score(result):.2f}]\n{text}"
            )

        return "\n\n---\n\n".join(passages)

    @staticmethod
    def _score(result: Mapping[str, Any]) -> float:
        try:
            return float(result.get("score", 0.0))
        except (TypeError, ValueError):
            return 0.0


_DEFAULT_SETTINGS = AgentSettings.from_environment()
_client: Any | None = None


def _get_client() -> Any:
    """Return the process-wide Bedrock Agent Runtime client."""
    global _client
    if _client is None:
        _client = boto3.client(
            "bedrock-agent-runtime",
            region_name=_DEFAULT_SETTINGS.region,
        )
    return _client


def retrieve_census_context(query: str, top_k: int | None = None) -> str:
    """Compatibility adapter around :class:`CensusKnowledgeBase`."""
    service = CensusKnowledgeBase(
        _DEFAULT_SETTINGS,
        client=_get_client() if _DEFAULT_SETTINGS.knowledge_base_id else None,
    )
    return service.retrieve(query, top_k)


__all__ = [
    "CensusKnowledgeBase",
    "CensusRetrievalError",
    "retrieve_census_context",
]
