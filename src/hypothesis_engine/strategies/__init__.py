"""Hypothesis strategies — all concrete implementations and ALL_STRATEGIES list."""

from __future__ import annotations

from src.hypothesis_engine.strategies.cross_service import CrossServiceStrategy
from src.hypothesis_engine.strategies.graph_backward import GraphBackwardStrategy
from src.hypothesis_engine.strategies.law_local import LawLocalStrategy
from src.hypothesis_engine.strategies.llm_assisted import LLMAssistedStrategy
from src.hypothesis_engine.strategies.memory_assisted import MemoryAssistedStrategy
from src.hypothesis_engine.strategies.temporal import TemporalStrategy

ALL_STRATEGIES: list[type] = [
    LawLocalStrategy,
    GraphBackwardStrategy,
    CrossServiceStrategy,
    TemporalStrategy,
    MemoryAssistedStrategy,
    LLMAssistedStrategy,
]

__all__ = [
    "CrossServiceStrategy",
    "GraphBackwardStrategy",
    "LawLocalStrategy",
    "LLMAssistedStrategy",
    "MemoryAssistedStrategy",
    "TemporalStrategy",
    "ALL_STRATEGIES",
]
