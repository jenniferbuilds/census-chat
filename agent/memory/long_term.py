"""AgentCore long-term memory service across managed strategies."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from functools import lru_cache
from typing import Any

import boto3

from settings import AgentSettings

logger = logging.getLogger(__name__)

MemoryStrategy = tuple[str, str]
StrategyProvider = Callable[[], Sequence[MemoryStrategy]]


class LongTermMemory:
    """Retrieve actor-scoped records from active long-term strategies."""

    SECTION_LABELS = {
        "SUMMARIZATION": "Past session summaries",
        "USER_PREFERENCE": "Known user preferences",
        "SEMANTIC": "Previously discussed facts",
        "EPISODIC": "Past research episodes",
    }

    def __init__(
        self,
        settings: AgentSettings,
        data_client: Any | None = None,
        control_client: Any | None = None,
        strategy_provider: StrategyProvider | None = None,
    ) -> None:
        self._settings = settings
        self._data_client = data_client
        self._control_client = control_client
        self._strategy_provider = strategy_provider
        self._strategy_cache: tuple[MemoryStrategy, ...] | None = None

    def retrieve_context(
        self,
        actor_id: str,
        query: str,
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> str:
        """Retrieve and format relevant long-term memories for one actor."""
        if not self._settings.memory_id:
            return ""

        sections: dict[str, list[str]] = {
            strategy_type: [] for strategy_type in self.SECTION_LABELS
        }
        try:
            for strategy_type, strategy_id in self._strategies():
                response = self._get_data_client().retrieve_memory_records(
                    memoryId=self._settings.memory_id,
                    namespacePath=(f"/strategies/{strategy_id}/actors/{actor_id}/"),
                    searchCriteria={
                        "searchQuery": query,
                        "memoryStrategyId": strategy_id,
                        "topK": top_k,
                    },
                    maxResults=top_k,
                )
                self._collect_records(
                    sections=sections,
                    strategy_type=strategy_type,
                    records=response.get("memoryRecordSummaries", []),
                    min_score=min_score,
                )
        except Exception:
            logger.exception("Failed to retrieve AgentCore long-term memory")
            return ""

        return self._format_sections(sections)

    def active_strategies(self) -> tuple[MemoryStrategy, ...]:
        """Return active ``(strategy_type, strategy_id)`` pairs."""
        if not self._settings.memory_id:
            return ()
        if self._strategy_cache is not None:
            return self._strategy_cache

        response = self._get_control_client().get_memory(
            memoryId=self._settings.memory_id
        )
        memory = response.get("memory", response)
        strategies = memory.get("strategies") or memory.get("memoryStrategies") or []
        self._strategy_cache = tuple(
            (
                str(strategy.get("type", "SEMANTIC")),
                str(strategy["strategyId"]),
            )
            for strategy in strategies
            if strategy.get("strategyId")
            and strategy.get("status", "ACTIVE") == "ACTIVE"
        )
        return self._strategy_cache

    def _strategies(self) -> Sequence[MemoryStrategy]:
        if self._strategy_provider is not None:
            return self._strategy_provider()
        return self.active_strategies()

    def _get_data_client(self) -> Any:
        if self._data_client is None:
            self._data_client = boto3.client(
                "bedrock-agentcore",
                region_name=self._settings.region,
            )
        return self._data_client

    def _get_control_client(self) -> Any:
        if self._control_client is None:
            self._control_client = boto3.client(
                "bedrock-agentcore-control",
                region_name=self._settings.region,
            )
        return self._control_client

    @staticmethod
    def _collect_records(
        sections: dict[str, list[str]],
        strategy_type: str,
        records: object,
        min_score: float,
    ) -> None:
        if not isinstance(records, list):
            return

        destination = sections.setdefault(strategy_type, [])
        for record in records:
            if not isinstance(record, Mapping):
                continue
            try:
                score = float(record.get("score", 1.0))
            except (TypeError, ValueError):
                continue
            if score < min_score:
                continue

            content = record.get("content", {})
            text = content.get("text", "") if isinstance(content, Mapping) else ""
            text = str(text).strip()
            if text and text not in destination:
                destination.append(text)

    @classmethod
    def _format_sections(cls, sections: Mapping[str, Sequence[str]]) -> str:
        formatted: list[str] = []
        for strategy_type, texts in sections.items():
            if not texts:
                continue
            label = cls.SECTION_LABELS.get(strategy_type, "Relevant memories")
            formatted.append(f"{label}:\n" + "\n".join(f"- {text}" for text in texts))
        return "\n\n".join(formatted)


_DEFAULT_SETTINGS = AgentSettings.from_environment()
_data_client: Any | None = None
_control_client: Any | None = None


def _get_data_client() -> Any:
    """Return the process-wide AgentCore data-plane client."""
    global _data_client
    if _data_client is None:
        _data_client = boto3.client(
            "bedrock-agentcore",
            region_name=_DEFAULT_SETTINGS.region,
        )
    return _data_client


def _get_control_client() -> Any:
    """Return the process-wide AgentCore control-plane client."""
    global _control_client
    if _control_client is None:
        _control_client = boto3.client(
            "bedrock-agentcore-control",
            region_name=_DEFAULT_SETTINGS.region,
        )
    return _control_client


@lru_cache(maxsize=1)
def _active_strategies() -> tuple[MemoryStrategy, ...]:
    """Compatibility cache for active strategy discovery."""
    service = LongTermMemory(
        _DEFAULT_SETTINGS,
        control_client=(_get_control_client() if _DEFAULT_SETTINGS.memory_id else None),
    )
    return service.active_strategies()


def retrieve_long_term_context(
    actor_id: str,
    query: str,
    top_k: int = 5,
    min_score: float = 0.3,
) -> str:
    """Compatibility adapter around :class:`LongTermMemory`."""
    service = LongTermMemory(
        _DEFAULT_SETTINGS,
        data_client=_get_data_client() if _DEFAULT_SETTINGS.memory_id else None,
        strategy_provider=_active_strategies,
    )
    return service.retrieve_context(actor_id, query, top_k, min_score)


__all__ = ["LongTermMemory", "retrieve_long_term_context"]
