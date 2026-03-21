"""Integration test: Phase 4 end-to-end pipeline.

Tests the full flow: OSG Materializer -> Failure Propagation -> Temporal Ordering ->
Causal Bayesian Network -> Intervention Scoring -> Causal Discriminator ->
Counterfactual Engine -> Repair Planning -> Verification -> Certificate.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from src.causal.builder import CBNBuilder
from src.causal.cbn import CausalBayesianNetwork
from src.causal.discriminator import CausalDiscriminator
from src.causal.intervention import InterventionScorer
from src.certificate.generator import CertificateGenerator
from src.certificate.verifier import CertificateVerifier
from src.core.certificate import DiagnosisCertificate
from src.core.counterfactual import (
    CounterfactualConclusion,
    Intervention,
    InterventionType,
)
from src.core.derived import (
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)
from src.core.fact import AddEdge, AddNode, GraphDelta
from src.core.runtime_event import EventStatus, EventType, RuntimeEvent
from src.counterfactual.boundary import AdaptiveSimulationBoundary
from src.counterfactual.engine import CounterfactualEngine
from src.counterfactual.replay import DeltaReplayEngine
from src.osg.failure_propagation import FailurePropagationInferrer
from src.osg.materializer import OSGMaterializer
from src.osg.temporal_order import TemporalOrderer
from src.repair.discriminator import DeltaDebugger, SBFLRanker
from src.repair.planner import (
    RepairAction,
    RepairActionType,
    RepairPlanner,
    RepairTrajectory,
)
from src.repair.scoring import RepairScorer
from src.repair.verification import VerificationEngine, VerificationStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service_ids(n: int) -> list[UUID]:
    """Return *n* deterministic UUIDs representing services."""
    return [uuid4() for _ in range(n)]


def _make_runtime_event(
    source: UUID,
    target: UUID | None = None,
    event_type: EventType = EventType.SERVICE_CALL,
    status: EventStatus = EventStatus.SUCCESS,
    timestamp: datetime | None = None,
    trace_id: str | None = None,
    causal_predecessors: set[UUID] | None = None,
    anomaly_score: float = 0.0,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_type=event_type,
        source_service=source,
        target_service=target,
        status=status,
        timestamp=timestamp or datetime.utcnow(),
        trace_id=trace_id,
        causal_predecessors=causal_predecessors or set(),
        anomaly_score=anomaly_score,
    )


def _make_violation(
    entity_id: UUID | None = None,
    rule_id: str = "STR-001",
    entity_ids: list[UUID] | None = None,
    confidence: float = 0.8,
    trigger_delta_ids: list[UUID] | None = None,
    trigger_operations: list[str] | None = None,
) -> DerivedFact:
    payload: dict = {"rule_id": rule_id}
    if entity_id is not None:
        payload["entity_id"] = entity_id
    if entity_ids is not None:
        payload["entity_ids"] = entity_ids
    if trigger_delta_ids is not None:
        payload["trigger_delta_ids"] = trigger_delta_ids
    if trigger_operations is not None:
        payload["trigger_operations"] = trigger_operations
    return DerivedFact(
        derived_type=DerivedType.VIOLATION,
        payload=payload,
        justification=ExtendedJustification(rule_id=rule_id),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
    )


def _make_hypothesis(
    entity_id: UUID,
    entity_ids: list[UUID] | None = None,
    trigger_delta_ids: list[UUID] | None = None,
    confidence: float = 0.7,
) -> DerivedFact:
    payload: dict = {
        "entity_id": str(entity_id),
        "entity_ids": entity_ids or [entity_id],
        "trigger_delta_ids": trigger_delta_ids or [],
    }
    return DerivedFact(
        derived_type=DerivedType.HYPOTHESIS,
        payload=payload,
        justification=ExtendedJustification(rule_id="hyp-root"),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
    )


def _make_graph_context(
    node_ids: list[UUID],
    edges: list[tuple[UUID, UUID]],
    node_types: dict[UUID, str] | None = None,
    attention_scores: dict[UUID, float] | None = None,
) -> dict:
    """Build a ``graph_context`` dict consumable by CBNBuilder / Boundary."""
    node_types = node_types or {}
    attention_scores = attention_scores or {}
    nodes = []
    for nid in node_ids:
        nodes.append({
            "id": nid,
            "type": node_types.get(nid, "service"),
            "attention_score": attention_scores.get(nid, 0.5),
        })
    edge_list = []
    for src, tgt in edges:
        edge_list.append({"source": src, "target": tgt, "weight": 0.8})
    return {"nodes": nodes, "edges": edge_list}


def _make_delta(
    node_ids: list[UUID],
    seq: int = 0,
    source: str = "test",
) -> GraphDelta:
    ops = [
        AddNode(node_id=nid, node_type="service")
        for nid in node_ids
    ]
    return GraphDelta(
        sequence_number=seq,
        source=source,
        operations=ops,
    )


# ===========================================================================
# Test class
# ===========================================================================


class TestPhase4EndToEnd:
    """Full Phase 4 pipeline integration."""

    # -----------------------------------------------------------------------
    # 1. OSG full lifecycle
    # -----------------------------------------------------------------------
    def test_osg_full_lifecycle(self) -> None:
        """Create OSGMaterializer, process 10+ events across 3 services,
        verify nodes/edges created, check failure events, verify snapshot."""
        svc_a, svc_b, svc_c = _make_service_ids(3)
        osg = OSGMaterializer()
        base = datetime(2026, 3, 20, 12, 0, 0)

        events: list[RuntimeEvent] = []
        # 4 calls A -> B (1 failure)
        for i in range(4):
            status = EventStatus.FAILURE if i == 2 else EventStatus.SUCCESS
            events.append(_make_runtime_event(
                svc_a, svc_b,
                status=status,
                timestamp=base + timedelta(seconds=i),
                trace_id="trace-1",
            ))
        # 3 calls B -> C (1 timeout)
        for i in range(3):
            status = EventStatus.TIMEOUT if i == 1 else EventStatus.SUCCESS
            events.append(_make_runtime_event(
                svc_b, svc_c,
                status=status,
                timestamp=base + timedelta(seconds=4 + i),
                trace_id="trace-1",
            ))
        # 3 calls A -> C
        for i in range(3):
            events.append(_make_runtime_event(
                svc_a, svc_c,
                timestamp=base + timedelta(seconds=7 + i),
                trace_id="trace-2",
            ))
        # 1 internal state transition on B
        events.append(_make_runtime_event(
            svc_b,
            event_type=EventType.STATE_TRANSITION,
            timestamp=base + timedelta(seconds=10),
        ))

        for e in events:
            osg.process_event(e)

        # 3 service nodes
        assert osg.node_count == 3
        # edges: A->B, B->C, A->C
        assert osg.edge_count == 3
        assert osg.event_count == len(events)

        # Failure/timeout events
        failures = osg.get_failure_events()
        assert len(failures) == 2  # 1 FAILURE + 1 TIMEOUT

        # Snapshot round-trip
        snap = osg.snapshot()
        assert len(snap["nodes"]) == 3
        assert len(snap["edges"]) == 3

        # Service A node tracking
        node_a = osg.get_node(svc_a)
        assert node_a is not None
        assert node_a.event_count >= 7  # source of 7 events

    # -----------------------------------------------------------------------
    # 2. OSG causal chain pinning
    # -----------------------------------------------------------------------
    def test_osg_causal_chain_pinning(self) -> None:
        """Process events with causal_predecessors, verify auto-pinning,
        evict window, confirm pinned events survive (v3.3 B4)."""
        svc_x, svc_y = _make_service_ids(2)
        osg = OSGMaterializer(window_duration=timedelta(seconds=10))
        base = datetime(2026, 3, 20, 12, 0, 0)

        # Old event that will be outside window
        old_event = _make_runtime_event(
            svc_x, svc_y,
            timestamp=base,
            trace_id="trace-pin",
        )
        osg.process_event(old_event)

        # Recent event that references the old one as causal predecessor
        recent_event = _make_runtime_event(
            svc_y, svc_x,
            timestamp=base + timedelta(seconds=55),
            trace_id="trace-pin",
            causal_predecessors={old_event.event_id},
        )
        osg.process_event(recent_event)

        # Auto-pinning should have happened
        assert osg.is_pinned(old_event.event_id)
        assert osg.pinned_count >= 1

        # Evict with "now" far enough that old event is outside the 10s window
        # but recent event at base+55s is within window of base+60s
        evicted = osg.evict_window(now=base + timedelta(seconds=60))

        # Old event should survive because it is pinned
        remaining_ids = {e.event_id for e in osg.get_events()}
        assert old_event.event_id in remaining_ids
        # Recent event is also within window from "now"
        assert recent_event.event_id in remaining_ids

    # -----------------------------------------------------------------------
    # 3. Failure propagation inference
    # -----------------------------------------------------------------------
    def test_osg_failure_propagation_inference(self) -> None:
        """Create events with failures, use FailurePropagationInferrer to
        infer propagation, verify chains."""
        svc_a, svc_b, svc_c = _make_service_ids(3)
        osg = OSGMaterializer()
        base = datetime(2026, 3, 20, 12, 0, 0)

        # Root failure A -> B
        root = _make_runtime_event(
            svc_a, svc_b,
            status=EventStatus.FAILURE,
            timestamp=base,
            trace_id="trace-fail",
            anomaly_score=0.8,
        )
        osg.process_event(root)

        # Cascading failure B -> C that references root
        cascade = _make_runtime_event(
            svc_b, svc_c,
            status=EventStatus.FAILURE,
            timestamp=base + timedelta(milliseconds=200),
            trace_id="trace-fail",
            causal_predecessors={root.event_id},
            anomaly_score=0.7,
        )
        osg.process_event(cascade)

        # Another correlated failure on C
        correlated = _make_runtime_event(
            svc_c,
            event_type=EventType.CIRCUIT_BREAKER_TRIP,
            status=EventStatus.FAILURE,
            timestamp=base + timedelta(milliseconds=500),
            trace_id="trace-fail",
            anomaly_score=0.6,
        )
        osg.process_event(correlated)

        inferrer = FailurePropagationInferrer(
            osg, temporal_window_ms=5000.0, anomaly_threshold=0.3
        )

        # Infer single chain from root
        chain = inferrer.infer_propagation(root.event_id)
        assert chain.max_depth >= 1
        assert len(chain.chain_events) >= 2
        assert len(chain.affected_services) >= 2
        assert chain.confidence > 0.0

        # Infer propagation events in window
        prop_events = inferrer.infer_failure_propagation(
            base - timedelta(seconds=1),
            base + timedelta(seconds=5),
        )
        assert len(prop_events) >= 1
        assert all(
            e.event_type == EventType.FAILURE_PROPAGATION for e in prop_events
        )

    # -----------------------------------------------------------------------
    # 4. Temporal ordering
    # -----------------------------------------------------------------------
    def test_temporal_ordering(self) -> None:
        """Create events with causal dependencies, verify TemporalOrderer
        produces correct topological + temporal order."""
        svc_a, svc_b, svc_c = _make_service_ids(3)
        base = datetime(2026, 3, 20, 12, 0, 0)

        # e1 happens first (no predecessors)
        e1 = _make_runtime_event(svc_a, svc_b, timestamp=base)
        # e2 depends on e1
        e2 = _make_runtime_event(
            svc_b, svc_c,
            timestamp=base + timedelta(seconds=1),
            causal_predecessors={e1.event_id},
        )
        # e3 depends on e2
        e3 = _make_runtime_event(
            svc_c,
            timestamp=base + timedelta(seconds=2),
            causal_predecessors={e2.event_id},
        )
        # e4 is concurrent with e2 (no causal link, but later timestamp)
        e4 = _make_runtime_event(
            svc_a,
            timestamp=base + timedelta(seconds=3),
        )

        orderer = TemporalOrderer()
        # Deliberately pass in shuffled order
        ordered = orderer.order([e4, e3, e1, e2])

        # e1 must come before e2, e2 before e3
        idx = {e.event_id: i for i, e in enumerate(ordered)}
        assert idx[e1.event_id] < idx[e2.event_id]
        assert idx[e2.event_id] < idx[e3.event_id]

        # Logical clocks
        clocks = orderer.compute_logical_clocks([e1, e2, e3, e4])
        assert clocks[e1.event_id] < clocks[e2.event_id]
        assert clocks[e2.event_id] < clocks[e3.event_id]

        # Group by trace
        events_with_trace = [
            _make_runtime_event(svc_a, trace_id="t1", timestamp=base),
            _make_runtime_event(svc_b, trace_id="t1", timestamp=base + timedelta(seconds=1)),
            _make_runtime_event(svc_c, trace_id="t2", timestamp=base),
        ]
        groups = orderer.group_by_trace(events_with_trace)
        assert "t1" in groups
        assert "t2" in groups
        assert len(groups["t1"]) == 2

    # -----------------------------------------------------------------------
    # 5. CBN build and infer
    # -----------------------------------------------------------------------
    def test_cbn_build_and_infer(self) -> None:
        """Build CBN from graph context using CBNBuilder, run inference
        with evidence, verify posteriors change."""
        svc_a, svc_b, svc_c, svc_d = _make_service_ids(4)

        graph_ctx = _make_graph_context(
            node_ids=[svc_a, svc_b, svc_c, svc_d],
            edges=[(svc_a, svc_b), (svc_b, svc_c), (svc_a, svc_d)],
        )

        builder = CBNBuilder()
        cbn = builder.build_from_graph(graph_ctx)

        assert cbn.node_count == 4
        assert cbn.edge_count == 3

        # Baseline inference (no evidence)
        baseline = cbn.infer({})
        assert len(baseline) == 4
        assert all(0.0 <= v <= 1.0 for v in baseline.values())

        # Evidence: svc_a is definitely faulty
        evidence = {svc_a: 1.0}
        posterior = cbn.infer(evidence)

        # svc_a clamped
        assert posterior[svc_a] == pytest.approx(1.0)
        # Children should shift
        assert posterior[svc_b] != baseline[svc_b]

    # -----------------------------------------------------------------------
    # 6. Intervention scoring
    # -----------------------------------------------------------------------
    def test_intervention_scoring(self) -> None:
        """Build CBN, add edges, score candidates with InterventionScorer,
        verify the node with the most downstream influence ranks highest."""
        root, mid, leaf1, leaf2 = _make_service_ids(4)

        cbn = CausalBayesianNetwork()
        cbn.add_node(root, "service", prior=0.8)
        cbn.add_node(mid, "service", prior=0.5)
        cbn.add_node(leaf1, "service", prior=0.5)
        cbn.add_node(leaf2, "service", prior=0.5)
        cbn.add_edge(root, mid, weight=0.9)
        cbn.add_edge(mid, leaf1, weight=0.8)
        cbn.add_edge(mid, leaf2, weight=0.8)

        scorer = InterventionScorer()
        results = scorer.score_candidates(cbn, [root, mid, leaf1, leaf2])

        assert len(results) == 4
        # Results sorted descending
        assert results[0][1] >= results[1][1]

        # mid has 2 children, so do(mid=0) should have highest impact
        mid_score = next(s for nid, s in results if nid == mid)
        leaf1_score = next(s for nid, s in results if nid == leaf1)
        assert mid_score > leaf1_score

        # Causal effect
        effect = scorer.compute_causal_effect(cbn, root, leaf1)
        assert isinstance(effect, float)

    # -----------------------------------------------------------------------
    # 7. Causal discriminator
    # -----------------------------------------------------------------------
    def test_causal_discriminator(self) -> None:
        """Build CBN, create hypotheses, run CausalDiscriminator.discriminate,
        verify ranking favors the hypothesis with stronger causal links."""
        svc_a, svc_b, svc_c, svc_d = _make_service_ids(4)

        cbn = CausalBayesianNetwork()
        # svc_a is the real root cause connected to a violation node
        cbn.add_node(svc_a, "service", prior=0.8)
        cbn.add_node(svc_b, "service", prior=0.5)
        cbn.add_node(svc_c, "violation", prior=0.6)
        cbn.add_node(svc_d, "service", prior=0.3)
        cbn.add_edge(svc_a, svc_c, weight=0.9)
        cbn.add_edge(svc_b, svc_c, weight=0.2)
        # svc_d is unconnected to the violation

        hyp_strong = DerivedFact(
            derived_type=DerivedType.HYPOTHESIS,
            payload={"entity_ids": [svc_a]},
            justification=ExtendedJustification(rule_id="hyp-1"),
            confidence=0.8,
        )
        hyp_weak = DerivedFact(
            derived_type=DerivedType.HYPOTHESIS,
            payload={"entity_ids": [svc_d]},
            justification=ExtendedJustification(rule_id="hyp-2"),
            confidence=0.8,
        )

        disc = CausalDiscriminator()
        ranked = disc.discriminate([hyp_weak, hyp_strong], cbn)

        # The hypothesis with entity svc_a (strong link to violation) should rank first
        assert ranked[0].derived_id == hyp_strong.derived_id

        # rank_root_causes
        rc_ranking = disc.rank_root_causes(
            cbn,
            violation_ids=[svc_c],
            candidate_ids=[svc_a, svc_b, svc_d],
        )
        assert len(rc_ranking) >= 2
        # svc_a should rank highest
        assert rc_ranking[0][0] == svc_a

    # -----------------------------------------------------------------------
    # 8. Counterfactual adaptive boundary
    # -----------------------------------------------------------------------
    def test_counterfactual_adaptive_boundary(self) -> None:
        """Test AdaptiveSimulationBoundary with progressive expansion.
        When the initial 1-hop neighborhood is too small, the boundary
        should expand through 2-hop and 3-hop rounds."""
        # Build a chain: center -> n1 -> n2 -> n3 -> n4 -> n5
        center = uuid4()
        chain = [uuid4() for _ in range(5)]
        all_ids = [center] + chain

        edges = [(all_ids[i], all_ids[i + 1]) for i in range(len(all_ids) - 1)]
        # Reverse edges too (undirected graph)
        all_edges = edges + [(b, a) for a, b in edges]

        # Give all nodes high attention so they pass thresholds
        attention = {nid: 0.9 for nid in all_ids}
        graph_ctx = _make_graph_context(
            all_ids, all_edges, attention_scores=attention
        )

        # Hypothesis centered on the first node
        hyp = _make_hypothesis(center)

        # Start with _MIN_BOUNDARY = 5, so 1-hop (1 neighbor) is too small
        boundary_comp = AdaptiveSimulationBoundary(
            max_nodes=100, expansion_rounds=3, initial_threshold=0.1
        )
        boundary, expansion_count = boundary_comp.compute(hyp, graph_ctx)

        # Should have expanded at least once
        assert expansion_count >= 1
        # Center is always in boundary
        assert center in boundary
        # Should have included some chain nodes
        assert len(boundary) >= 2

        triggers = boundary_comp.get_expansion_triggers()
        assert isinstance(triggers, list)

    # -----------------------------------------------------------------------
    # 9. Counterfactual engine: CAUSES_SYMPTOM
    # -----------------------------------------------------------------------
    def test_counterfactual_engine_causes_symptom(self) -> None:
        """Set up a scenario where removing the cause delta eliminates the
        violation. Verify the engine concludes CAUSES_SYMPTOM."""
        cause_node = uuid4()
        effect_node = uuid4()
        observer_node = uuid4()
        all_ids = [cause_node, effect_node, observer_node]

        # Create a graph context with enough nodes for a valid boundary
        neighbors = [uuid4() for _ in range(6)]
        all_ids_extended = all_ids + neighbors
        edges = (
            [(cause_node, effect_node), (effect_node, observer_node)]
            + [(cause_node, n) for n in neighbors]
        )
        attention = {nid: 0.9 for nid in all_ids_extended}
        graph_ctx = _make_graph_context(
            all_ids_extended, edges, attention_scores=attention
        )

        # Delta log: one delta adds the cause node
        cause_delta = _make_delta([cause_node], seq=0)
        normal_delta = _make_delta([observer_node], seq=1)
        delta_log = [cause_delta, normal_delta]

        # The cause delta's ID is used as trigger_delta_id in the hypothesis
        hyp = _make_hypothesis(
            cause_node,
            trigger_delta_ids=[cause_delta.delta_id],
        )

        # Original violations include the cause delta id as a "violation"
        # (the engine checks if vid is in removed_delta_ids)
        original_violations = {cause_delta.delta_id}

        engine = CounterfactualEngine()
        scenario = engine.validate_hypothesis(
            hyp, delta_log, original_violations, graph_ctx, budget_ms=10000
        )

        assert scenario.conclusion == CounterfactualConclusion.CAUSES_SYMPTOM
        assert scenario.resulting_health_delta > 0.0
        assert scenario.boundary_size >= 3

    # -----------------------------------------------------------------------
    # 10. Counterfactual engine: DOES_NOT_CAUSE
    # -----------------------------------------------------------------------
    def test_counterfactual_engine_does_not_cause(self) -> None:
        """Set up a scenario where removing a benign delta does not help.
        Verify the engine concludes DOES_NOT_CAUSE."""
        benign_node = uuid4()
        violation_node = uuid4()
        all_ids = [benign_node, violation_node]

        neighbors = [uuid4() for _ in range(6)]
        all_ids_extended = all_ids + neighbors
        edges = [(benign_node, n) for n in neighbors]
        attention = {nid: 0.9 for nid in all_ids_extended}
        graph_ctx = _make_graph_context(
            all_ids_extended, edges, attention_scores=attention
        )

        # Delta log: benign delta and a different delta
        benign_delta = _make_delta([benign_node], seq=0)
        other_delta = _make_delta([violation_node], seq=1)
        delta_log = [benign_delta, other_delta]

        # Hypothesis targets the benign delta
        hyp = _make_hypothesis(
            benign_node,
            trigger_delta_ids=[benign_delta.delta_id],
        )

        # Violation is associated with violation_node, NOT with benign_delta
        original_violations = {violation_node}

        engine = CounterfactualEngine()
        scenario = engine.validate_hypothesis(
            hyp, delta_log, original_violations, graph_ctx, budget_ms=10000
        )

        assert scenario.conclusion == CounterfactualConclusion.DOES_NOT_CAUSE
        assert scenario.resulting_health_delta == 0.0

    # -----------------------------------------------------------------------
    # 11. Repair full pipeline
    # -----------------------------------------------------------------------
    def test_repair_full_pipeline(self) -> None:
        """Create violations, generate candidates via RepairPlanner,
        score with RepairScorer, verify with VerificationEngine."""
        entity_a = uuid4()
        entity_b = uuid4()

        violations = [
            _make_violation(
                entity_id=entity_a,
                rule_id="STR-001",
                trigger_operations=["add_node"],
            ),
            _make_violation(
                entity_id=entity_b,
                rule_id="DEP-002",
                trigger_operations=["remove_edge"],
            ),
        ]

        # Generate candidates
        planner = RepairPlanner()
        candidates = planner.generate_candidates(violations)
        assert len(candidates) >= 1, "Should generate at least one repair candidate"

        # Score candidates
        scorer = RepairScorer()
        scored = scorer.score_batch(candidates)
        assert len(scored) == len(candidates)
        # Verify sorted descending
        for i in range(len(scored) - 1):
            assert scored[i].score >= scored[i + 1].score

        # Verify the top candidate
        top = scored[0]
        engine = VerificationEngine()
        ver_result = engine.verify(top, context={
            "current_violations": {v.derived_id for v in violations},
            "simulated_violations": set(),  # repair fixes everything
            "test_results": {"smoke_test": True, "integration_test": True},
            "baseline_passing": {"check_a", "check_b"},
            "post_repair_passing": {"check_a", "check_b"},
        })

        assert ver_result.is_valid()
        assert ver_result.passed_count >= 4  # static + graph_law + dynamic(2) + regression + security

    # -----------------------------------------------------------------------
    # 12. Certificate round-trip
    # -----------------------------------------------------------------------
    def test_certificate_round_trip(self) -> None:
        """Generate certificate via CertificateGenerator with violations and
        counterfactuals, verify with CertificateVerifier, check round-trip
        serialization."""
        investigation_id = uuid4()
        entity = uuid4()

        violations = [
            _make_violation(entity_id=entity, rule_id="STR-001"),
            _make_violation(entity_id=entity, rule_id="STR-002"),
        ]

        root_cause = _make_hypothesis(entity, confidence=0.85)

        # Build a counterfactual scenario
        from src.core.counterfactual import CounterfactualScenario

        cf_scenario = CounterfactualScenario(
            base_state_checkpoint=0,
            intervention=Intervention(
                intervention_type=InterventionType.REMOVE_DELTA,
                target_deltas=[uuid4()],
            ),
            conclusion=CounterfactualConclusion.CAUSES_SYMPTOM,
            resulting_health_delta=0.5,
            boundary_size=10,
            expansion_count=2,
        )

        # Generate
        gen = CertificateGenerator()
        cert = gen.generate(
            investigation_id=investigation_id,
            violations=violations,
            root_cause=root_cause,
            repair_plan_ids=[uuid4()],
            counterfactuals=[cf_scenario],
            attention_scores={"svc-a": 0.9, "svc-b": 0.3},
            law_health_states={"STR-001": "healthy", "DEP-001": "degraded"},
            mandatory_ops=["causal_inference", "counterfactual_validation"],
            floor_budget_pct=42.0,
        )

        assert cert.incident_id == investigation_id
        assert len(cert.supporting_evidence) == 2
        assert len(cert.counterfactual_results) == 1
        assert cert.confidence > 0.0
        assert cert.floor_budget_consumed_pct == 42.0
        assert len(cert.mandatory_ops_executed) == 2

        # Verify
        verifier = CertificateVerifier()
        result = verifier.verify(cert)
        assert result.is_valid
        assert result.checks_performed == 5
        assert result.checks_passed >= 4  # serialization should pass

        # Round-trip serialization
        serialized = cert.model_dump_json()
        deserialized = DiagnosisCertificate.model_validate_json(serialized)
        assert deserialized.certificate_id == cert.certificate_id
        assert deserialized.incident_id == cert.incident_id
        assert deserialized.confidence == cert.confidence
        assert len(deserialized.counterfactual_results) == 1

    # -----------------------------------------------------------------------
    # 13. Repair planner 5 strategies
    # -----------------------------------------------------------------------
    def test_repair_planner_5_strategies(self) -> None:
        """Create diverse violations, verify RepairPlanner exercises all
        5 strategies (template, inverse, dependency, config, composite)."""
        entity_ids = [uuid4() for _ in range(8)]

        violations = [
            # Inverse strategy -- has trigger_operations with "add_"
            _make_violation(
                entity_id=entity_ids[0],
                rule_id="STR-001",
                trigger_operations=["add_node"],
            ),
            _make_violation(
                entity_id=entity_ids[1],
                rule_id="STR-002",
                trigger_operations=["remove_edge"],
            ),
            # Dependency strategy -- rule_id contains "dep" / "circular" / "orphan"
            _make_violation(
                entity_id=entity_ids[2],
                rule_id="circular-dep-001",
            ),
            _make_violation(
                entity_id=entity_ids[3],
                rule_id="orphan-component-002",
            ),
            # Config strategy -- rule_id contains "config" / "threshold" / "param"
            _make_violation(
                entity_id=entity_ids[4],
                rule_id="config-timeout-001",
            ),
            _make_violation(
                entity_id=entity_ids[5],
                rule_id="threshold-breach-002",
            ),
            # Composite strategy needs 2+ violations sharing a rule_id prefix
            _make_violation(
                entity_id=entity_ids[6],
                rule_id="STR-003",
                trigger_operations=["update_attribute"],
            ),
            _make_violation(
                entity_id=entity_ids[7],
                rule_id="STR-004",
                trigger_operations=["update_attribute"],
            ),
        ]

        planner = RepairPlanner()
        candidates = planner.generate_candidates(violations)

        strategies_used = {c.strategy for c in candidates}
        assert "inverse" in strategies_used, (
            f"Inverse strategy missing; got {strategies_used}"
        )
        assert "dependency" in strategies_used, (
            f"Dependency strategy missing; got {strategies_used}"
        )
        assert "config" in strategies_used, (
            f"Config strategy missing; got {strategies_used}"
        )
        assert "composite" in strategies_used, (
            f"Composite strategy missing; got {strategies_used}"
        )
        # Template needs repair_templates in context, test it separately
        # to confirm it at least runs without error
        template_ctx = {
            "repair_templates": [],  # no templates => no candidates, but no crash
        }
        planner.generate_candidates(violations, context=template_ctx)

        # Verify per-strategy cap
        for strat in strategies_used:
            count = sum(1 for c in candidates if c.strategy == strat)
            assert count <= 20, f"Strategy {strat} produced {count} > 20 candidates"

        # Verify total cap
        assert len(candidates) <= 100

    # -----------------------------------------------------------------------
    # 14. Verification engine 5 modalities
    # -----------------------------------------------------------------------
    def test_verification_5_modalities(self) -> None:
        """Verify VerificationEngine runs all 5 verification modalities
        and each produces at least one check."""
        entity = uuid4()
        trajectory = RepairTrajectory(
            violation_ids=[uuid4(), uuid4()],
            actions=[
                RepairAction(
                    action_type=RepairActionType.UPDATE_ATTRIBUTE,
                    target_entity=entity,
                    parameters={
                        "key": "status",
                        "old_value": "broken",
                        "new_value": "fixed",
                    },
                    description="Fix broken status",
                    confidence=0.7,
                    risk=0.3,
                ),
                RepairAction(
                    action_type=RepairActionType.ADD_EDGE,
                    target_entity=entity,
                    parameters={
                        "target_id": str(uuid4()),
                        "edge_type": "depends_on",
                    },
                    description="Add missing dependency",
                    confidence=0.6,
                    risk=0.2,
                ),
            ],
            strategy="inverse",
            confidence=0.7,
            risk=0.3,
        )

        engine = VerificationEngine()
        result = engine.verify(trajectory, context={
            "current_violations": {uuid4()},
            "simulated_violations": set(),
            "test_results": {
                "unit_test_1": True,
                "unit_test_2": True,
                "smoke_test": True,
            },
            "baseline_passing": {"check_a", "check_b", "check_c"},
            "post_repair_passing": {"check_a", "check_b", "check_c"},
        })

        # Collect modalities that produced checks
        modalities_seen = {c.modality for c in result.checks}
        assert "static" in modalities_seen, "Static modality missing"
        assert "graph_law" in modalities_seen, "Graph law modality missing"
        assert "dynamic" in modalities_seen, "Dynamic modality missing"
        assert "regression" in modalities_seen, "Regression modality missing"
        assert "security" in modalities_seen, "Security modality missing"

        # All 5 modalities should have at least one check
        for mod in ("static", "graph_law", "dynamic", "regression", "security"):
            count = sum(1 for c in result.checks if c.modality == mod)
            assert count >= 1, f"Modality '{mod}' produced 0 checks"

        # With clean inputs, result should be valid
        assert result.is_valid()
        assert result.failed_count == 0

    # -----------------------------------------------------------------------
    # Integration: OSG -> Causal -> Counterfactual -> Repair -> Certificate
    # -----------------------------------------------------------------------
    def test_full_phase4_integration_pipeline(self) -> None:
        """End-to-end: ingest events in OSG, build CBN, run counterfactual,
        generate repair, verify, and produce a certificate."""
        # 1. OSG: ingest events
        svc_db, svc_api, svc_web = _make_service_ids(3)
        osg = OSGMaterializer()
        base = datetime(2026, 3, 20, 12, 0, 0)

        root_fail = _make_runtime_event(
            svc_db, svc_api,
            status=EventStatus.FAILURE,
            timestamp=base,
            trace_id="incident-1",
            anomaly_score=0.9,
        )
        osg.process_event(root_fail)

        cascade_fail = _make_runtime_event(
            svc_api, svc_web,
            status=EventStatus.FAILURE,
            timestamp=base + timedelta(milliseconds=100),
            trace_id="incident-1",
            causal_predecessors={root_fail.event_id},
            anomaly_score=0.7,
        )
        osg.process_event(cascade_fail)

        # Some normal traffic
        for i in range(5):
            osg.process_event(_make_runtime_event(
                svc_web, svc_api,
                timestamp=base + timedelta(seconds=1 + i),
                trace_id="incident-1",
            ))

        # 2. Build CBN from graph context
        snap = osg.snapshot()
        # Convert snapshot to graph_context format
        node_ids_map: dict[str, UUID] = {}
        gc_nodes = []
        for n in snap["nodes"]:
            uid = UUID(n["service_id"])
            node_ids_map[n["service_id"]] = uid
            gc_nodes.append({
                "id": uid,
                "type": "service",
            })
        gc_edges = []
        for e in snap["edges"]:
            gc_edges.append({
                "source": UUID(e["source"]),
                "target": UUID(e["target"]),
                "weight": 0.8,
            })

        builder = CBNBuilder()
        cbn = builder.build_from_graph({"nodes": gc_nodes, "edges": gc_edges})
        assert cbn.node_count == 3

        # 3. Run intervention scoring
        scorer = InterventionScorer()
        results = scorer.score_candidates(cbn, [svc_db, svc_api, svc_web])
        assert len(results) == 3

        # 4. Create violations and hypothesis
        violation = _make_violation(entity_id=svc_db, rule_id="DB-FAILURE")
        cause_delta = _make_delta([svc_db], seq=0)
        hyp = _make_hypothesis(
            svc_db,
            trigger_delta_ids=[cause_delta.delta_id],
        )

        # 5. Counterfactual validation
        # Build extended graph context for boundary computation
        neighbors = [uuid4() for _ in range(6)]
        all_ids = [svc_db, svc_api, svc_web] + neighbors
        ctx_edges = (
            [(svc_db, svc_api), (svc_api, svc_web)]
            + [(svc_db, n) for n in neighbors]
        )
        attention = {nid: 0.8 for nid in all_ids}
        ext_graph_ctx = _make_graph_context(
            all_ids, ctx_edges, attention_scores=attention
        )

        cf_engine = CounterfactualEngine()
        scenario = cf_engine.validate_hypothesis(
            hyp,
            [cause_delta, _make_delta([svc_web], seq=1)],
            {cause_delta.delta_id},
            ext_graph_ctx,
            budget_ms=10000,
        )
        assert scenario.conclusion == CounterfactualConclusion.CAUSES_SYMPTOM

        # 6. Repair planning
        planner = RepairPlanner()
        repair_candidates = planner.generate_candidates([violation])
        assert len(repair_candidates) >= 1

        repair_scorer = RepairScorer()
        scored_repairs = repair_scorer.score_batch(repair_candidates)
        top_repair = scored_repairs[0]

        # 7. Verification
        ver_engine = VerificationEngine()
        ver_result = ver_engine.verify(top_repair)
        assert ver_result.passed_count >= 1

        # 8. Certificate
        cert_gen = CertificateGenerator()
        cert = cert_gen.generate(
            investigation_id=uuid4(),
            violations=[violation],
            root_cause=hyp,
            repair_plan_ids=[top_repair.trajectory_id],
            counterfactuals=[scenario],
            attention_scores={"svc_db": 0.9},
            law_health_states={"DB-FAILURE": "healthy"},
        )

        assert cert.confidence > 0.0
        assert len(cert.counterfactual_results) == 1
        assert len(cert.repair_plan_ids) == 1

        cert_verifier = CertificateVerifier()
        cert_result = cert_verifier.verify(cert)
        assert cert_result.is_valid

    # -----------------------------------------------------------------------
    # Bonus: DeltaDebugger and SBFLRanker from repair.discriminator
    # -----------------------------------------------------------------------
    def test_delta_debugger_minimization(self) -> None:
        """DeltaDebugger should minimize a failing delta set."""
        deltas = [_make_delta([uuid4()], seq=i) for i in range(10)]
        # The "failing" delta is at index 3
        failing_id = deltas[3].delta_id

        def test_fn(subset: list[GraphDelta]) -> bool:
            return any(d.delta_id == failing_id for d in subset)

        debugger = DeltaDebugger(max_iterations=20)
        minimal = debugger.minimize(deltas, test_fn)

        # Should find just the one failing delta
        assert len(minimal) == 1
        assert minimal[0].delta_id == failing_id

    def test_sbfl_ranker_suspiciousness(self) -> None:
        """SBFLRanker should rank entities involved in failures higher."""
        entity_a = uuid4()
        entity_b = uuid4()
        entity_c = uuid4()

        ranker = SBFLRanker()
        # entity_a appears in all failing tests
        ranker.record_failing({entity_a, entity_b})
        ranker.record_failing({entity_a, entity_c})
        ranker.record_failing({entity_a})
        # entity_b appears in passing tests too
        ranker.record_passing({entity_b, entity_c})
        ranker.record_passing({entity_b})

        scores = ranker.rank()
        assert len(scores) == 3

        # entity_a should be most suspicious (only in failures)
        assert scores[0].entity_id == entity_a
        assert scores[0].combined > 0.0
        assert scores[0].ochiai > 0.0
