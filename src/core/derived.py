"""Derived fact primitives: DerivedFact, ExtendedJustification, ConfidenceContribution."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DerivedType(str, enum.Enum):
    VIOLATION = "violation"
    HYPOTHESIS = "hypothesis"
    CAUSAL_EDGE = "causal_edge"
    REPAIR_SCORE = "repair_score"
    HEALTH_AGGREGATE = "health_aggregate"
    PATTERN_MATCH = "pattern_match"
    BLAST_RADIUS = "blast_radius"
    COUNTERFACTUAL_RESULT = "counterfactual_result"


class DerivedStatus(str, enum.Enum):
    SUPPORTED = "supported"
    RETRACTED = "retracted"
    UNKNOWN = "unknown"
    COMPETING = "competing"


class ConfidenceSource(str, enum.Enum):
    EVIDENCE = "evidence"
    MEMORY_MATCH = "memory_match"
    GENERATOR_DIVERSITY = "generator_diversity"
    TMS_PROPAGATION = "tms_propagation"
    COUNTERFACTUAL_VALIDATION = "counterfactual_validation"


class ConfidenceContribution(BaseModel):
    """A single source of confidence for a derived fact."""

    source: ConfidenceSource
    weight: float = Field(ge=0.0, le=1.0)
    detail: str = ""


class ExtendedJustification(BaseModel):
    """Full justification chain for a derived fact."""

    justification_id: UUID = Field(default_factory=uuid4)
    rule_id: str
    supporting_facts: set[UUID] = Field(default_factory=set)
    contradicting_facts: set[UUID] = Field(default_factory=set)
    monotonic: bool = True
    confidence_weight: float = Field(ge=0.0, le=1.0, default=1.0)
    source_strategy: str = ""


class DerivedFact(BaseModel):
    """A fact derived by the reasoning engine, with full provenance."""

    derived_id: UUID = Field(default_factory=uuid4)
    derived_type: DerivedType
    payload: dict  # TypedPayload — schema depends on derived_type
    justification: ExtendedJustification
    status: DerivedStatus = DerivedStatus.UNKNOWN
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    confidence_sources: list[ConfidenceContribution] = Field(default_factory=list)
    competing_with: set[UUID] = Field(default_factory=set)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    fingerprint: bytes = b""
    memo_key: Optional[bytes] = None
