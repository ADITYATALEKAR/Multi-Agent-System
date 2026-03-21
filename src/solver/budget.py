"""Solver budget -- manages time allocation for constraint solving.

Enforces per-call and total time budgets based on problem complexity classes,
preventing runaway solver invocations from consuming excessive resources.
"""

from __future__ import annotations

from enum import Enum

import structlog

logger = structlog.get_logger(__name__)

# Default time allocations per complexity class (milliseconds)
_DEFAULT_ALLOCATIONS: dict[str, float] = {
    "simple": 50.0,
    "moderate": 200.0,
    "complex": 500.0,
}


class ComplexityClass(str, Enum):
    """Solver problem complexity classification."""

    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class SolverBudget:
    """Tracks and allocates solver time budgets based on problem complexity.

    Manages a total time budget (hard limit, default 1000ms) and allocates
    per-call budgets according to the problem's complexity class.
    """

    def __init__(self, total_budget_ms: float = 1000.0) -> None:
        """Initialize budget tracker.

        Args:
            total_budget_ms: Hard limit on total solver time across all calls.
        """
        self._total_budget_ms = total_budget_ms
        self._consumed_ms: float = 0.0
        logger.debug("solver_budget_init", total_budget_ms=total_budget_ms)

    def allocate(self, complexity_class: ComplexityClass | str) -> float:
        """Allocate a time budget in milliseconds for the given complexity class.

        The returned budget is capped by the remaining total budget so the
        hard limit is never exceeded.

        Args:
            complexity_class: Problem complexity -- a ComplexityClass enum value
                or one of the strings 'simple', 'moderate', 'complex'.

        Returns:
            Allocated budget in milliseconds (may be 0.0 if budget exhausted).
        """
        key = complexity_class.value if isinstance(complexity_class, ComplexityClass) else str(complexity_class).lower()
        base_allocation = _DEFAULT_ALLOCATIONS.get(key, _DEFAULT_ALLOCATIONS["simple"])
        remaining = self.remaining_ms()
        allocated = min(base_allocation, remaining)
        logger.debug(
            "budget_allocated",
            complexity_class=key,
            base_ms=base_allocation,
            allocated_ms=allocated,
            remaining_ms=remaining,
        )
        return max(allocated, 0.0)

    def record_usage(self, duration_ms: float) -> None:
        """Record actual solver time consumed.

        Args:
            duration_ms: Wall-clock milliseconds consumed by the solver call.
        """
        self._consumed_ms += duration_ms
        logger.debug(
            "budget_usage_recorded",
            duration_ms=duration_ms,
            total_consumed_ms=self._consumed_ms,
            remaining_ms=self.remaining_ms(),
        )

    def is_exhausted(self) -> bool:
        """Check whether the total budget has been fully consumed.

        Returns:
            True if the total consumed time >= the total budget.
        """
        return self._consumed_ms >= self._total_budget_ms

    def remaining_ms(self) -> float:
        """Return the time remaining in the total budget.

        Returns:
            Milliseconds remaining (non-negative).
        """
        return max(self._total_budget_ms - self._consumed_ms, 0.0)

    @property
    def total_budget_ms(self) -> float:
        """Total budget in milliseconds."""
        return self._total_budget_ms

    @property
    def consumed_ms(self) -> float:
        """Total time consumed so far in milliseconds."""
        return self._consumed_ms
