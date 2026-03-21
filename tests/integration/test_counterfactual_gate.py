"""Counterfactual Ground-Truth Validation Gate (v3.3 F4).

GATE requirement: >= 7/10 correct conclusions on 10 known-cause scenarios.

Each scenario uses a graph with 5+ nodes to ensure the adaptive boundary
produces a boundary >= 3 (the engine's _BOUNDARY_TOO_SMALL threshold).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from src.core.counterfactual import CounterfactualConclusion
from src.core.derived import DerivedFact, DerivedType, DerivedStatus, ExtendedJustification
from src.core.fact import AddNode, GraphDelta
from src.counterfactual.boundary import AdaptiveSimulationBoundary
from src.counterfactual.engine import CounterfactualEngine
from src.counterfactual.replay import DeltaReplayEngine


def _hyp(entity_id: UUID, trigger_ids: list[UUID]) -> DerivedFact:
    return DerivedFact(
        derived_type=DerivedType.HYPOTHESIS,
        payload={
            "entity_id": str(entity_id),
            "rule_id": "test-rule",
            "trigger_delta_ids": [str(d) for d in trigger_ids],
        },
        justification=ExtendedJustification(rule_id="test-rule"),
        status=DerivedStatus.SUPPORTED,
        confidence=0.8,
    )


def _ctx(node_ids: list[UUID], edges: list[tuple[UUID, UUID]]) -> dict:
    """Build graph context with high attention scores to ensure inclusion."""
    nodes = [{"id": str(nid), "attention_score": 0.8} for nid in node_ids]
    return {"nodes": nodes, "edges": [{"source": str(s), "target": str(t)} for s, t in edges]}


def _delta(seq: int, node_id: UUID, did: UUID | None = None) -> GraphDelta:
    d = GraphDelta(
        sequence_number=seq,
        source="test",
        operations=[AddNode(node_id=node_id, node_type="service")],
    )
    if did:
        d = d.model_copy(update={"delta_id": did})
    return d


def _star_graph(center: UUID, n: int = 5) -> tuple[list[UUID], list[tuple[UUID, UUID]]]:
    """Create a star graph with center and n neighbors. Returns (all_nodes, edges)."""
    neighbors = [uuid4() for _ in range(n)]
    all_nodes = [center] + neighbors
    edges = [(center, nb) for nb in neighbors]
    return all_nodes, edges


class TestCounterfactualGate:
    """v3.3 F4: Counterfactual ground-truth validation — >= 7/10 on known cases."""

    def _engine(self) -> CounterfactualEngine:
        return CounterfactualEngine(
            boundary_computer=AdaptiveSimulationBoundary(
                max_nodes=750, expansion_rounds=3, initial_threshold=0.1,
            ),
            replay_engine=DeltaReplayEngine(),
        )

    def _run(self, engine, hyp, delta_log, violations, ctx) -> CounterfactualConclusion:
        return engine.validate_hypothesis(hyp, delta_log, violations, ctx, budget_ms=5000).conclusion

    def test_gate_7_of_10(self) -> None:
        """GATE: >= 7/10 correct conclusions on 10 known-cause scenarios."""
        engine = self._engine()
        correct = 0

        # ── Scenario 1: Root cause delta removed → CAUSES_SYMPTOM ──
        root = uuid4()
        root_did = uuid4()
        nodes1, edges1 = _star_graph(root, 5)
        deltas1 = [_delta(1, root, root_did)] + [_delta(i + 2, n) for i, n in enumerate(nodes1[1:])]
        r = self._run(engine, _hyp(root, [root_did]), deltas1, {root_did}, _ctx(nodes1, edges1))
        if r == CounterfactualConclusion.CAUSES_SYMPTOM:
            correct += 1

        # ── Scenario 2: Benign delta removed → DOES_NOT_CAUSE ──
        center2 = uuid4()
        benign2 = uuid4()
        benign_did = uuid4()
        nodes2, edges2 = _star_graph(center2, 5)
        nodes2.append(benign2)
        edges2.append((center2, benign2))
        deltas2 = [_delta(1, center2)] + [_delta(i + 2, n) for i, n in enumerate(nodes2[1:])]
        deltas2.append(_delta(len(nodes2) + 1, benign2, benign_did))
        viol2 = {uuid4()}  # Unrelated violation
        r = self._run(engine, _hyp(benign2, [benign_did]), deltas2, viol2, _ctx(nodes2, edges2))
        if r == CounterfactualConclusion.DOES_NOT_CAUSE:
            correct += 1

        # ── Scenario 3: Chain root removed → CAUSES_SYMPTOM ──
        chain = [uuid4() for _ in range(6)]
        chain_edges = [(chain[i], chain[i + 1]) for i in range(5)]
        chain_did = uuid4()
        deltas3 = [_delta(1, chain[0], chain_did)] + [_delta(i + 2, chain[i + 1]) for i in range(5)]
        r = self._run(engine, _hyp(chain[0], [chain_did]), deltas3, {chain_did}, _ctx(chain, chain_edges))
        if r == CounterfactualConclusion.CAUSES_SYMPTOM:
            correct += 1

        # ── Scenario 4: Leaf removed → DOES_NOT_CAUSE ──
        leaf_did = uuid4()
        deltas4 = list(deltas3) + [_delta(7, chain[5], leaf_did)]
        root_viol = {chain_did}
        r = self._run(engine, _hyp(chain[5], [leaf_did]), deltas4, root_viol, _ctx(chain, chain_edges))
        if r == CounterfactualConclusion.DOES_NOT_CAUSE:
            correct += 1

        # ── Scenario 5: Multi-cause all removed → CAUSES_SYMPTOM ──
        hub = uuid4()
        ca, cb = uuid4(), uuid4()
        da, db = uuid4(), uuid4()
        nodes5 = [hub, ca, cb] + [uuid4() for _ in range(3)]
        edges5 = [(hub, n) for n in nodes5[1:]]
        deltas5 = [_delta(1, ca, da), _delta(2, cb, db)] + [_delta(i + 3, n) for i, n in enumerate(nodes5[2:])]
        r = self._run(engine, _hyp(hub, [da, db]), deltas5, {da, db}, _ctx(nodes5, edges5))
        if r == CounterfactualConclusion.CAUSES_SYMPTOM:
            correct += 1

        # ── Scenario 6: Unrelated sibling → DOES_NOT_CAUSE ──
        hub6 = uuid4()
        nodes6, edges6 = _star_graph(hub6, 6)
        sibling = nodes6[5]
        sibling_did = uuid4()
        deltas6 = [_delta(i + 1, n) for i, n in enumerate(nodes6)]
        deltas6.append(_delta(len(nodes6) + 1, sibling, sibling_did))
        viol6 = {uuid4()}
        r = self._run(engine, _hyp(sibling, [sibling_did]), deltas6, viol6, _ctx(nodes6, edges6))
        if r == CounterfactualConclusion.DOES_NOT_CAUSE:
            correct += 1

        # ── Scenario 7: Service A failure → CAUSES_SYMPTOM ──
        svc_a = uuid4()
        svc_did = uuid4()
        nodes7, edges7 = _star_graph(svc_a, 5)
        deltas7 = [_delta(1, svc_a, svc_did)] + [_delta(i + 2, n) for i, n in enumerate(nodes7[1:])]
        r = self._run(engine, _hyp(svc_a, [svc_did]), deltas7, {svc_did}, _ctx(nodes7, edges7))
        if r == CounterfactualConclusion.CAUSES_SYMPTOM:
            correct += 1

        # ── Scenario 8: Healthy leaf removed → DOES_NOT_CAUSE ──
        hub8 = uuid4()
        nodes8, edges8 = _star_graph(hub8, 5)
        healthy = nodes8[4]
        healthy_did = uuid4()
        broken_did = uuid4()
        deltas8 = [_delta(1, hub8, broken_did)] + [_delta(i + 2, n) for i, n in enumerate(nodes8[1:])]
        deltas8.append(_delta(7, healthy, healthy_did))
        r = self._run(engine, _hyp(healthy, [healthy_did]), deltas8, {broken_did}, _ctx(nodes8, edges8))
        if r == CounterfactualConclusion.DOES_NOT_CAUSE:
            correct += 1

        # ── Scenario 9: Database root cause → CAUSES_SYMPTOM ──
        db = uuid4()
        db_did = uuid4()
        nodes9, edges9 = _star_graph(db, 5)
        deltas9 = [_delta(1, db, db_did)] + [_delta(i + 2, n) for i, n in enumerate(nodes9[1:])]
        r = self._run(engine, _hyp(db, [db_did]), deltas9, {db_did}, _ctx(nodes9, edges9))
        if r == CounterfactualConclusion.CAUSES_SYMPTOM:
            correct += 1

        # ── Scenario 10: Partial cause removes subset → CAUSES_SYMPTOM ──
        pc = uuid4()
        pc_did = uuid4()
        other_did = uuid4()
        nodes10, edges10 = _star_graph(pc, 5)
        deltas10 = [_delta(1, pc, pc_did)] + [_delta(i + 2, n) for i, n in enumerate(nodes10[1:])]
        deltas10.append(_delta(7, nodes10[3], other_did))
        r = self._run(engine, _hyp(pc, [pc_did]), deltas10, {pc_did, other_did}, _ctx(nodes10, edges10))
        if r == CounterfactualConclusion.CAUSES_SYMPTOM:
            correct += 1

        # ── GATE CHECK ──
        assert correct >= 7, (
            f"Counterfactual ground-truth gate FAILED: {correct}/10 correct, need >= 7"
        )

    def test_adaptive_boundary_expansion(self) -> None:
        """Boundary should expand when initial boundary too small."""
        boundary = AdaptiveSimulationBoundary(
            max_nodes=750, expansion_rounds=3, initial_threshold=0.3,
        )
        center = uuid4()
        neighbors = [uuid4() for _ in range(10)]
        all_nodes = [center] + neighbors
        edges = [(center, n) for n in neighbors]
        ctx = _ctx(all_nodes, edges)

        hyp = _hyp(center, [uuid4()])
        result_set, expansion_count = boundary.compute(hyp, ctx)

        assert center in result_set
        assert expansion_count >= 1

    def test_counterfactual_tracks_v33_fix2_fields(self) -> None:
        """CounterfactualScenario should have boundary_size, expansion_count, expansion_triggers."""
        engine = self._engine()
        node = uuid4()
        nodes, edges = _star_graph(node, 5)
        delta_id = uuid4()
        delta_log = [_delta(1, node, delta_id)] + [_delta(i + 2, n) for i, n in enumerate(nodes[1:])]
        hyp = _hyp(node, [delta_id])

        scenario = engine.validate_hypothesis(
            hyp, delta_log, {delta_id}, _ctx(nodes, edges), budget_ms=5000
        )

        assert scenario.boundary_size >= 3
        assert scenario.expansion_count >= 1
        assert isinstance(scenario.expansion_triggers, list)
