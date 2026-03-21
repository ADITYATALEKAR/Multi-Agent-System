"""Memory type definitions — 5 memory types + RepairTemplate.

Phase 3 — Memory subsystem types per v3.1 spec.

Types:
    1. WorkingMemory — active investigation state
    2. Episode — resolved incident record
    3. SemanticRule — extracted generalisation from episodes
    4. Procedure — diagnostic procedure / runbook
    5. Pattern — recurring structural / behavioural pattern
    6. RepairTemplate — proven repair recipe (linked to episodes)
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class MemoryType(str, Enum):
    """Classification of memory entries."""

    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    PATTERN = "pattern"
    REPAIR_TEMPLATE = "repair_template"
    CAUSAL_TEMPLATE = "causal_template"
    WORKING = "working"


class EpisodeOutcome(str, Enum):
    """How the incident was resolved."""

    RESOLVED = "resolved"
    MITIGATED = "mitigated"
    ESCALATED = "escalated"
    FALSE_POSITIVE = "false_positive"


# ---------------------------------------------------------------------------
# 1. WorkingMemory — active investigation state
# ---------------------------------------------------------------------------


class WorkingMemory(BaseModel):
    """Transient memory for an active investigation.

    Holds the current set of violations, hypotheses, attention scores,
    and partial results accumulated during a diagnosis.
    """

    incident_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = "default"
    violations: list[UUID] = Field(default_factory=list)
    hypothesis_ids: list[UUID] = Field(default_factory=list)
    attention_scores: dict[str, float] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# 2. Episode — resolved incident record
# ---------------------------------------------------------------------------


class Episode(BaseModel):
    """A complete record of a resolved incident.

    Episodes are the primary unit of episodic memory.  They capture the
    full lifecycle: trigger violations, hypotheses explored, root cause
    identified, repair applied, and outcome.
    """

    episode_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = "default"
    incident_id: UUID = Field(default_factory=uuid4)
    trigger_violations: list[UUID] = Field(default_factory=list)
    hypotheses_explored: list[UUID] = Field(default_factory=list)
    root_cause_id: Optional[UUID] = None
    repair_actions: list[dict[str, Any]] = Field(default_factory=list)
    outcome: EpisodeOutcome = EpisodeOutcome.RESOLVED
    environment: str = "production"
    region: set[UUID] = Field(default_factory=set)
    law_categories: set[str] = Field(default_factory=set)
    fingerprint: bytes = b""
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    duration_ms: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# 3. SemanticRule — extracted generalisation
# ---------------------------------------------------------------------------


class SemanticRule(BaseModel):
    """A generalised rule extracted from multiple episodes.

    Semantic rules capture invariants like "when service X has > N
    circular dependencies, latency degrades" — learned from repeated
    observations rather than hard-coded laws.
    """

    rule_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = "default"
    description: str
    condition: str  # human-readable or DSL condition
    conclusion: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    supporting_episodes: list[UUID] = Field(default_factory=list)
    region: set[UUID] = Field(default_factory=set)
    environment: str = "production"
    law_categories: set[str] = Field(default_factory=set)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_validated: Optional[datetime] = None
    match_count: int = 0

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# 4. Procedure — diagnostic procedure / runbook
# ---------------------------------------------------------------------------


class ProcedureStep(BaseModel):
    """A single step in a diagnostic procedure."""

    step_id: int
    action: str  # e.g. "check_logs", "query_metrics", "run_test"
    description: str
    expected_outcome: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class Procedure(BaseModel):
    """A reusable diagnostic procedure (runbook).

    Procedures encode ordered sequences of diagnostic steps that have
    proven effective for particular violation patterns.
    """

    procedure_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = "default"
    name: str
    description: str = ""
    steps: list[ProcedureStep] = Field(default_factory=list)
    applicable_patterns: list[bytes] = Field(default_factory=list)
    success_rate: float = Field(ge=0.0, le=1.0, default=0.0)
    invocation_count: int = 0
    avg_duration_ms: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# 5. Pattern — recurring structural / behavioural pattern
# ---------------------------------------------------------------------------


class Pattern(BaseModel):
    """A recurring structural or behavioural pattern.

    Patterns capture fingerprinted sub-graph structures that recur
    across incidents.  They enable fast matching: "we've seen this
    topology before."
    """

    pattern_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = "default"
    name: str = ""
    description: str = ""
    signature: bytes = b""  # WL-hash fingerprint
    exemplar_nodes: list[UUID] = Field(default_factory=list)
    occurrence_count: int = 1
    associated_violations: list[str] = Field(default_factory=list)  # rule_ids
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: Optional[datetime] = None

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# 6. RepairTemplate — proven repair recipe
# ---------------------------------------------------------------------------


class RepairTemplate(BaseModel):
    """A proven repair recipe linked to resolved episodes.

    Repair templates encode the delta operations that successfully
    resolved a class of violations.
    """

    template_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = "default"
    name: str
    description: str = ""
    target_violation_pattern: bytes = b""  # fingerprint of target violations
    repair_steps: list[dict[str, Any]] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    source_episodes: list[UUID] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# MemoryResult — unified query result
# ---------------------------------------------------------------------------


class MemoryResult(BaseModel):
    """Result from a memory query across all types."""

    episodes: list[Episode] = Field(default_factory=list)
    rules: list[SemanticRule] = Field(default_factory=list)
    procedures: list[Procedure] = Field(default_factory=list)
    patterns: list[Pattern] = Field(default_factory=list)
    repair_templates: list[RepairTemplate] = Field(default_factory=list)
    total_matches: int = 0
    query_time_ms: float = 0.0
