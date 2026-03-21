"""Cost tracking for resource budget management.

Implements Welford's online algorithm for running statistics and a
per-operation-type cost / duration tracker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import structlog

log = structlog.get_logger(__name__)


@dataclass
class RunningStats:
    """Welford's online algorithm for running mean and variance.

    Maintains count, mean, and M2 aggregator to compute variance and
    standard deviation in a numerically stable, single-pass fashion.
    """

    count: int = 0
    mean: float = 0.0
    m2: float = 0.0

    def update(self, value: float) -> None:
        """Incorporate a new observation."""
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self.m2 += delta * delta2

    @property
    def variance(self) -> float:
        """Population variance (returns 0.0 when count < 2)."""
        if self.count < 2:
            return 0.0
        return self.m2 / self.count

    @property
    def stddev(self) -> float:
        """Population standard deviation."""
        return math.sqrt(self.variance)


class OperationCostTracker:
    """Tracks per-operation-type cost and duration using running statistics."""

    def __init__(self) -> None:
        self._cost_stats: dict[str, RunningStats] = {}
        self._duration_stats: dict[str, RunningStats] = {}
        self._total_cost: float = 0.0

    # ── Recording ─────────────────────────────────────────────────────────

    def record(
        self,
        operation_type: str,
        cost: float,
        duration_ms: float,
    ) -> None:
        """Record the cost and wall-clock duration of an operation."""
        if operation_type not in self._cost_stats:
            self._cost_stats[operation_type] = RunningStats()
            self._duration_stats[operation_type] = RunningStats()

        self._cost_stats[operation_type].update(cost)
        self._duration_stats[operation_type].update(duration_ms)
        self._total_cost += cost

        log.debug(
            "cost_tracker.recorded",
            operation_type=operation_type,
            cost=cost,
            duration_ms=duration_ms,
            total_cost=self._total_cost,
        )

    # ── Queries ───────────────────────────────────────────────────────────

    def get_stats(self, operation_type: str) -> RunningStats:
        """Return cost RunningStats for *operation_type* (empty stats if unseen)."""
        return self._cost_stats.get(operation_type, RunningStats())

    def get_duration_stats(self, operation_type: str) -> RunningStats:
        """Return duration RunningStats for *operation_type*."""
        return self._duration_stats.get(operation_type, RunningStats())

    def get_total_cost(self) -> float:
        """Return the cumulative cost across all operations."""
        return self._total_cost

    def get_budget_remaining(self, budget: float) -> float:
        """Return how much of *budget* is still available."""
        return max(budget - self._total_cost, 0.0)

    def is_budget_exhausted(self, budget: float) -> bool:
        """Return True when cumulative cost >= *budget*."""
        return self._total_cost >= budget
