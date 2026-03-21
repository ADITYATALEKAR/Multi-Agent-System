"""Base hypothesis strategy interface and shared context."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from src.core.coordination import ResourceBudget
from src.core.derived import DerivedFact


@dataclass
class HypothesisContext:
    """Shared context passed to every hypothesis strategy."""

    reasoning_graph: Any | None = None
    attention_scores: dict[UUID, float] = field(default_factory=dict)
    budget: ResourceBudget = field(default_factory=ResourceBudget)
    tenant_id: str = "default"


class HypothesisStrategy(abc.ABC):
    """Abstract base for all hypothesis-generation strategies.

    STRATEGY_ID: unique string identifying the strategy.
    PRIORITY: lower value = tried first.
    """

    STRATEGY_ID: str
    PRIORITY: int  # lower = tried first

    @abc.abstractmethod
    async def generate(
        self,
        violations: list[DerivedFact],
        context: HypothesisContext,
    ) -> list[DerivedFact]:
        """Generate hypotheses from the given violations.

        Args:
            violations: List of DerivedFact with derived_type == VIOLATION.
            context: Shared hypothesis context (graph, budget, etc.).

        Returns:
            List of DerivedFact with derived_type == HYPOTHESIS.
        """
        ...
