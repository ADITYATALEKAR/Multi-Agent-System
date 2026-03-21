"""Unit tests for the causal module: CBN, builder, intervention, discriminator."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.causal.builder import CBNBuilder
from src.causal.cbn import MAX_NODES, CausalBayesianNetwork
from src.causal.discriminator import CausalDiscriminator
from src.causal.intervention import InterventionScorer
from src.core.derived import (
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_justification() -> ExtendedJustification:
    return ExtendedJustification(rule_id="test-rule")


def _make_violation(
    entity_ids: list[UUID] | None = None,
    confidence: float = 0.7,
    payload_extra: dict | None = None,
) -> DerivedFact:
    payload: dict = {"entity_ids": entity_ids or []}
    if payload_extra:
        payload.update(payload_extra)
    return DerivedFact(
        derived_type=DerivedType.VIOLATION,
        payload=payload,
        justification=_make_justification(),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
    )


def _make_hypothesis(
    entity_ids: list[UUID] | None = None,
    confidence: float = 0.6,
) -> DerivedFact:
    return DerivedFact(
        derived_type=DerivedType.HYPOTHESIS,
        payload={"entity_ids": entity_ids or []},
        justification=_make_justification(),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
    )


# ===================================================================
# CausalBayesianNetwork tests
# ===================================================================


class TestCausalBayesianNetwork:
    """Tests for CausalBayesianNetwork."""

    def test_add_node_and_node_count(self) -> None:
        cbn = CausalBayesianNetwork()
        nid = uuid4()
        cbn.add_node(nid, "service")
        assert cbn.node_count == 1

    def test_add_edge_and_edge_count(self) -> None:
        cbn = CausalBayesianNetwork()
        a, b = uuid4(), uuid4()
        cbn.add_node(a, "service")
        cbn.add_node(b, "database")
        cbn.add_edge(a, b, weight=0.8)
        assert cbn.edge_count == 1

    def test_get_parents_and_children(self) -> None:
        cbn = CausalBayesianNetwork()
        parent, child = uuid4(), uuid4()
        cbn.add_node(parent, "service")
        cbn.add_node(child, "service")
        cbn.add_edge(parent, child)

        assert child in cbn.get_children(parent)
        assert parent in cbn.get_parents(child)
        assert cbn.get_parents(parent) == []
        assert cbn.get_children(child) == []

    def test_topological_order(self) -> None:
        cbn = CausalBayesianNetwork()
        a, b, c = uuid4(), uuid4(), uuid4()
        cbn.add_node(a, "service")
        cbn.add_node(b, "service")
        cbn.add_node(c, "service")
        cbn.add_edge(a, b)
        cbn.add_edge(b, c)

        order = cbn.topological_order()
        idx_a = order.index(a)
        idx_b = order.index(b)
        idx_c = order.index(c)
        assert idx_a < idx_b < idx_c

    def test_infer_with_evidence_changes_posteriors(self) -> None:
        cbn = CausalBayesianNetwork()
        cause, effect = uuid4(), uuid4()
        cbn.add_node(cause, "service", prior=0.5)
        cbn.add_node(effect, "service", prior=0.5)
        cbn.add_edge(cause, effect, weight=0.9)

        # Baseline
        baseline = cbn.infer({})
        # With evidence: cause is 1.0
        with_evidence = cbn.infer({cause: 1.0})

        # The effect posterior should be higher when cause is observed as 1.0
        assert with_evidence[effect] > baseline[effect]
        # Cause is clamped
        assert with_evidence[cause] == 1.0

    def test_infer_clamped_to_zero(self) -> None:
        cbn = CausalBayesianNetwork()
        cause, effect = uuid4(), uuid4()
        cbn.add_node(cause, "service", prior=0.8)
        cbn.add_node(effect, "service", prior=0.5)
        cbn.add_edge(cause, effect, weight=0.9)

        with_zero = cbn.infer({cause: 0.0})
        assert with_zero[cause] == 0.0
        # Effect should decrease when cause is clamped to 0
        baseline = cbn.infer({})
        assert with_zero[effect] < baseline[effect]

    def test_max_nodes_cap(self) -> None:
        cbn = CausalBayesianNetwork()
        for i in range(MAX_NODES):
            cbn.add_node(uuid4(), "service")

        assert cbn.node_count == MAX_NODES

        with pytest.raises(ValueError, match="cap reached"):
            cbn.add_node(uuid4(), "service")

    def test_add_edge_missing_source_raises(self) -> None:
        cbn = CausalBayesianNetwork()
        target = uuid4()
        cbn.add_node(target, "service")
        with pytest.raises(KeyError, match="Source node"):
            cbn.add_edge(uuid4(), target)

    def test_add_edge_missing_target_raises(self) -> None:
        cbn = CausalBayesianNetwork()
        source = uuid4()
        cbn.add_node(source, "service")
        with pytest.raises(KeyError, match="Target node"):
            cbn.add_edge(source, uuid4())

    def test_duplicate_node_is_idempotent(self) -> None:
        cbn = CausalBayesianNetwork()
        nid = uuid4()
        cbn.add_node(nid, "service", prior=0.3)
        cbn.add_node(nid, "service", prior=0.9)  # should be a no-op
        assert cbn.node_count == 1
        info = cbn.get_node(nid)
        assert info is not None
        assert info["prior"] == 0.3  # original prior kept


# ===================================================================
# CBNBuilder tests
# ===================================================================


class TestCBNBuilder:
    """Tests for CBNBuilder."""

    def test_build_from_graph_with_nodes_and_edges(self) -> None:
        builder = CBNBuilder()
        n1, n2 = uuid4(), uuid4()
        graph_context = {
            "nodes": [
                {"id": str(n1), "type": "service"},
                {"id": str(n2), "type": "database"},
            ],
            "edges": [
                {"source": str(n1), "target": str(n2), "weight": 0.7},
            ],
        }
        cbn = builder.build_from_graph(graph_context)
        assert cbn.node_count == 2
        assert cbn.edge_count == 1
        assert n2 in cbn.get_children(n1)

    def test_build_from_violations_links_shared_entities(self) -> None:
        builder = CBNBuilder()
        shared_entity = uuid4()
        v1 = _make_violation(entity_ids=[shared_entity, uuid4()])
        v2 = _make_violation(entity_ids=[shared_entity, uuid4()])
        v3 = _make_violation(entity_ids=[uuid4()])  # no shared entity

        cbn = builder.build_from_violations([v1, v2, v3])
        assert cbn.node_count == 3
        # v1 and v2 share an entity, so there should be an edge between them
        assert cbn.edge_count >= 1

    def test_build_from_graph_empty_context(self) -> None:
        builder = CBNBuilder()
        cbn = builder.build_from_graph({"nodes": [], "edges": []})
        assert cbn.node_count == 0
        assert cbn.edge_count == 0


# ===================================================================
# InterventionScorer tests
# ===================================================================


class TestInterventionScorer:
    """Tests for InterventionScorer."""

    def test_score_candidates_returns_sorted_list(self) -> None:
        cbn = CausalBayesianNetwork()
        a, b, c = uuid4(), uuid4(), uuid4()
        cbn.add_node(a, "service", prior=0.8)
        cbn.add_node(b, "service", prior=0.5)
        cbn.add_node(c, "service", prior=0.5)
        cbn.add_edge(a, b, weight=0.9)
        cbn.add_edge(a, c, weight=0.9)

        scorer = InterventionScorer()
        results = scorer.score_candidates(cbn, [a, b])
        assert isinstance(results, list)
        assert len(results) == 2
        # Each entry is (UUID, float)
        for nid, score in results:
            assert isinstance(nid, UUID)
            assert isinstance(score, float)
        # 'a' has two children influenced by do(a=0); 'b' has none.
        # So a should rank higher.
        assert results[0][0] == a
        assert results[0][1] >= results[1][1]

    def test_score_candidates_empty_list(self) -> None:
        cbn = CausalBayesianNetwork()
        scorer = InterventionScorer()
        assert scorer.score_candidates(cbn, []) == []

    def test_compute_causal_effect(self) -> None:
        cbn = CausalBayesianNetwork()
        cause, effect = uuid4(), uuid4()
        cbn.add_node(cause, "service", prior=0.5)
        cbn.add_node(effect, "service", prior=0.5)
        cbn.add_edge(cause, effect, weight=0.9)

        scorer = InterventionScorer()
        ce = scorer.compute_causal_effect(cbn, cause, effect)
        # P(effect | do(cause=1)) > P(effect | do(cause=0)), so effect > 0
        assert ce > 0.0

    def test_compute_causal_effect_missing_node(self) -> None:
        cbn = CausalBayesianNetwork()
        scorer = InterventionScorer()
        assert scorer.compute_causal_effect(cbn, uuid4(), uuid4()) == 0.0


# ===================================================================
# CausalDiscriminator tests
# ===================================================================


class TestCausalDiscriminator:
    """Tests for CausalDiscriminator."""

    def test_discriminate_ranks_hypotheses(self) -> None:
        # Build a CBN with some violation nodes and entity nodes
        cbn = CausalBayesianNetwork()
        ent_a, ent_b = uuid4(), uuid4()
        viol = uuid4()
        cbn.add_node(ent_a, "service", prior=0.8)
        cbn.add_node(ent_b, "service", prior=0.3)
        cbn.add_node(viol, "violation", prior=0.5)
        cbn.add_edge(ent_a, viol, weight=0.9)  # strong link
        cbn.add_edge(ent_b, viol, weight=0.1)  # weak link

        h1 = _make_hypothesis(entity_ids=[ent_a], confidence=0.6)
        h2 = _make_hypothesis(entity_ids=[ent_b], confidence=0.6)

        disc = CausalDiscriminator()
        ranked = disc.discriminate([h2, h1], cbn)

        assert isinstance(ranked, list)
        assert len(ranked) == 2
        # h1 (ent_a, strong link) should rank above h2 (ent_b, weak link)
        assert ranked[0].derived_id == h1.derived_id

    def test_discriminate_empty_hypotheses(self) -> None:
        cbn = CausalBayesianNetwork()
        disc = CausalDiscriminator()
        assert disc.discriminate([], cbn) == []

    def test_rank_root_causes(self) -> None:
        cbn = CausalBayesianNetwork()
        cause_a, cause_b = uuid4(), uuid4()
        viol = uuid4()
        cbn.add_node(cause_a, "service", prior=0.8)
        cbn.add_node(cause_b, "service", prior=0.3)
        cbn.add_node(viol, "violation", prior=0.5)
        cbn.add_edge(cause_a, viol, weight=0.9)
        cbn.add_edge(cause_b, viol, weight=0.1)

        disc = CausalDiscriminator()
        ranked = disc.rank_root_causes(cbn, [viol], [cause_a, cause_b])
        assert len(ranked) == 2
        # cause_a should rank higher
        assert ranked[0][0] == cause_a
        assert ranked[0][1] >= ranked[1][1]

    def test_rank_root_causes_empty_candidates(self) -> None:
        cbn = CausalBayesianNetwork()
        disc = CausalDiscriminator()
        assert disc.rank_root_causes(cbn, [uuid4()], []) == []
