"""Multi-objective repair scoring: J() function.

Combines multiple objectives:
- Effectiveness: probability of fixing the violation
- Risk: probability of introducing regressions
- Complexity: number and type of repair actions
- Precedent: whether similar repairs have worked before
- Coverage: fraction of violations addressed
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from src.repair.planner import RepairTrajectory

logger = structlog.get_logger()


class ScoreComponents(BaseModel):
    """Breakdown of a repair trajectory's score."""

    effectiveness: float = 0.0
    risk_penalty: float = 0.0
    complexity_penalty: float = 0.0
    precedent_bonus: float = 0.0
    coverage_bonus: float = 0.0
    total: float = 0.0


class RepairScorer:
    """Multi-objective scoring function J() for repair trajectories.

    J(t) = w_eff * effectiveness
         - w_risk * risk
         - w_comp * complexity_penalty
         + w_prec * precedent_bonus
         + w_cov * coverage_bonus

    Default weights sum to ~1.0 for normalized output.
    """

    def __init__(
        self,
        w_effectiveness: float = 0.35,
        w_risk: float = 0.25,
        w_complexity: float = 0.15,
        w_precedent: float = 0.10,
        w_coverage: float = 0.15,
    ) -> None:
        self._w_eff = w_effectiveness
        self._w_risk = w_risk
        self._w_comp = w_complexity
        self._w_prec = w_precedent
        self._w_cov = w_coverage

    def score(self, trajectory: RepairTrajectory, context: dict[str, Any] | None = None) -> float:
        """Compute J() for a single trajectory. Updates trajectory.score in place."""
        components = self.score_detailed(trajectory, context)
        trajectory.score = components.total
        return components.total

    def score_detailed(
        self, trajectory: RepairTrajectory, context: dict[str, Any] | None = None
    ) -> ScoreComponents:
        """Compute J() with full component breakdown."""
        context = context or {}

        effectiveness = self._compute_effectiveness(trajectory)
        risk_penalty = self._compute_risk(trajectory)
        complexity_penalty = self._compute_complexity(trajectory)
        precedent_bonus = self._compute_precedent(trajectory, context)
        coverage_bonus = self._compute_coverage(trajectory, context)

        total = (
            self._w_eff * effectiveness
            - self._w_risk * risk_penalty
            - self._w_comp * complexity_penalty
            + self._w_prec * precedent_bonus
            + self._w_cov * coverage_bonus
        )

        return ScoreComponents(
            effectiveness=effectiveness,
            risk_penalty=risk_penalty,
            complexity_penalty=complexity_penalty,
            precedent_bonus=precedent_bonus,
            coverage_bonus=coverage_bonus,
            total=total,
        )

    def score_batch(
        self, trajectories: list[RepairTrajectory], context: dict[str, Any] | None = None
    ) -> list[RepairTrajectory]:
        """Score and sort a batch of trajectories by J() descending."""
        for t in trajectories:
            self.score(t, context)
        return sorted(trajectories, key=lambda t: t.score, reverse=True)

    def _compute_effectiveness(self, trajectory: RepairTrajectory) -> float:
        """Effectiveness = average action confidence weighted by violation count."""
        if not trajectory.actions:
            return 0.0

        avg_conf = sum(a.confidence for a in trajectory.actions) / len(trajectory.actions)
        violation_factor = min(1.0, len(trajectory.violation_ids) / 5.0)
        return avg_conf * (0.7 + 0.3 * violation_factor)

    def _compute_risk(self, trajectory: RepairTrajectory) -> float:
        """Risk = max action risk * count scaling."""
        if not trajectory.actions:
            return 0.0

        max_risk = max(a.risk for a in trajectory.actions)
        count_factor = min(1.0, len(trajectory.actions) / 10.0)
        return max_risk * (0.6 + 0.4 * count_factor)

    def _compute_complexity(self, trajectory: RepairTrajectory) -> float:
        """Complexity penalty based on action count and diversity."""
        if not trajectory.actions:
            return 0.0

        count_penalty = min(1.0, len(trajectory.actions) / 10.0)
        action_types = {a.action_type for a in trajectory.actions}
        diversity_penalty = min(1.0, len(action_types) / 5.0)
        return 0.6 * count_penalty + 0.4 * diversity_penalty

    def _compute_precedent(self, trajectory: RepairTrajectory, context: dict) -> float:
        """Precedent bonus: has a similar repair worked before?"""
        known_successful = context.get("successful_strategies", set())
        if trajectory.strategy in known_successful:
            return 0.8
        # Partial credit for same strategy family
        for known in known_successful:
            if trajectory.strategy.split("_")[0] == known.split("_")[0]:
                return 0.4
        return 0.0

    def _compute_coverage(self, trajectory: RepairTrajectory, context: dict) -> float:
        """Coverage bonus: fraction of total violations addressed."""
        total_violations = context.get("total_violation_count", len(trajectory.violation_ids))
        if total_violations == 0:
            return 0.0
        return min(1.0, len(trajectory.violation_ids) / total_violations)
