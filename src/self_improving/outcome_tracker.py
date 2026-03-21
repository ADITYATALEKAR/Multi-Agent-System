"""Outcome tracking for self-improvement feedback loops."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


class OutcomeRecord(BaseModel):
    """A single outcome observation used by the self-improving subsystem."""

    record_id: UUID = Field(default_factory=uuid4)
    target_id: UUID  # ID of the entity this outcome is about
    record_type: str  # "law_evaluation", "hypothesis_resolution", "repair_execution"
    outcome: str  # "correct", "incorrect", "partial", "timeout"
    details: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    tenant_id: str = "default"


class OutcomeTracker:
    """Stores and queries outcome records for self-improvement.

    Records are kept in-memory (per-process).  A durable backend can
    be plugged in later by subclassing.
    """

    def __init__(self) -> None:
        self._records: list[OutcomeRecord] = []

    # ── Mutation ──────────────────────────────────────────────────────────

    def record(self, outcome: OutcomeRecord) -> None:
        """Persist a new outcome record."""
        self._records.append(outcome)
        log.debug(
            "outcome_tracker.recorded",
            record_id=str(outcome.record_id),
            target_id=str(outcome.target_id),
            record_type=outcome.record_type,
            outcome=outcome.outcome,
        )

    # ── Queries ───────────────────────────────────────────────────────────

    def get_records(
        self,
        target_id: UUID,
        record_type: str = "",
    ) -> list[OutcomeRecord]:
        """Return records for *target_id*, optionally filtered by type."""
        return [
            r
            for r in self._records
            if r.target_id == target_id
            and (not record_type or r.record_type == record_type)
        ]

    def get_success_rate(
        self,
        target_id: UUID,
        record_type: str = "",
    ) -> float:
        """Return the fraction of 'correct' outcomes for *target_id*.

        Returns 0.0 when there are no matching records.
        """
        records = self.get_records(target_id, record_type)
        if not records:
            return 0.0
        correct = sum(1 for r in records if r.outcome == "correct")
        return correct / len(records)
