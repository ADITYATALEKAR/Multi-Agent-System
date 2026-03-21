"""Value estimation for cost-benefit analysis."""

from __future__ import annotations

import math
from uuid import UUID

import structlog

from src.cost_aware.cost_tracker import OperationCostTracker

log = structlog.get_logger(__name__)


class ValueEstimator:
    """Estimates the expected value of an operation before execution.

    Value is computed as::

        value = (1 / (1 + avg_cost)) * scope_size_factor

    where ``scope_size_factor = log1p(len(scope)) / log1p(100)`` normalises
    scope size to roughly [0, 1].
    """

    def estimate_value(
        self,
        operation_type: str,
        scope: set[UUID],
        tracker: OperationCostTracker,
    ) -> float:
        """Estimate the expected value of running *operation_type* over *scope*.

        Args:
            operation_type: The type of operation being considered.
            scope: Set of entity UUIDs that the operation would cover.
            tracker: Cost tracker with historical cost statistics.

        Returns:
            Estimated value in [0.0, ~1.0].
        """
        stats = tracker.get_stats(operation_type)
        avg_cost = stats.mean if stats.count > 0 else 0.0

        cost_factor = 1.0 / (1.0 + avg_cost)
        scope_factor = math.log1p(len(scope)) / math.log1p(100)

        value = cost_factor * scope_factor
        log.debug(
            "value_estimator.estimate",
            operation_type=operation_type,
            scope_size=len(scope),
            avg_cost=avg_cost,
            value=value,
        )
        return value

    def should_execute(
        self,
        operation_type: str,
        scope: set[UUID],
        tracker: OperationCostTracker,
        threshold: float = 0.1,
    ) -> bool:
        """Return True if estimated value exceeds *threshold*.

        Args:
            operation_type: The type of operation being considered.
            scope: Set of entity UUIDs.
            tracker: Cost tracker with historical statistics.
            threshold: Minimum acceptable value to proceed.

        Returns:
            Whether the operation's value justifies execution.
        """
        value = self.estimate_value(operation_type, scope, tracker)
        decision = value >= threshold

        log.debug(
            "value_estimator.decision",
            operation_type=operation_type,
            value=value,
            threshold=threshold,
            execute=decision,
        )
        return decision
