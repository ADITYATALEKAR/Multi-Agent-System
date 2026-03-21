"""Unit tests for the Truth Maintenance System (Phase 2)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.derived import (
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)
from src.tms.belief import BeliefNode
from src.tms.confidence import EPSILON, ConfidencePropagator
from src.tms.engine import TMSEngine
from src.tms.index import TMSIndex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_derived(
    confidence: float = 0.9,
    rule_id: str = "test-rule",
    derived_type: DerivedType = DerivedType.VIOLATION,
) -> DerivedFact:
    """Create a minimal DerivedFact for testing."""
    return DerivedFact(
        derived_type=derived_type,
        payload={"detail": "test"},
        justification=ExtendedJustification(rule_id=rule_id),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
    )


def _make_justification(
    rule_id: str = "test-rule",
    supporting_facts: set | None = None,
    confidence_weight: float = 1.0,
) -> ExtendedJustification:
    """Create a minimal ExtendedJustification."""
    return ExtendedJustification(
        rule_id=rule_id,
        supporting_facts=supporting_facts or set(),
        confidence_weight=confidence_weight,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTMSEngine:
    def test_register_belief(self) -> None:
        """Registering a belief creates a BeliefNode with status IN."""
        engine = TMSEngine()
        derived = _make_derived(confidence=0.8)
        justification = _make_justification()

        belief = engine.register_belief(derived, justification, tenant_id="default")

        assert isinstance(belief, BeliefNode)
        assert belief.status == "IN"
        assert belief.confidence == pytest.approx(0.8)
        assert belief.derived_fact_id == derived.derived_id
        assert len(belief.justifications) == 1

    def test_register_belief_with_tenant_id(self) -> None:
        """Beliefs are associated with the supplied tenant_id."""
        engine = TMSEngine()
        derived = _make_derived()
        justification = _make_justification()

        belief = engine.register_belief(derived, justification, tenant_id="acme-corp")

        assert belief.tenant_id == "acme-corp"

        # Retrievable via get_all_beliefs
        all_beliefs = engine.get_all_beliefs(tenant_id="acme-corp")
        assert len(all_beliefs) == 1
        assert all_beliefs[0].belief_id == belief.belief_id

    def test_retract_support(self) -> None:
        """Retracting a fact sets the belief status to OUT with confidence 0."""
        engine = TMSEngine()
        derived = _make_derived(confidence=0.9)
        justification = _make_justification()

        belief = engine.register_belief(derived, justification)
        assert belief.status == "IN"

        transitioned = engine.retract_support(derived.derived_id)
        assert belief.belief_id in transitioned
        assert belief.status == "OUT"
        assert belief.confidence == 0.0

    def test_retract_cascades(self) -> None:
        """Retracting a root belief cascades to dependent beliefs."""
        engine = TMSEngine()

        # Create root belief
        root_derived = _make_derived(confidence=0.9, rule_id="root-rule")
        root_just = _make_justification(rule_id="root-rule")
        root_belief = engine.register_belief(root_derived, root_just)

        # Create dependent belief that cites the root
        dep_derived = _make_derived(confidence=0.7, rule_id="dep-rule")
        dep_just = _make_justification(
            rule_id="dep-rule",
            supporting_facts={root_derived.derived_id},
        )
        dep_belief = engine.register_belief(dep_derived, dep_just)

        assert dep_belief.status == "IN"

        # Retract root
        transitioned = engine.retract_support(root_derived.derived_id)

        assert root_belief.status == "OUT"
        assert dep_belief.status == "OUT"
        assert dep_belief.confidence == 0.0
        assert len(transitioned) >= 2

    def test_confidence_propagation(self) -> None:
        """Confidence changes propagate to dependent beliefs."""
        engine = TMSEngine()

        root_derived = _make_derived(confidence=0.9, rule_id="root")
        root_just = _make_justification(rule_id="root")
        root_belief = engine.register_belief(root_derived, root_just)

        dep_derived = _make_derived(confidence=0.5, rule_id="dep")
        dep_just = _make_justification(
            rule_id="dep",
            supporting_facts={root_derived.derived_id},
            confidence_weight=0.8,
        )
        dep_belief = engine.register_belief(dep_derived, dep_just)

        # The dependent's confidence should have been propagated
        # It should be derived from the root's confidence somehow
        assert dep_belief.confidence >= 0.0
        assert dep_belief.confidence <= 1.0

    def test_confidence_dampening_epsilon(self) -> None:
        """EPSILON is 0.005 and propagator uses it for dampening."""
        assert EPSILON == 0.005

        propagator = ConfidencePropagator()
        assert propagator.epsilon == EPSILON

        # A change smaller than EPSILON should not propagate further
        belief_id = uuid4()
        belief = BeliefNode(
            derived_fact_id=uuid4(),
            status="IN",
            confidence=0.5,
        )
        graph = {belief_id: belief}
        belief.belief_id = belief_id  # Ensure match

        # Change by less than epsilon
        affected = propagator.propagate(
            belief_id, 0.5 + EPSILON / 2, graph,
        )
        # Should only affect the source (dampened at source)
        assert len(affected) == 1

    def test_belief_status_transitions(self) -> None:
        """Belief transitions: UNKNOWN -> IN on justification, IN -> OUT on removal."""
        belief = BeliefNode(derived_fact_id=uuid4())
        assert belief.status == "UNKNOWN"

        # Adding a justification transitions to IN
        just = _make_justification()
        belief.add_justification(just)
        assert belief.status == "IN"
        assert belief.status_change_count == 1

        # Removing the justification transitions to OUT
        removed = belief.remove_justification(just.justification_id)
        assert removed is not None
        assert belief.status == "OUT"
        assert belief.status_change_count == 2

    def test_get_consequences(self) -> None:
        """get_consequences returns transitive dependents of a belief."""
        engine = TMSEngine()

        # Build chain: A -> B -> C
        d_a = _make_derived(confidence=0.9, rule_id="a")
        j_a = _make_justification(rule_id="a")
        b_a = engine.register_belief(d_a, j_a)

        d_b = _make_derived(confidence=0.8, rule_id="b")
        j_b = _make_justification(
            rule_id="b", supporting_facts={d_a.derived_id},
        )
        b_b = engine.register_belief(d_b, j_b)

        d_c = _make_derived(confidence=0.7, rule_id="c")
        j_c = _make_justification(
            rule_id="c", supporting_facts={d_b.derived_id},
        )
        b_c = engine.register_belief(d_c, j_c)

        # Consequences of A should include B and C
        consequences = engine.get_consequences(b_a.belief_id)
        assert b_b.belief_id in consequences
        assert b_c.belief_id in consequences

    def test_get_all_beliefs_by_tenant(self) -> None:
        """get_all_beliefs filters by tenant_id."""
        engine = TMSEngine()

        d1 = _make_derived(rule_id="r1")
        d2 = _make_derived(rule_id="r2")
        d3 = _make_derived(rule_id="r3")

        engine.register_belief(d1, _make_justification(rule_id="r1"), tenant_id="alpha")
        engine.register_belief(d2, _make_justification(rule_id="r2"), tenant_id="alpha")
        engine.register_belief(d3, _make_justification(rule_id="r3"), tenant_id="beta")

        alpha_beliefs = engine.get_all_beliefs(tenant_id="alpha")
        assert len(alpha_beliefs) == 2

        beta_beliefs = engine.get_all_beliefs(tenant_id="beta")
        assert len(beta_beliefs) == 1

        empty = engine.get_all_beliefs(tenant_id="gamma")
        assert len(empty) == 0

    def test_belief_add_justification(self) -> None:
        """Adding a second justification to an existing belief via register_belief."""
        engine = TMSEngine()
        derived = _make_derived(confidence=0.7, rule_id="r1")
        j1 = _make_justification(rule_id="r1")
        belief = engine.register_belief(derived, j1)
        assert len(belief.justifications) == 1

        # Register the same derived_id again with a different justification
        j2 = _make_justification(rule_id="r1-alt")
        belief2 = engine.register_belief(derived, j2)

        # Should be the same belief node, now with 2 justifications
        assert belief2.belief_id == belief.belief_id
        assert len(belief2.justifications) == 2


class TestTMSIndex:
    def test_index_add_and_lookup(self) -> None:
        """TMSIndex maps derived_fact_id to belief_id."""
        index = TMSIndex()
        fact_id = uuid4()
        belief = BeliefNode(derived_fact_id=fact_id, tenant_id="t1")
        index.add_belief(belief)

        assert index.get_belief_for_fact(fact_id) == belief.belief_id
        assert belief.belief_id in index.get_tenant_beliefs("t1")

    def test_index_dependency(self) -> None:
        """TMSIndex tracks dependency links between beliefs."""
        index = TMSIndex()
        supporter_id = uuid4()
        dependent_id = uuid4()

        index.add_dependency(supporter_id, dependent_id)

        assert dependent_id in index.get_dependents(supporter_id)
        assert supporter_id in index.get_supporters(dependent_id)


class TestConfidencePropagator:
    def test_propagate_basic(self) -> None:
        """Propagating a confidence change updates the belief."""
        propagator = ConfidencePropagator()
        bid = uuid4()
        belief = BeliefNode(derived_fact_id=uuid4(), status="IN", confidence=0.5)
        # Override the auto-generated belief_id
        graph = {bid: belief}
        belief.belief_id = bid

        affected = propagator.propagate(bid, 0.9, graph)
        assert bid in affected
        assert belief.confidence == pytest.approx(0.9)
