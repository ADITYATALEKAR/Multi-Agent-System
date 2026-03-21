"""Energy-based scoring — HealthVector computation and blast-radius analysis."""

from __future__ import annotations

import math
from typing import Any

import structlog
from pydantic import BaseModel, Field

from src.core.derived import DerivedFact

log = structlog.get_logger(__name__)

# Default dimension weights for the energy function.
_DIMENSION_WEIGHTS: dict[str, float] = {
    "structural": 0.25,
    "dependency": 0.25,
    "security": 0.20,
    "complexity": 0.15,
    "style": 0.15,
}


class HealthVector(BaseModel):
    """Multi-dimensional health assessment of a codebase region."""

    overall_score: float = Field(
        ge=0.0, le=1.0, default=1.0,
        description="0.0 = broken, 1.0 = healthy",
    )
    dimension_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Per-category scores (structural, dependency, etc.)",
    )
    violation_count: int = 0
    critical_violation_count: int = 0
    blast_radius_score: float = Field(
        ge=0.0, le=1.0, default=0.0,
        description="0.0 = localised, 1.0 = system-wide impact",
    )


class BlastRadiusComputer:
    """Estimates the blast radius of a set of violations.

    Blast radius measures how far the impact of violations can propagate
    through the dependency graph.  Without a graph, a simple heuristic
    based on the number and severity of violations is used.
    """

    def compute(
        self,
        violations: list[DerivedFact],
        graph_context: Any = None,
    ) -> float:
        """Return a blast-radius score in [0.0, 1.0].

        With a graph, the score is the fraction of reachable nodes from
        violation entities.  Without one, we fall back to a logarithmic
        scaling of violation count and confidence.
        """
        if not violations:
            return 0.0

        if graph_context is not None and hasattr(graph_context, "node_count"):
            total_nodes = max(graph_context.node_count, 1)
            affected: set[str] = set()
            for v in violations:
                eid = v.payload.get("subject_id", v.payload.get("entity_id"))
                if eid:
                    affected.add(str(eid))
                    # If graph exposes reachable nodes, use them.
                    if hasattr(graph_context, "get_reachable"):
                        for r in graph_context.get_reachable(str(eid)):
                            affected.add(str(r))
            return min(len(affected) / total_nodes, 1.0)

        # Heuristic fallback: log-scaled count weighted by avg confidence.
        avg_conf = sum(v.confidence for v in violations) / len(violations)
        raw = math.log1p(len(violations)) / math.log1p(50)  # 50 violations -> ~1.0
        return min(raw * avg_conf, 1.0)


def _classify_dimension(rule_id: str) -> str:
    """Map a rule_id to a scoring dimension via simple prefix matching."""
    rule_lower = rule_id.lower()
    if any(k in rule_lower for k in ("struct", "naming", "layout")):
        return "structural"
    if any(k in rule_lower for k in ("dep", "import", "cycle")):
        return "dependency"
    if any(k in rule_lower for k in ("sec", "vuln", "auth", "cred")):
        return "security"
    if any(k in rule_lower for k in ("complex", "loc", "cognitive")):
        return "complexity"
    if any(k in rule_lower for k in ("style", "format", "lint")):
        return "style"
    return "structural"  # default bucket


class EnergyScorer:
    """Computes a HealthVector from a set of violations.

    Each violation's impact is weighted by its law weight (from
    ``payload.get("law_weight", 1.0)``) and its confidence score.
    """

    def __init__(
        self,
        dimension_weights: dict[str, float] | None = None,
    ) -> None:
        self._weights = dimension_weights or dict(_DIMENSION_WEIGHTS)
        self._blast = BlastRadiusComputer()

    def compute(
        self,
        violations: list[DerivedFact],
        rg: Any = None,
    ) -> HealthVector:
        """Compute health across dimensions.

        Args:
            violations: DerivedFact list with derived_type == VIOLATION.
            rg: Optional reasoning graph for blast-radius computation.

        Returns:
            Populated HealthVector.
        """
        if not violations:
            return HealthVector(
                overall_score=1.0,
                dimension_scores={d: 1.0 for d in self._weights},
                violation_count=0,
                critical_violation_count=0,
                blast_radius_score=0.0,
            )

        # Accumulate penalty per dimension.
        dimension_penalties: dict[str, float] = {d: 0.0 for d in self._weights}
        dimension_counts: dict[str, int] = {d: 0 for d in self._weights}
        critical_count = 0

        for v in violations:
            dim = _classify_dimension(v.justification.rule_id)
            if dim not in dimension_penalties:
                dim = "structural"

            law_weight = v.payload.get("law_weight", 1.0)
            penalty = v.confidence * law_weight
            dimension_penalties[dim] += penalty
            dimension_counts[dim] += 1

            if v.payload.get("severity") == "critical" or law_weight >= 1.5:
                critical_count += 1

        # Convert penalties to scores in [0.0, 1.0].
        dimension_scores: dict[str, float] = {}
        for dim, pen in dimension_penalties.items():
            # Sigmoid-like decay: score = 1 / (1 + penalty)
            dimension_scores[dim] = 1.0 / (1.0 + pen)

        # Weighted average across dimensions.
        total_weight = sum(self._weights.values())
        overall = sum(
            dimension_scores.get(d, 1.0) * w
            for d, w in self._weights.items()
        ) / max(total_weight, 1e-9)
        overall = max(0.0, min(overall, 1.0))

        blast = self._blast.compute(violations, rg)

        # Penalise overall score by blast radius.
        overall = overall * (1.0 - 0.3 * blast)
        overall = max(0.0, min(overall, 1.0))

        hv = HealthVector(
            overall_score=round(overall, 4),
            dimension_scores={d: round(s, 4) for d, s in dimension_scores.items()},
            violation_count=len(violations),
            critical_violation_count=critical_count,
            blast_radius_score=round(blast, 4),
        )
        log.info(
            "energy.computed",
            overall=hv.overall_score,
            violations=hv.violation_count,
            critical=hv.critical_violation_count,
            blast_radius=hv.blast_radius_score,
        )
        return hv
