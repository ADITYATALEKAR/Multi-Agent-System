"""Hypothesis engine: multi-strategy hypothesis generation and aggregation.

Provides six hypothesis strategies (four functional, two Phase-5 stubs) and a
HypothesisAggregator that deduplicates, merges, and ranks hypotheses by
confidence.

Strategies
----------
- LawLocalStrategy       -- hypotheses from grouped law violations
- GraphBackwardStrategy  -- backward dependency-graph traversal
- CrossServiceStrategy   -- cross-service violation correlation
- TemporalStrategy       -- temporally correlated violations
- MemoryAssistedStrategy -- Phase 5 stub (episodic memory)
- LLMAssistedStrategy    -- Phase 5 stub (LLM-aided reasoning)
"""

from __future__ import annotations

from src.hypothesis.aggregator import HypothesisAggregator
from src.hypothesis.hypothesis import Hypothesis, HypothesisStatus
from src.hypothesis.strategies import (
    BaseStrategy,
    CrossServiceStrategy,
    GraphBackwardStrategy,
    LawLocalStrategy,
    LLMAssistedStrategy,
    MemoryAssistedStrategy,
    TemporalStrategy,
)

__all__ = [
    "BaseStrategy",
    "CrossServiceStrategy",
    "GraphBackwardStrategy",
    "Hypothesis",
    "HypothesisAggregator",
    "HypothesisStatus",
    "LawLocalStrategy",
    "LLMAssistedStrategy",
    "MemoryAssistedStrategy",
    "TemporalStrategy",
]
