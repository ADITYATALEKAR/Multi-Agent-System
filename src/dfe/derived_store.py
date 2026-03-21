"""DerivedFact storage + TMS registration (Phase 2 Step 6).

In-memory store for derived facts produced by the Rete network.
Registers facts with the TMS for truth maintenance.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

import structlog

from src.core.derived import DerivedFact, DerivedStatus, DerivedType

logger = structlog.get_logger(__name__)


class DerivedFactStore:
    """In-memory store for derived facts with type and status indexing."""

    def __init__(self) -> None:
        self._facts: dict[UUID, DerivedFact] = {}
        self._by_type: dict[DerivedType, dict[UUID, DerivedFact]] = defaultdict(dict)
        self._by_status: dict[DerivedStatus, set[UUID]] = defaultdict(set)
        self._by_rule: dict[str, set[UUID]] = defaultdict(set)

    def store(self, fact: DerivedFact) -> None:
        """Store a derived fact and update indexes."""
        self._facts[fact.derived_id] = fact
        self._by_type[fact.derived_type][fact.derived_id] = fact
        self._by_status[fact.status].add(fact.derived_id)
        self._by_rule[fact.justification.rule_id].add(fact.derived_id)

        logger.debug(
            "derived_fact_stored",
            derived_id=str(fact.derived_id),
            type=fact.derived_type.value,
            status=fact.status.value,
            confidence=fact.confidence,
        )

    def get(self, derived_id: UUID) -> DerivedFact | None:
        """Retrieve a derived fact by ID."""
        return self._facts.get(derived_id)

    def update_status(self, derived_id: UUID, new_status: DerivedStatus) -> bool:
        """Update the status of a derived fact."""
        fact = self._facts.get(derived_id)
        if fact is None:
            return False

        old_status = fact.status
        self._by_status[old_status].discard(derived_id)

        # Create updated fact (pydantic models are semi-immutable)
        updated = fact.model_copy(update={"status": new_status})
        self._facts[derived_id] = updated
        self._by_type[updated.derived_type][derived_id] = updated
        self._by_status[new_status].add(derived_id)

        return True

    def update_confidence(self, derived_id: UUID, new_confidence: float) -> bool:
        """Update the confidence of a derived fact."""
        fact = self._facts.get(derived_id)
        if fact is None:
            return False

        updated = fact.model_copy(update={"confidence": new_confidence})
        self._facts[derived_id] = updated
        self._by_type[updated.derived_type][derived_id] = updated
        return True

    def remove(self, derived_id: UUID) -> bool:
        """Remove a derived fact from the store."""
        fact = self._facts.pop(derived_id, None)
        if fact is None:
            return False

        self._by_type[fact.derived_type].pop(derived_id, None)
        self._by_status[fact.status].discard(derived_id)
        self._by_rule[fact.justification.rule_id].discard(derived_id)
        return True

    def query_by_type(self, derived_type: DerivedType) -> list[DerivedFact]:
        """Query derived facts by type."""
        return list(self._by_type.get(derived_type, {}).values())

    def query_by_status(self, status: DerivedStatus) -> list[DerivedFact]:
        """Query derived facts by status."""
        ids = self._by_status.get(status, set())
        return [self._facts[fid] for fid in ids if fid in self._facts]

    def query_by_rule(self, rule_id: str) -> list[DerivedFact]:
        """Query derived facts by the rule that produced them."""
        ids = self._by_rule.get(rule_id, set())
        return [self._facts[fid] for fid in ids if fid in self._facts]

    def get_violations(self, tenant_id: str = "default") -> list[DerivedFact]:
        """Get all current supported violations."""
        violations = self.query_by_type(DerivedType.VIOLATION)
        return [v for v in violations if v.status == DerivedStatus.SUPPORTED]

    def get_hypotheses(self) -> list[DerivedFact]:
        """Get all current hypotheses, sorted by confidence."""
        hyps = self.query_by_type(DerivedType.HYPOTHESIS)
        return sorted(hyps, key=lambda h: h.confidence, reverse=True)

    @property
    def count(self) -> int:
        return len(self._facts)

    @property
    def violation_count(self) -> int:
        return len(self._by_type.get(DerivedType.VIOLATION, {}))


class DerivedFactEmitter:
    """Emits derived facts to both the store and optionally the TMS.

    Bridges DFE output to the TMS for truth maintenance.
    """

    def __init__(
        self,
        store: DerivedFactStore,
        tms_engine: Any = None,  # TMSEngine, imported lazily to avoid circular
    ) -> None:
        self._store = store
        self._tms = tms_engine

    def emit(self, fact: DerivedFact, tenant_id: str = "default") -> None:
        """Store a derived fact and register it with TMS if available."""
        self._store.store(fact)

        if self._tms is not None:
            try:
                self._tms.register_belief(
                    derived=fact,
                    justification=fact.justification,
                    tenant_id=tenant_id,
                )
            except Exception as e:
                logger.error(
                    "tms_registration_failed",
                    derived_id=str(fact.derived_id),
                    error=str(e),
                )

    def emit_batch(self, facts: list[DerivedFact], tenant_id: str = "default") -> None:
        """Store and register a batch of derived facts."""
        for fact in facts:
            self.emit(fact, tenant_id)

    def retract(self, derived_id: UUID) -> bool:
        """Retract a derived fact from the store and TMS."""
        fact = self._store.get(derived_id)
        if fact is None:
            return False

        self._store.update_status(derived_id, DerivedStatus.RETRACTED)

        if self._tms is not None:
            try:
                self._tms.retract_support(derived_id)
            except Exception as e:
                logger.error(
                    "tms_retraction_failed",
                    derived_id=str(derived_id),
                    error=str(e),
                )

        return True
