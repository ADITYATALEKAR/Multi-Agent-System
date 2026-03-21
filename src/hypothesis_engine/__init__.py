"""Hypothesis generation: multi-strategy hypothesis engine."""

from __future__ import annotations

from src.hypothesis_engine.aggregator import HypothesisAggregator, StructuralDeduplicator
from src.hypothesis_engine.base import HypothesisContext, HypothesisStrategy
from src.hypothesis_engine.generator import HypothesisGenerator

__all__ = [
    "HypothesisAggregator",
    "HypothesisContext",
    "HypothesisGenerator",
    "HypothesisStrategy",
    "StructuralDeduplicator",
]
