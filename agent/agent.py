"""Amazon Bedrock AgentCore Runtime entry point for Census Chat."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent, tool
from tools.state_rankings import rank_states_by_homeownership

from guardrails import (
    BedrockGuardrail,
    GuardrailBlockedError,
    GuardrailServiceError,
    GuardrailSource,
    apply_guardrail,
)
from memory.long_term import LongTermMemory, retrieve_long_term_context
from memory.short_term import ShortTermMemory, load_session_history, save_event
from prompts import ConversationPromptBuilder, SYSTEM_PROMPT
from settings import AgentSettings
from tools.census_retrieve import (
    CensusKnowledgeBase,
    CensusRetrievalError,
    retrieve_census_context,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class InvocationContext(Protocol):
    """Subset of AgentCore invocation context used by the application."""

    session_id: str


class GuardrailPort(Protocol):
    def validate(
        self,
        text: str,
        source: GuardrailSource | str,
        *,
        grounding_source: str | None = None,
        query: str | None = None,
    ) -> str: ...


class ShortTermMemoryPort(Protocol):
    def load_history(
        self,
        session_id: str,
        actor_id: str,
        max_turns: int | None = None,
    ) -> list[dict[str, str]]: ...

    def save_turn(
        self,
        session_id: str,
        actor_id: str,
        user_message: str,
        assistant_response: str,
        metadata: Mapping[str, object] | None = None,
    ) -> str | None: ...


class LongTermMemoryPort(Protocol):
    def retrieve_context(self, actor_id: str, query: str) -> str: ...


class CensusKnowledgeBasePort(Protocol):
    def retrieve(self, query: str) -> str: ...


@tool
def calculate_year_over_year(
    metric: str,
    geography: str,
    year1: int,
    year2: int,
    value1: float,
    value2: float,
) -> str:
    """Calculate absolute and percent change between two years for a metric.

    Use this tool whenever the user asks about trends, growth, decline, or
    year-over-year comparisons. Always pass the exact values from the Census data.

    Args:
        metric: The metric being compared (e.g., "poverty rate", "median household income")
        geography: The geographic area (e.g., "Cook County, Illinois")
        year1: The earlier year (e.g., 2016)
        year2: The later year (e.g., 2020)
        value1: The metric value in year1 (e.g., 14.1)
        value2: The metric value in year2 (e.g., 13.2)

    Returns:
        A formatted string with the change, percent change, and trend direction.
    """
    if value1 is None or value2 is None:
        return f"Insufficient data to calculate {metric} change for {geography}."

    change = value2 - value1
    if abs(change) < 0.05:
        trend = "remained roughly unchanged"
        pct = 0.0
    elif value1 != 0:
        pct = (change / abs(value1)) * 100
        trend = "increased" if change > 0 else "decreased"
    else:
        pct = 0.0
        trend = "changed"

    sign = "+" if change > 0 else ""
    pct_sign = "+" if pct > 0 else ""
    return (
        f"{metric.title()} in {geography} {trend} from {value1:,.1f} ({year1}) to "
        f"{value2:,.1f} ({year2}), a change of {sign}{change:,.1f} "
        f"({pct_sign}{pct:.1f}%)."
    )


@tool
def compare_geographies(
    metric: str,
    year: int,
    geography1: str,
    value1: float,
    geography2: str,
    value2: float,
) -> str:
    """Compare the same metric between two geographies for a given year.

    Use this tool when the user asks to compare one metric between two
    counties, states, or other geographies. Always pass the exact values from
    the Census data.

    Args:
        metric: The metric being compared (e.g., "median household income")
        year: The year of the data (e.g., 2020)
        geography1: First geography (e.g., "Cook County, Illinois")
        value1: Metric value for geography1 (e.g., 77072)
        geography2: Second geography (e.g., "Mississippi")
        value2: Metric value for geography2 (e.g., 45116)

    Returns:
        A formatted string comparing the two geographies.
    """
    if value1 is None or value2 is None:
        return f"Insufficient data to compare {metric} between {geography1} and {geography2}."

    if value1 > value2:
        diff = value1 - value2
        pct = (diff / abs(value2)) * 100 if value2 != 0 else 0
        result = (
            f"{metric.title()} in {geography1} is {diff:,.1f} higher than {geography2} "
            f"({value1:,.1f} vs {value2:,.1f}), a {pct:.1f}% difference."
        )
    elif value1 < value2:
        diff = value2 - value1
        pct = (diff / abs(value1)) * 100 if value1 != 0 else 0
        result = (
            f"{metric.title()} in {geography1} is {diff:,.1f} lower than {geography2} "
            f"({value1:,.1f} vs {value2:,.1f}), a {pct:.1f}% difference."
        )
    else:
        result = (
            f"{metric.title()} is the same in {geography1} and {geography2} "
            f"({value1:,.1f} in {year})."
        )
    return result


class InvalidInvocationError(ValueError):
    """Raised when an invocation payload is invalid."""


@dataclass(frozen=True, slots=True)
class ChatTurn:
    """Validated identifiers and content for one chat turn."""

    message: str
    actor_id: str
    session_id: str

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        context: InvocationContext,
    ) -> "ChatTurn":
        if not isinstance(payload, Mapping):
            raise InvalidInvocationError("request body must be a JSON object")

        message = str(payload.get("message", "")).strip()
        context_session_id = getattr(context, "session_id", None)
        session_id = cls._safe_identifier(
            context_session_id or payload.get("session_id"),
            fallback="session-unavailable",
        )
        actor_id = cls._safe_identifier(
            payload.get("actor_id"),
            fallback="authenticated-user",
        )

        if not message:
            raise InvalidInvocationError("message is required")

        return cls(message=message, actor_id=actor_id, session_id=session_id)

    @staticmethod
    def _safe_identifier(value: object, fallback: str) -> str:
        text = str(value or "").strip()
        if (
            not text
            or len(text) > 256
            or re.fullmatch(r"[A-Za-z0-9._:-]+", text) is None
        ):
            return fallback
        return text


class CensusChatAgent:
    """Coordinate safety, memory, retrieval, and grounded generation."""

    def __init__(
        self,
        settings: AgentSettings,
        guardrail: GuardrailPort | None = None,
        short_term_memory: ShortTermMemoryPort | None = None,
        long_term_memory: LongTermMemoryPort | None = None,
        knowledge_base: CensusKnowledgeBasePort | None = None,
        prompt_builder: ConversationPromptBuilder | None = None,
        agent_factory: Callable[..., Any] = Agent,
    ) -> None:
        self._settings = settings
        self._guardrail = (
            guardrail if guardrail is not None else BedrockGuardrail(settings)
        )
        self._short_term_memory = (
            short_term_memory
            if short_term_memory is not None
            else ShortTermMemory(settings)
        )
        self._long_term_memory = (
            long_term_memory
            if long_term_memory is not None
            else LongTermMemory(settings)
        )
        self._knowledge_base = (
            knowledge_base
            if knowledge_base is not None
            else CensusKnowledgeBase(settings)
        )
        self._prompt_builder = prompt_builder or ConversationPromptBuilder()
        self._agent_factory = agent_factory

    def handle(
        self,
        payload: Mapping[str, Any],
        context: InvocationContext,
    ) -> dict[str, Any]:
        """Process one authenticated chat turn."""
        try:
            turn = ChatTurn.from_payload(payload, context)
        except InvalidInvocationError as error:
            session_id = ChatTurn._safe_identifier(
                getattr(context, "session_id", None),
                fallback="session-unavailable",
            )
            return {
                "error": str(error),
                "status": 400,
                "session_id": session_id,
            }

        if len(turn.message) > self._settings.max_input_chars:
            return {
                "response": (
                    "Please shorten your question to "
                    f"{self._settings.max_input_chars:,} characters or fewer."
                ),
                "session_id": turn.session_id,
                "blocked": True,
            }

        logger.info(
            "Processing chat turn for actor=%s session=%s",
            turn.actor_id,
            turn.session_id,
        )

        input_result = self._validate_input(turn)
        if input_result is not None:
            return input_result

        history = self._short_term_memory.load_history(
            turn.session_id,
            turn.actor_id,
            max_turns=self._settings.max_history_turns,
        )
        long_term_context = self._long_term_memory.retrieve_context(
            turn.actor_id,
            turn.message,
        )
        retrieval_query = self._prompt_builder.retrieval_query(
            turn.message,
            history,
        )

        try:
            census_context = self._knowledge_base.retrieve(retrieval_query)
        except CensusRetrievalError:
            return self._degraded_response(
                "I'm having trouble reaching the Census data index. "
                "Please try again in a moment.",
                turn.session_id,
            )

        try:
            response_text = self._generate_response(
                user_message=turn.message,
                census_context=census_context,
                session_history=history,
                long_term_context=long_term_context,
            )
        except Exception:
            logger.exception("Census model generation failed")
            return self._degraded_response(
                "I couldn't generate an answer from the retrieved Census data "
                "right now. Please try again.",
                turn.session_id,
            )

        output_result = self._validate_output(
            turn=turn,
            response_text=response_text,
            census_context=census_context,
        )
        if isinstance(output_result, dict):
            return output_result
        response_text = output_result

        self._short_term_memory.save_turn(
            session_id=turn.session_id,
            actor_id=turn.actor_id,
            user_message=turn.message,
            assistant_response=response_text,
            metadata={
                "application": "census-chat",
                "model": self._settings.model_id,
            },
        )

        return {
            "response": response_text,
            "session_id": turn.session_id,
            "blocked": False,
        }

    def _validate_input(self, turn: ChatTurn) -> dict[str, Any] | None:
        try:
            self._guardrail.validate(turn.message, GuardrailSource.INPUT)
        except GuardrailBlockedError as error:
            return {
                "response": error.user_message,
                "session_id": turn.session_id,
                "blocked": True,
            }
        except GuardrailServiceError:
            return self._degraded_response(
                "I can't safely process that request because the safety service "
                "is temporarily unavailable. Please try again.",
                turn.session_id,
            )
        return None

    def _generate_response(
        self,
        user_message: str,
        census_context: str,
        session_history: list[dict[str, str]],
        long_term_context: str,
    ) -> str:
        model_agent = self._agent_factory(
            model=self._settings.model_id,
            system_prompt=SYSTEM_PROMPT,
            tools=[calculate_year_over_year, compare_geographies, rank_states_by_homeownership],
        )
        prompt = self._prompt_builder.build(
            user_message=user_message,
            census_context=census_context,
            session_history=session_history,
            long_term_context=long_term_context,
        )
        response_text = str(model_agent(prompt)).strip()
        if not response_text:
            raise RuntimeError("The model returned an empty response")
        return response_text

    def _validate_output(
        self,
        turn: ChatTurn,
        response_text: str,
        census_context: str,
    ) -> str | dict[str, Any]:
        try:
            self._guardrail.validate(
                response_text,
                GuardrailSource.OUTPUT,
                grounding_source=census_context,
                query=turn.message,
            )
        except GuardrailBlockedError as error:
            return error.user_message
        except GuardrailServiceError:
            return self._degraded_response(
                "I found relevant Census data, but couldn't complete the required "
                "safety check. Please try again.",
                turn.session_id,
            )
        return response_text

    @staticmethod
    def _degraded_response(message: str, session_id: str) -> dict[str, Any]:
        return {
            "response": message,
            "session_id": session_id,
            "degraded": True,
        }


class _FunctionGuardrailAdapter:
    """Resolve the module adapter at call time for runtime compatibility."""

    def validate(
        self,
        text: str,
        source: GuardrailSource | str,
        *,
        grounding_source: str | None = None,
        query: str | None = None,
    ) -> str:
        return apply_guardrail(
            text,
            str(source),
            grounding_source=grounding_source,
            query=query,
        )


class _FunctionShortTermMemoryAdapter:
    def load_history(
        self,
        session_id: str,
        actor_id: str,
        max_turns: int | None = None,
    ) -> list[dict[str, str]]:
        return load_session_history(
            session_id,
            actor_id,
            max_turns=max_turns or 10,
        )

    def save_turn(
        self,
        session_id: str,
        actor_id: str,
        user_message: str,
        assistant_response: str,
        metadata: Mapping[str, object] | None = None,
    ) -> str | None:
        return save_event(
            session_id=session_id,
            actor_id=actor_id,
            user_message=user_message,
            assistant_response=assistant_response,
            metadata=metadata,
        )


class _FunctionLongTermMemoryAdapter:
    def retrieve_context(self, actor_id: str, query: str) -> str:
        return retrieve_long_term_context(actor_id, query)


class _FunctionKnowledgeBaseAdapter:
    def retrieve(self, query: str) -> str:
        return retrieve_census_context(query)


def _create_strands_agent(**kwargs: Any) -> Any:
    return Agent(
        **kwargs,
        callback_handler=None,
        # Disable tool input verification to allow free-form calculation calls.
        # The tools validate their own numeric inputs.
    )


settings = AgentSettings.from_environment()
census_agent = CensusChatAgent(
    settings=settings,
    guardrail=_FunctionGuardrailAdapter(),
    short_term_memory=_FunctionShortTermMemoryAdapter(),
    long_term_memory=_FunctionLongTermMemoryAdapter(),
    knowledge_base=_FunctionKnowledgeBaseAdapter(),
    agent_factory=_create_strands_agent,
)
app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict[str, Any], context: InvocationContext) -> dict[str, Any]:
    """Adapt AgentCore Runtime requests to the application service."""
    return census_agent.handle(payload, context)


if __name__ == "__main__":
    logger.info(
        "Starting Census Chat AgentCore server with model %s",
        settings.model_id,
    )
    app.run(port=8080)
