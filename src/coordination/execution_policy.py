"""Two-level execution policy enforcement (v3.3 Fix 4).

Implements floor + ranking policy: mandatory operations always execute
within a capped floor budget, then ranked operations fill remaining budget.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from src.core.coordination import ResourceBudget

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class Operation(BaseModel):
    """Represents a single operation to execute."""

    operation_id: UUID = Field(default_factory=uuid4)
    operation_type: str
    agent_id: str
    work_item_id: UUID
    estimated_cost: float = 0.0  # normalised cost 0-1
    priority: float = 0.0
    mandatory: bool = False


# ---------------------------------------------------------------------------
# Floor policy
# ---------------------------------------------------------------------------


class FloorPolicy:
    """Classifies operations as mandatory or ranked."""

    MANDATORY_TYPES: set[str] = {
        "law_check",
        "heartbeat_check",
        "stale_cleanup",
        "security_verify",
    }

    def classify(
        self, operations: list[Operation]
    ) -> tuple[list[Operation], list[Operation]]:
        """Split *operations* into (mandatory, ranked).

        An operation is mandatory when its ``operation_type`` is listed in
        ``MANDATORY_TYPES`` **or** its ``mandatory`` flag is ``True``.
        """
        mandatory: list[Operation] = []
        ranked: list[Operation] = []

        for op in operations:
            if op.operation_type in self.MANDATORY_TYPES or op.mandatory:
                mandatory.append(op)
            else:
                ranked.append(op)

        logger.debug(
            "floor_policy.classify",
            mandatory_count=len(mandatory),
            ranked_count=len(ranked),
        )
        return mandatory, ranked


# ---------------------------------------------------------------------------
# Ranking policy
# ---------------------------------------------------------------------------


class RankingPolicy:
    """Ranks non-mandatory operations by priority then cost."""

    def rank(self, operations: list[Operation]) -> list[Operation]:
        """Return *operations* sorted by priority descending, cost ascending."""
        return sorted(
            operations,
            key=lambda op: (-op.priority, op.estimated_cost),
        )


# ---------------------------------------------------------------------------
# Two-level execution policy (v3.3 Fix 4)
# ---------------------------------------------------------------------------


class ExecutionPolicy:
    """Two-level floor + ranking execution policy.

    1. **Floor** -- mandatory operations always run, capped at
       ``MAX_FLOOR_BUDGET_PCT`` of the total budget.
    2. **Ranking** -- remaining budget is filled with the highest-priority,
       cheapest ranked operations.
    """

    MAX_FLOOR_BUDGET_PCT: float = 0.40  # floor budget <= 40 %

    def __init__(self) -> None:
        self._floor = FloorPolicy()
        self._ranking = RankingPolicy()
        self._log = structlog.get_logger(self.__class__.__name__)

    # -- classification -----------------------------------------------------

    def classify_operations(
        self, operations: list[Operation]
    ) -> tuple[list[Operation], list[Operation]]:
        """Delegate to :class:`FloorPolicy` and return (mandatory, ranked)."""
        return self._floor.classify(operations)

    # -- budget-aware execution list ----------------------------------------

    def execute_with_floors(
        self, operations: list[Operation], total_budget: float
    ) -> list[Operation]:
        """Build an ordered execution list respecting floor and ranking.

        Steps:
            1. Classify into mandatory and ranked.
            2. Floor budget = min(sum-of-mandatory-costs, total_budget * MAX_FLOOR_BUDGET_PCT).
            3. Include mandatory ops that fit within the floor budget.
            4. Remaining budget = total_budget - floor_budget_used.
            5. Fill with ranked ops (by rank order) until budget exhausted.

        Returns:
            Ordered list -- mandatory ops first, then ranked.
        """
        mandatory, ranked = self.classify_operations(operations)
        ranked = self._ranking.rank(ranked)

        # --- floor budget ---
        mandatory_total_cost = sum(op.estimated_cost for op in mandatory)
        floor_budget = min(
            mandatory_total_cost,
            total_budget * self.MAX_FLOOR_BUDGET_PCT,
        )

        accepted_mandatory: list[Operation] = []
        floor_budget_used = 0.0
        for op in mandatory:
            if floor_budget_used + op.estimated_cost <= floor_budget:
                accepted_mandatory.append(op)
                floor_budget_used += op.estimated_cost

        # --- remaining budget for ranked ops ---
        remaining_budget = total_budget - floor_budget_used
        accepted_ranked: list[Operation] = []
        for op in ranked:
            if op.estimated_cost <= remaining_budget:
                accepted_ranked.append(op)
                remaining_budget -= op.estimated_cost

        self._log.info(
            "execute_with_floors",
            mandatory_accepted=len(accepted_mandatory),
            ranked_accepted=len(accepted_ranked),
            floor_budget_used=round(floor_budget_used, 4),
            remaining_budget=round(remaining_budget, 4),
        )
        return accepted_mandatory + accepted_ranked

    # -- single-operation checks --------------------------------------------

    def should_execute(self, operation: Operation) -> bool:
        """Return ``True`` if *operation* should execute.

        Mandatory operations always execute.  Ranked operations execute only
        when their priority is positive.
        """
        if (
            operation.operation_type in FloorPolicy.MANDATORY_TYPES
            or operation.mandatory
        ):
            return True
        return operation.priority > 0

    def get_approval_level(self, operation: Operation) -> str:
        """Return the required approval level for *operation*.

        * ``"auto"`` -- mandatory operations.
        * ``"review"`` -- ranked operations with priority > 0.5.
        * ``"human-in-the-loop"`` -- everything else.
        """
        if (
            operation.operation_type in FloorPolicy.MANDATORY_TYPES
            or operation.mandatory
        ):
            return "auto"
        if operation.priority > 0.5:
            return "review"
        return "human-in-the-loop"

    # -- budget introspection -----------------------------------------------

    def floor_budget_consumed_pct(
        self, operations: list[Operation], total_budget: float
    ) -> float:
        """Return the percentage of *total_budget* consumed by floor ops."""
        if total_budget <= 0:
            return 0.0
        mandatory, _ = self.classify_operations(operations)
        mandatory_cost = sum(op.estimated_cost for op in mandatory)
        return (mandatory_cost / total_budget) * 100.0
