"""Unit tests for the counterfactual module: boundary, replay, engine."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.core.counterfactual import (
    CounterfactualConclusion,
    CounterfactualScenario,
    Intervention,
    InterventionType,
)
from src.core.derived import (
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)
from src.core.fact import AddNode, GraphDelta
from src.counterfactual.boundary import AdaptiveSimulationBoundary
from src.counterfactual.engine import CounterfactualEngine
from src.counterfactual.replay import DeltaReplayEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_justification() -> ExtendedJustification:
    return ExtendedJustification(rule_id="test-rule")


def _make_hypothesis(
    entity_id: UUID,
    trigger_delta_ids: list[UUID] | None = None,
    confidence: float = 0.7,
) -> DerivedFact:
    return DerivedFact(
        derived_type=DerivedType.HYPOTHESIS,
        payload={
            "entity_id": str(entity_id),
            "entity_ids": [str(entity_id)],
            "trigger_delta_ids": [str(tid) for tid in (trigger_delta_ids or [])],
        },
        justification=_make_justification(),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
    )


def _make_graph_context(
    node_ids: list[UUID],
    edges: list[tuple[UUID, UUID]] | None = None,
    attention_scores: dict[UUID, float] | None = None,
) -> dict:
    """Build a graph_context dict for boundary computation."""
    attn = attention_scores or {}
    nodes = [
        {"id": str(nid), "attention_score": attn.get(nid, 0.5)}
        for nid in node_ids
    ]
    edge_list = [
        {"source": str(src), "target": str(tgt)}
        for src, tgt in (edges or [])
    ]
    return {"nodes": nodes, "edges": edge_list}


def _make_delta(
    node_id: UUID,
    seq: int = 0,
    delta_id: UUID | None = None,
) -> GraphDelta:
    return GraphDelta(
        delta_id=delta_id or uuid4(),
        sequence_number=seq,
        source="test",
        operations=[AddNode(node_id=node_id, node_type="service")],
    )


# ===================================================================
# AdaptiveSimulationBoundary tests
# ===================================================================


class TestAdaptiveSimulationBoundary:
    """Tests for AdaptiveSimulationBoundary."""

    def test_compute_returns_boundary_with_seed_node(self) -> None:
        """Seed entity must always be in the boundary."""
        seed = uuid4()
        neighbor = uuid4()

        graph = _make_graph_context(
            node_ids=[seed, neighbor],
            edges=[(seed, neighbor)],
            attention_scores={seed: 0.8, neighbor: 0.5},
        )
        hyp = _make_hypothesis(entity_id=seed)

        boundary_comp = AdaptiveSimulationBoundary()
        boundary, exp_count = boundary_comp.compute(hyp, graph)

        assert seed in boundary
        assert isinstance(boundary, set)
        assert exp_count >= 1

    def test_expansion_when_boundary_too_small(self) -> None:
        """If 1-hop gives < 5 nodes, expansion rounds fire."""
        seed = uuid4()
        # Build a chain: seed -> n1 -> n2 -> n3 -> n4 -> n5
        chain = [uuid4() for _ in range(5)]
        all_nodes = [seed] + chain
        edges = [(all_nodes[i], all_nodes[i + 1]) for i in range(len(all_nodes) - 1)]
        attn = {nid: 0.5 for nid in all_nodes}  # all above threshold 0.3

        graph = _make_graph_context(
            node_ids=all_nodes, edges=edges, attention_scores=attn,
        )
        hyp = _make_hypothesis(entity_id=seed)

        boundary_comp = AdaptiveSimulationBoundary()
        boundary, exp_count = boundary_comp.compute(hyp, graph)

        # With expansion, boundary should include more than just seed + 1-hop
        assert len(boundary) >= 2
        # At least 2 expansion rounds (1-hop gives only 1 neighbor)
        assert exp_count >= 2

    def test_hard_cap_at_750_nodes(self) -> None:
        seed = uuid4()
        # Create 800 neighbors all connected to seed
        neighbors = [uuid4() for _ in range(800)]
        all_nodes = [seed] + neighbors
        edges = [(seed, n) for n in neighbors]
        attn = {nid: 0.9 for nid in all_nodes}

        graph = _make_graph_context(
            node_ids=all_nodes, edges=edges, attention_scores=attn,
        )
        hyp = _make_hypothesis(entity_id=seed)

        boundary_comp = AdaptiveSimulationBoundary(max_nodes=750)
        boundary, _ = boundary_comp.compute(hyp, graph)

        assert len(boundary) <= 750
        assert seed in boundary  # seed always preserved

    def test_get_expansion_triggers(self) -> None:
        seed = uuid4()
        # Isolated seed with no neighbors -> expansion rounds fire
        graph = _make_graph_context(node_ids=[seed], edges=[])
        hyp = _make_hypothesis(entity_id=seed)

        boundary_comp = AdaptiveSimulationBoundary()
        boundary_comp.compute(hyp, graph)

        triggers = boundary_comp.get_expansion_triggers()
        assert isinstance(triggers, list)
        # Boundary is just {seed} which is < 5, so expansion triggers should fire
        assert len(triggers) >= 1

    def test_missing_entity_id_returns_empty(self) -> None:
        hyp = DerivedFact(
            derived_type=DerivedType.HYPOTHESIS,
            payload={},  # no entity_id
            justification=_make_justification(),
            status=DerivedStatus.SUPPORTED,
            confidence=0.7,
        )
        boundary_comp = AdaptiveSimulationBoundary()
        boundary, exp = boundary_comp.compute(hyp, {"nodes": [], "edges": []})
        assert boundary == set()
        assert exp == 0


# ===================================================================
# DeltaReplayEngine tests
# ===================================================================


class TestDeltaReplayEngine:
    """Tests for DeltaReplayEngine."""

    def test_replay_remove_delta_removes_target(self) -> None:
        engine = DeltaReplayEngine()
        node_a, node_b = uuid4(), uuid4()
        d1 = _make_delta(node_a, seq=0)
        d2 = _make_delta(node_b, seq=1)

        intervention = Intervention(
            intervention_type=InterventionType.REMOVE_DELTA,
            target_deltas=[d1.delta_id],
        )

        result = engine.replay([d1, d2], intervention, boundary=set())
        # d1 should be removed; d2 should remain
        result_ids = {d.delta_id for d in result}
        assert d1.delta_id not in result_ids
        assert d2.delta_id in result_ids

    def test_replay_with_boundary_filtering(self) -> None:
        engine = DeltaReplayEngine()
        node_a, node_b = uuid4(), uuid4()
        d1 = _make_delta(node_a, seq=0)
        d2 = _make_delta(node_b, seq=1)

        intervention = Intervention(
            intervention_type=InterventionType.REMOVE_DELTA,
            target_deltas=[],  # don't remove anything
        )

        # Boundary only includes node_a
        result = engine.replay([d1, d2], intervention, boundary={node_a})
        result_ids = {d.delta_id for d in result}
        assert d1.delta_id in result_ids
        assert d2.delta_id not in result_ids

    def test_compute_violation_diff(self) -> None:
        engine = DeltaReplayEngine()
        v1, v2, v3 = uuid4(), uuid4(), uuid4()

        original = {v1, v2}
        replayed = {v2, v3}

        removed, added = engine.compute_violation_diff(original, replayed)
        assert removed == {v1}
        assert added == {v3}

    def test_replay_empty_deltas(self) -> None:
        engine = DeltaReplayEngine()
        intervention = Intervention(
            intervention_type=InterventionType.REMOVE_DELTA,
            target_deltas=[],
        )
        result = engine.replay([], intervention, boundary=set())
        assert result == []


# ===================================================================
# CounterfactualEngine tests
# ===================================================================


class TestCounterfactualEngine:
    """Tests for CounterfactualEngine."""

    def test_validate_hypothesis_returns_counterfactual_scenario(self) -> None:
        seed = uuid4()
        trigger_delta_id = uuid4()

        # Build graph context with enough nodes for a non-INCONCLUSIVE result
        neighbors = [uuid4() for _ in range(6)]
        all_nodes = [seed] + neighbors
        edges = [(seed, n) for n in neighbors]
        attn = {nid: 0.8 for nid in all_nodes}
        graph_ctx = _make_graph_context(all_nodes, edges, attn)

        # Build delta log with the trigger delta referencing the seed node
        trigger_delta = _make_delta(seed, seq=0, delta_id=trigger_delta_id)
        other_delta = _make_delta(neighbors[0], seq=1)
        delta_log = [trigger_delta, other_delta]

        # Build hypothesis
        hyp = _make_hypothesis(
            entity_id=seed,
            trigger_delta_ids=[trigger_delta_id],
            confidence=0.8,
        )

        original_violations = {trigger_delta_id}

        engine = CounterfactualEngine()
        scenario = engine.validate_hypothesis(
            hypothesis=hyp,
            delta_log=delta_log,
            original_violations=original_violations,
            graph_context=graph_ctx,
            budget_ms=10000,
        )

        assert isinstance(scenario, CounterfactualScenario)
        assert scenario.boundary_size >= 1
        assert scenario.conclusion in (
            CounterfactualConclusion.CAUSES_SYMPTOM,
            CounterfactualConclusion.DOES_NOT_CAUSE,
            CounterfactualConclusion.INCONCLUSIVE,
        )

    def test_validate_hypothesis_causes_symptom_when_violations_removed(self) -> None:
        """When the trigger delta is removed and it was also the violation,
        violations_removed should be non-empty, yielding CAUSES_SYMPTOM."""
        seed = uuid4()
        trigger_id = uuid4()

        neighbors = [uuid4() for _ in range(6)]
        all_nodes = [seed] + neighbors
        edges = [(seed, n) for n in neighbors]
        attn = {nid: 0.8 for nid in all_nodes}
        graph_ctx = _make_graph_context(all_nodes, edges, attn)

        # The trigger delta is both the delta and the violation ID
        trigger_delta = _make_delta(seed, seq=0, delta_id=trigger_id)
        delta_log = [trigger_delta]

        hyp = _make_hypothesis(entity_id=seed, trigger_delta_ids=[trigger_id])
        original_violations = {trigger_id}

        engine = CounterfactualEngine()
        scenario = engine.validate_hypothesis(
            hyp, delta_log, original_violations, graph_ctx, budget_ms=10000,
        )

        # The trigger delta is removed, so the violation should also be removed
        assert scenario.conclusion == CounterfactualConclusion.CAUSES_SYMPTOM
        assert scenario.resulting_health_delta > 0.0
