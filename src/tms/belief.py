"""Belief node for the Truth Maintenance System.

Each BeliefNode wraps a DerivedFact and tracks its current belief status,
confidence, justifications, and dependency links to other beliefs.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from src.core.derived import ExtendedJustification as _ExtendedJustification

if TYPE_CHECKING:
    from src.core.derived import ExtendedJustification

logger = structlog.get_logger(__name__)


class BeliefNode(BaseModel):
    """A node in the TMS belief graph.

    Status semantics:
      - "IN"      -- the belief is currently justified and believed.
      - "OUT"     -- the belief has lost all supporting justifications.
      - "UNKNOWN" -- initial state before any justification is evaluated.

    ``status_change_count`` is incremented every time the status flips
    between IN and OUT.  IIE Pass 7 treats > 3 flips as oscillation.
    """

    belief_id: UUID = Field(default_factory=uuid4)
    derived_fact_id: UUID
    tenant_id: str = "default"
    status: str = "UNKNOWN"
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    justifications: list[ExtendedJustification] = Field(default_factory=list)
    supporting_beliefs: set[UUID] = Field(default_factory=set)
    dependent_beliefs: set[UUID] = Field(default_factory=set)
    status_change_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"arbitrary_types_allowed": True}

    # ── status queries ───────────────────────────────────────────────

    def is_in(self) -> bool:
        """Return ``True`` if the belief is currently believed (status == IN)."""
        return self.status == "IN"

    def is_out(self) -> bool:
        """Return ``True`` if the belief is not currently believed (status == OUT)."""
        return self.status == "OUT"

    # ── justification management ─────────────────────────────────────

    def add_justification(self, justification: ExtendedJustification) -> None:
        """Add a justification and transition status to IN if applicable.

        If the belief was previously OUT or UNKNOWN and now has at least one
        justification, the status flips to IN.
        """
        self.justifications.append(justification)
        self.updated_at = datetime.utcnow()

        old_status = self.status
        if self.justifications:
            self.status = "IN"

        if old_status != self.status:
            self.status_change_count += 1
            logger.info(
                "belief_status_changed",
                belief_id=str(self.belief_id),
                old_status=old_status,
                new_status=self.status,
                change_count=self.status_change_count,
            )

    def remove_justification(self, justification_id: UUID) -> ExtendedJustification | None:
        """Remove a justification by its ID.

        If no justifications remain, the status transitions to OUT.

        Returns:
            The removed justification, or ``None`` if not found.
        """
        removed: ExtendedJustification | None = None
        new_justifications: list[ExtendedJustification] = []
        for j in self.justifications:
            if j.justification_id == justification_id:
                removed = j
            else:
                new_justifications.append(j)

        if removed is None:
            logger.warning(
                "justification_not_found",
                belief_id=str(self.belief_id),
                justification_id=str(justification_id),
            )
            return None

        self.justifications = new_justifications
        self.updated_at = datetime.utcnow()

        old_status = self.status
        if not self.justifications:
            self.status = "OUT"
            self.confidence = 0.0

        if old_status != self.status:
            self.status_change_count += 1
            logger.info(
                "belief_status_changed",
                belief_id=str(self.belief_id),
                old_status=old_status,
                new_status=self.status,
                change_count=self.status_change_count,
            )

        return removed


BeliefNode.model_rebuild(_types_namespace={"ExtendedJustification": _ExtendedJustification})
