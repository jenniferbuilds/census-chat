"""Validated application configuration for Census Chat."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentSettings:
    """Configuration loaded from environment variables at process startup."""

    region: str = "us-east-1"
    model_id: str = "us.moonshotai.kimi-k3.1"
    knowledge_base_id: str = ""
    knowledge_base_top_k: int = 8
    knowledge_base_min_score: float = 0.25
    guardrail_id: str = ""
    guardrail_version: str = "1"
    memory_id: str = ""
    max_history_turns: int = 10
    max_input_chars: int = 2_000

    def __post_init__(self) -> None:
        if self.knowledge_base_top_k < 1:
            raise ValueError("knowledge_base_top_k must be at least 1")
        if not 0 <= self.knowledge_base_min_score <= 1:
            raise ValueError("knowledge_base_min_score must be between 0 and 1")
        if self.max_history_turns < 1:
            raise ValueError("max_history_turns must be at least 1")
        if self.max_input_chars < 1:
            raise ValueError("max_input_chars must be at least 1")

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "AgentSettings":
        """Build settings from an environment mapping."""
        env = os.environ if environment is None else environment
        defaults = cls()

        return cls(
            region=(
                env.get("AWS_REGION")
                or env.get("AWS_DEFAULT_REGION")
                or defaults.region
            ),
            model_id=env.get("MODEL_ID", defaults.model_id),
            knowledge_base_id=env.get("KNOWLEDGE_BASE_ID", ""),
            knowledge_base_top_k=cls._read_int(
                env,
                "KB_TOP_K",
                defaults.knowledge_base_top_k,
            ),
            knowledge_base_min_score=cls._read_float(
                env,
                "KB_MIN_SCORE",
                defaults.knowledge_base_min_score,
            ),
            guardrail_id=env.get("GUARDRAIL_ID", ""),
            guardrail_version=env.get("GUARDRAIL_VERSION", "1"),
            memory_id=env.get("AGENTCORE_MEMORY_ID", ""),
            max_history_turns=cls._read_int(
                env,
                "MAX_HISTORY_TURNS",
                defaults.max_history_turns,
            ),
            max_input_chars=cls._read_int(
                env,
                "MAX_INPUT_CHARS",
                defaults.max_input_chars,
            ),
        )

    @staticmethod
    def _read_int(environment: Mapping[str, str], key: str, default: int) -> int:
        raw_value = environment.get(key)
        if raw_value is None:
            return default
        try:
            return int(raw_value)
        except ValueError as error:
            raise ValueError(f"{key} must be an integer") from error

    @staticmethod
    def _read_float(environment: Mapping[str, str], key: str, default: float) -> float:
        raw_value = environment.get(key)
        if raw_value is None:
            return default
        try:
            return float(raw_value)
        except ValueError as error:
            raise ValueError(f"{key} must be a number") from error
