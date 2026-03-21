"""Diagnosis certificate primitives."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.core.counterfactual import CounterfactualScenario as _CounterfactualScenario

if TYPE_CHECKING:
    from src.core.counterfactual import CounterfactualScenario


class SolverResult(BaseModel):
    """Result from a formal solver invocation."""

    solver_id: str
    query: str
    satisfiable: bool | None = None
    model: dict[str, Any] | None = None
    duration_ms: float = 0.0
    complexity_class: str = "simple"


class ParameterChange(BaseModel):
    """A self-improvement parameter update."""

    parameter: str
    old_value: Any
    new_value: Any
    reason: str = ""


class OSGSubgraph(BaseModel):
    """A snapshot of the Operational State Graph relevant to a diagnosis."""

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class DiagnosisCertificate(BaseModel):
    """Complete audit trail of a diagnostic investigation.

    v3.2: budget_gated_operations, self_improvement_updates,
          simulation_boundary_sizes, temporal_index_queries.
    v3.3 Fix 3: law_health_states.
    v3.3 Fix 4: mandatory_ops_executed, floor_budget_consumed_pct.
    """

    certificate_id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    root_cause_hypothesis_ids: list[UUID] = Field(default_factory=list)
    supporting_evidence: list[UUID] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    counterfactual_results: list[CounterfactualScenario] = Field(default_factory=list)
    solver_results: list[SolverResult] = Field(default_factory=list)
    attention_scores_at_diagnosis: dict[str, float] = Field(default_factory=dict)
    osg_snapshot: OSGSubgraph = Field(default_factory=OSGSubgraph)
    repair_plan_ids: list[UUID] = Field(default_factory=list)
    # v3.2 additions
    budget_gated_operations: list[str] = Field(default_factory=list)
    self_improvement_updates: list[ParameterChange] = Field(default_factory=list)
    simulation_boundary_sizes: list[int] = Field(default_factory=list)
    temporal_index_queries: int = 0
    # v3.3 Fix 3
    law_health_states: dict[str, str] = Field(default_factory=dict)
    # v3.3 Fix 4
    mandatory_ops_executed: list[str] = Field(default_factory=list)
    floor_budget_consumed_pct: float = 0.0


DiagnosisCertificate.model_rebuild(
    _types_namespace={"CounterfactualScenario": _CounterfactualScenario}
)
