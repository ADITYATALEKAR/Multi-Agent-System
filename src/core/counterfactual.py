"""Counterfactual reasoning primitives: CounterfactualScenario, Intervention."""

from __future__ import annotations

import enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.core.fact import DeltaOp, GraphDelta


class InterventionType(str, enum.Enum):
    REMOVE_DELTA = "remove_delta"
    MODIFY_DELTA = "modify_delta"
    INJECT_DELTA = "inject_delta"


class CounterfactualConclusion(str, enum.Enum):
    CAUSES_SYMPTOM = "causes_symptom"
    DOES_NOT_CAUSE = "does_not_cause"
    INCONCLUSIVE = "inconclusive"


class Intervention(BaseModel):
    """A counterfactual intervention on the delta stream."""

    intervention_type: InterventionType
    target_deltas: list[UUID] = Field(default_factory=list)
    replacement: Optional[list[DeltaOp]] = None


class CounterfactualScenario(BaseModel):
    """Result of a counterfactual simulation.

    v3.3 Fix 2: boundary_size, expansion_count, expansion_triggers tracked.
    """

    scenario_id: UUID = Field(default_factory=uuid4)
    base_state_checkpoint: int
    intervention: Intervention
    replayed_deltas: list[GraphDelta] = Field(default_factory=list)
    resulting_violations: set[UUID] = Field(default_factory=set)
    resulting_health_delta: float = 0.0
    conclusion: CounterfactualConclusion = CounterfactualConclusion.INCONCLUSIVE
    boundary_size: int = 0  # v3.3 Fix 2
    expansion_count: int = 0  # v3.3 Fix 2
    expansion_triggers: list[str] = Field(default_factory=list)  # v3.3 Fix 2
