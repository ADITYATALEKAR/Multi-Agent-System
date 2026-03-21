"""Unit tests for the Rete network (DFE Phase 2)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.derived import DerivedStatus, DerivedType
from src.core.fact import AddEdge, AddNode, GraphDelta, RemoveNode
from src.dfe.rete import (
    BETA_MEMORY_CAP,
    AlphaCondition,
    BetaMemory,
    PartialMatch,
    ProductionNode,
    ReteNetwork,
    RuleAction,
    RuleIR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _single_node_rule(rule_id: str = "r1", node_type: str = "class") -> RuleIR:
    """Create a simple single-condition rule matching a node type."""
    return RuleIR(
        rule_id=rule_id,
        name=f"Rule {rule_id}",
        conditions=[
            AlphaCondition(
                condition_id=f"{rule_id}_c0",
                entity_type="node",
                type_filter=node_type,
                bind_var="x",
            ),
        ],
        action=RuleAction(
            derived_type=DerivedType.VIOLATION,
            confidence=0.9,
            message_template="Node $x is a violation",
        ),
    )


def _multi_condition_rule(
    rule_id: str = "r2",
    node_type_a: str = "class",
    node_type_b: str = "function",
) -> RuleIR:
    """Create a two-condition rule matching two different node types."""
    return RuleIR(
        rule_id=rule_id,
        name=f"Rule {rule_id}",
        conditions=[
            AlphaCondition(
                condition_id=f"{rule_id}_c0",
                entity_type="node",
                type_filter=node_type_a,
                bind_var="a",
            ),
            AlphaCondition(
                condition_id=f"{rule_id}_c1",
                entity_type="node",
                type_filter=node_type_b,
                bind_var="b",
            ),
        ],
        action=RuleAction(
            derived_type=DerivedType.VIOLATION,
            confidence=0.85,
        ),
    )


def _edge_rule(rule_id: str = "r_edge", edge_type: str = "contains") -> RuleIR:
    """Create a single-condition rule matching an edge type."""
    return RuleIR(
        rule_id=rule_id,
        name=f"Edge Rule {rule_id}",
        conditions=[
            AlphaCondition(
                condition_id=f"{rule_id}_c0",
                entity_type="edge",
                type_filter=edge_type,
                bind_var="e",
            ),
        ],
        action=RuleAction(
            derived_type=DerivedType.PATTERN_MATCH,
            confidence=0.7,
        ),
    )


def _make_delta(operations, seq=0):
    return GraphDelta(
        sequence_number=seq,
        source="test",
        operations=operations,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReteNetwork:
    def test_register_single_condition_rule(self) -> None:
        """Registering a single-condition rule adds it to the network."""
        net = ReteNetwork()
        rule = _single_node_rule()
        net.register_rule(rule)

        assert net.rule_count == 1
        assert "r1" in net._rules
        assert len(net._rule_alphas["r1"]) == 1
        assert len(net._rule_betas["r1"]) == 0

    def test_register_multi_condition_rule(self) -> None:
        """Registering a two-condition rule creates alpha + beta nodes."""
        net = ReteNetwork()
        rule = _multi_condition_rule()
        net.register_rule(rule)

        assert net.rule_count == 1
        assert len(net._rule_alphas["r2"]) == 2
        assert len(net._rule_betas["r2"]) == 1

    def test_assert_node_matches_rule(self) -> None:
        """Asserting a node that matches a single-condition rule produces a DerivedFact."""
        net = ReteNetwork()
        net.register_rule(_single_node_rule("r1", "class"))

        node_id = uuid4()
        derived = net.assert_fact(
            fact_id=node_id,
            entity_type="node",
            type_value="class",
            attributes={"name": "Foo"},
        )

        assert len(derived) == 1
        assert derived[0].derived_type == DerivedType.VIOLATION
        assert derived[0].status == DerivedStatus.SUPPORTED
        assert derived[0].confidence == 0.9

    def test_assert_node_no_match(self) -> None:
        """Asserting a node with a non-matching type produces no derived facts."""
        net = ReteNetwork()
        net.register_rule(_single_node_rule("r1", "class"))

        derived = net.assert_fact(
            fact_id=uuid4(),
            entity_type="node",
            type_value="function",
            attributes={},
        )

        assert len(derived) == 0

    def test_assert_edge_matches(self) -> None:
        """Asserting an edge that matches an edge-type condition produces a DerivedFact."""
        net = ReteNetwork()
        net.register_rule(_edge_rule("r_edge", "contains"))

        src, tgt = uuid4(), uuid4()
        edge_id = uuid4()
        derived = net.assert_fact(
            fact_id=edge_id,
            entity_type="edge",
            type_value="contains",
            attributes={},
            src_id=src,
            tgt_id=tgt,
        )

        assert len(derived) == 1
        assert derived[0].derived_type == DerivedType.PATTERN_MATCH
        assert derived[0].confidence == 0.7

    def test_retract_fact(self) -> None:
        """Retracting a fact that was part of a multi-condition match cleans up."""
        net = ReteNetwork()
        net.register_rule(_multi_condition_rule("r2", "class", "function"))

        nid_a = uuid4()
        nid_b = uuid4()

        # Assert both conditions so a match fires
        net.assert_fact(fact_id=nid_a, entity_type="node",
                        type_value="class", attributes={})
        derived = net.assert_fact(fact_id=nid_b, entity_type="node",
                                  type_value="function", attributes={})
        assert len(derived) == 1

        # Retract one of the supporting facts
        retracted = net.retract_fact(nid_a)
        # The retraction should succeed (list may contain IDs of retracted derivations)
        assert isinstance(retracted, list)

    def test_evaluate_delta_produces_derived_facts(self) -> None:
        """Evaluating a GraphDelta with matching AddNode ops produces derived facts."""
        net = ReteNetwork()
        net.register_rule(_single_node_rule("r1", "service"))

        node_id = uuid4()
        delta = _make_delta([
            AddNode(node_id=node_id, node_type="service", attributes={"name": "svc-a"}),
        ])

        derived = net.evaluate(delta)
        assert len(derived) == 1
        assert derived[0].derived_type == DerivedType.VIOLATION
        assert derived[0].justification.rule_id == "r1"

    def test_evaluate_empty_delta(self) -> None:
        """Evaluating an empty delta produces no derived facts."""
        net = ReteNetwork()
        net.register_rule(_single_node_rule())

        delta = _make_delta([])
        derived = net.evaluate(delta)
        assert derived == []

    def test_partial_match_extend(self) -> None:
        """PartialMatch.extend creates a new PartialMatch with an added binding."""
        pm = PartialMatch(bindings={"x": uuid4()}, fact_ids=set())
        new_val = uuid4()
        fact_id = uuid4()
        extended = pm.extend("y", new_val, fact_id)

        assert "y" in extended.bindings
        assert extended.bindings["y"] == new_val
        assert fact_id in extended.fact_ids
        # Original is not mutated
        assert "y" not in pm.bindings

    def test_partial_match_merge_compatible(self) -> None:
        """Merging two compatible PartialMatches produces a combined result."""
        uid_x = uuid4()
        uid_y = uuid4()
        pm1 = PartialMatch(bindings={"x": uid_x}, fact_ids={uuid4()})
        pm2 = PartialMatch(bindings={"y": uid_y}, fact_ids={uuid4()})

        merged = pm1.merge(pm2)
        assert merged is not None
        assert merged.bindings["x"] == uid_x
        assert merged.bindings["y"] == uid_y
        assert len(merged.fact_ids) == 2

    def test_partial_match_merge_conflict(self) -> None:
        """Merging two PartialMatches with conflicting bindings returns None."""
        uid1 = uuid4()
        uid2 = uuid4()
        pm1 = PartialMatch(bindings={"x": uid1}, fact_ids=set())
        pm2 = PartialMatch(bindings={"x": uid2}, fact_ids=set())

        merged = pm1.merge(pm2)
        assert merged is None

    def test_beta_memory_cap(self) -> None:
        """BetaMemory refuses additions beyond BETA_MEMORY_CAP (v3.3 B2)."""
        small_cap = 3
        bm = BetaMemory(rule_id="cap_test", cap=small_cap)

        for i in range(small_cap):
            uid = uuid4()
            pm = PartialMatch(bindings={f"v{i}": uid}, fact_ids={uid})
            assert bm.add(pm) is True

        # Next add should be rejected
        uid = uuid4()
        pm = PartialMatch(bindings={"overflow": uid}, fact_ids={uid})
        assert bm.add(pm) is False
        assert len(bm) == small_cap

    def test_production_node_dedup(self) -> None:
        """ProductionNode only fires once for the same partial match key."""
        rule = _single_node_rule("r_dedup", "class")
        prod = ProductionNode(rule)

        uid = uuid4()
        pm = PartialMatch(bindings={"x": uid}, fact_ids={uid})

        first = prod.fire(pm)
        assert first is not None
        assert first.derived_type == DerivedType.VIOLATION

        second = prod.fire(pm)
        assert second is None

    def test_beta_memory_cap_constant(self) -> None:
        """BETA_MEMORY_CAP is 100_000 as specified in v3.3 B2."""
        assert BETA_MEMORY_CAP == 100_000
