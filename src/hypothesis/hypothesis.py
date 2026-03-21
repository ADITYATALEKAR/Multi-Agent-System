"""Hypothesis data model — the core unit produced by every strategy."""

from __future__ import annotations

import enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class HypothesisStatus(str, enum.Enum):
    """Lifecycle status of a hypothesis."""

    PROPOSED = "proposed"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    MERGED = "merged"


class Hypothesis(BaseModel):
    """A single root-cause hypothesis produced by a strategy.

    Attributes:
        hypothesis_id: Globally unique identifier for this hypothesis.
        description: Human-readable explanation of the suspected root cause.
        confidence: Confidence score in [0.0, 1.0]; higher is more likely.
        strategy_id: Identifier of the strategy that created this hypothesis.
        supporting_evidence: UUIDs of DerivedFacts / violations that back this
            hypothesis.
        status: Current lifecycle status (proposed, supported, refuted, merged).
    """

    hypothesis_id: UUID = Field(default_factory=uuid4)
    description: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    strategy_id: str
    supporting_evidence: list[UUID] = Field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
