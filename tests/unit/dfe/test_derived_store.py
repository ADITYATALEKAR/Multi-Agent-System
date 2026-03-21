"""Unit tests for the DerivedFactStore (DFE Phase 2)."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from src.core.derived import (
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)
from src.dfe.derived_store import DerivedFactEmitter, DerivedFactStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_derived(
    derived_type: DerivedType = DerivedType.VIOLATION,
    status: DerivedStatus = DerivedStatus.SUPPORTED,
    confidence: float = 0.9,
    rule_id: str = "test-rule",
) -> DerivedFact:
    """Create a minimal DerivedFact for testing."""
    return DerivedFact(
        derived_type=derived_type,
        payload={"detail": "test"},
        justification=ExtendedJustification(rule_id=rule_id),
        status=status,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDerivedFactStore:
    def test_store_and_get(self) -> None:
        """Storing a fact makes it retrievable by ID."""
        store = DerivedFactStore()
        fact = _make_derived()
        store.store(fact)

        retrieved = store.get(fact.derived_id)
        assert retrieved is not None
        assert retrieved.derived_id == fact.derived_id
        assert retrieved.derived_type == DerivedType.VIOLATION
        assert store.count == 1

    def test_update_status(self) -> None:
        """update_status changes the status of a stored fact."""
        store = DerivedFactStore()
        fact = _make_derived(status=DerivedStatus.SUPPORTED)
        store.store(fact)

        result = store.update_status(fact.derived_id, DerivedStatus.RETRACTED)
        assert result is True

        updated = store.get(fact.derived_id)
        assert updated is not None
        assert updated.status == DerivedStatus.RETRACTED

        # Updating a nonexistent fact returns False
        assert store.update_status(uuid4(), DerivedStatus.UNKNOWN) is False

    def test_update_confidence(self) -> None:
        """update_confidence changes the confidence of a stored fact."""
        store = DerivedFactStore()
        fact = _make_derived(confidence=0.5)
        store.store(fact)

        result = store.update_confidence(fact.derived_id, 0.95)
        assert result is True

        updated = store.get(fact.derived_id)
        assert updated is not None
        assert updated.confidence == pytest.approx(0.95)

        # Updating a nonexistent fact returns False
        assert store.update_confidence(uuid4(), 0.1) is False

    def test_remove(self) -> None:
        """Removing a fact makes it no longer retrievable."""
        store = DerivedFactStore()
        fact = _make_derived()
        store.store(fact)
        assert store.count == 1

        result = store.remove(fact.derived_id)
        assert result is True
        assert store.get(fact.derived_id) is None
        assert store.count == 0

        # Removing again returns False
        assert store.remove(fact.derived_id) is False

    def test_query_by_type(self) -> None:
        """query_by_type returns facts of the requested type only."""
        store = DerivedFactStore()
        v1 = _make_derived(derived_type=DerivedType.VIOLATION, rule_id="r1")
        v2 = _make_derived(derived_type=DerivedType.VIOLATION, rule_id="r2")
        h1 = _make_derived(derived_type=DerivedType.HYPOTHESIS, rule_id="r3")

        store.store(v1)
        store.store(v2)
        store.store(h1)

        violations = store.query_by_type(DerivedType.VIOLATION)
        assert len(violations) == 2

        hypotheses = store.query_by_type(DerivedType.HYPOTHESIS)
        assert len(hypotheses) == 1
        assert hypotheses[0].derived_id == h1.derived_id

        # Type with no facts returns empty
        causal = store.query_by_type(DerivedType.CAUSAL_EDGE)
        assert causal == []


class TestDerivedFactEmitter:
    def test_emitter_with_tms(self) -> None:
        """Emitter stores fact in DerivedFactStore and registers with TMS."""
        store = DerivedFactStore()
        mock_tms = MagicMock()
        emitter = DerivedFactEmitter(store=store, tms_engine=mock_tms)

        fact = _make_derived()
        emitter.emit(fact, tenant_id="tenant-a")

        # Fact is in the store
        assert store.get(fact.derived_id) is not None

        # TMS.register_belief was called with the correct arguments
        mock_tms.register_belief.assert_called_once_with(
            derived=fact,
            justification=fact.justification,
            tenant_id="tenant-a",
        )

    def test_emitter_without_tms(self) -> None:
        """Emitter works without a TMS engine (stores only)."""
        store = DerivedFactStore()
        emitter = DerivedFactEmitter(store=store, tms_engine=None)

        fact = _make_derived()
        emitter.emit(fact)

        assert store.get(fact.derived_id) is not None
        assert store.count == 1

    def test_emitter_retract(self) -> None:
        """Emitter.retract updates status to RETRACTED and notifies TMS."""
        store = DerivedFactStore()
        mock_tms = MagicMock()
        emitter = DerivedFactEmitter(store=store, tms_engine=mock_tms)

        fact = _make_derived()
        emitter.emit(fact)

        result = emitter.retract(fact.derived_id)
        assert result is True

        updated = store.get(fact.derived_id)
        assert updated is not None
        assert updated.status == DerivedStatus.RETRACTED

        mock_tms.retract_support.assert_called_once_with(fact.derived_id)
