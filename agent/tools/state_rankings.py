"""Deterministic state-level ranking tool for Census metrics."""

from __future__ import annotations

import os
from typing import Any

from strands import tool

from settings import AgentSettings
from tools.census_retrieve import CensusKnowledgeBase


def _default_knowledge_base() -> CensusKnowledgeBase:
    return CensusKnowledgeBase(AgentSettings.from_environment())


@tool
def rank_states_by_homeownership(top_n: int = 10) -> str:
    """Rank U.S. states by homeownership rate from the Census index.

    Use this tool whenever the user asks which states have the highest or
    lowest homeownership rates. Always prefer this deterministic ranking over
    inferring from partial retrieval results.

    Args:
        top_n: Number of states to return (default 10).

    Returns:
        A Markdown table of the top states by homeownership rate.
    """
    kb = _default_knowledge_base()
    context = kb.retrieve("homeownership rate state", top_k=50)

    # Parse state-level documents and extract homeownership rate
    states: list[tuple[str, float]] = []
    for passage in context.split("\n\n---\n\n"):
        if "LEVEL: State" not in passage:
            continue
        for line in passage.splitlines():
            if "Homeownership Rate:" in line:
                rate_text = line.split(":", 1)[1].strip().rstrip("%")
                try:
                    rate = float(rate_text)
                    state_name = passage.split("GEOGRAPHY: ")[1].split(" (")[0]
                    states.append((state_name, rate))
                except (ValueError, IndexError):
                    continue

    states.sort(key=lambda item: item[1], reverse=True)
    top = states[: max(1, min(top_n, 20))]

    if not top:
        return "No state-level homeownership data found in the Census index."

    rows = [
        f"| Rank | State | Homeownership Rate (2020) |",
        f"|------|-------|---------------------------|",
    ]
    for rank, (state, rate) in enumerate(top, 1):
        rows.append(f"| {rank} | {state} | {rate:.1f}% |")

    return "\n".join(rows) + "\n\nSource: 2020 ACS 5-year estimates."
