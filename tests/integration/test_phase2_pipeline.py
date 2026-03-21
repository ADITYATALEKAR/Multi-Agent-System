"""Integration test: Phase 2 end-to-end pipeline.

Tests the full flow: GraphDelta → DFE/Rete → LawEvaluator → DerivedFact →
DerivedFactStore → TMS → BeliefNode → ConfidencePropagation →
EnergyScorer → HypothesisEngine → Self-Improving feedback.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.derived import DerivedFact, DerivedType, DerivedStatus, ExtendedJustification
from src.core.fact import AddNode, AddEdge, GraphDelta
from src.dfe.attention import GraphAttentionLayer
from src.dfe.compiler import RuleCompiler
from src.dfe.derived_store import DerivedFactStore, DerivedFactEmitter
from src.dfe.rete import ReteNetwork
from src.law_engine.evaluator import LawEvaluator
from src.law_engine.governance import LawGovernance, HEALTH_ACTIVE, HEALTH_QUARANTINED, HEALTH_DEGRADED
from src.law_engine.law import LawCategory, LawDefinition
from src.law_engine.library import LawLibrary
from src.scoring.energy import EnergyScorer
from src.tms.engine import TMSEngine
from src.hypothesis.strategies import LawLocalStrategy, TemporalStrategy
from src.hypothesis.aggregator import HypothesisAggregator
from src.self_improving.outcome_tracker import OutcomeTracker, OutcomeRecord
from src.self_improving.law_weight_updater import LawWeightUpdater
from src.cost_aware.cost_tracker import OperationCostTracker


def _make_delta(*ops, seq: int = 1) -> GraphDelta:
    return GraphDelta(operations=list(ops), sequence_number=seq, source="integration_test")


class TestPhase2EndToEnd:
    """Full Phase 2 pipeline integration."""

    def test_dfe_to_tms_pipeline(self) -> None:
        """Delta → Rete → DerivedFact → DerivedStore → TMS → BeliefNode."""
        # Setup
        rete = ReteNetwork()
        compiler = RuleCompiler()
        tms = TMSEngine()
        store = DerivedFactStore()
        emitter = DerivedFactEmitter(store, tms)

        # Register a simple rule
        rule_def = {
            "rule_id": "test-circular-dep",
            "name": "Circular Dependency",
            "conditions": [{"entity": "node", "type": "cycle", "bind": "c"}],
            "action": {"type": "violation", "message": "Cycle: $c", "confidence": 0.9},
        }
        rule_ir = compiler.compile(rule_def)
        rete.register_rule(rule_ir)

        # Evaluate delta with a cycle node
        node_id = uuid4()
        delta = _make_delta(AddNode(node_id=node_id, node_type="cycle", attributes={"name": "A→B→A"}))
        derived = rete.evaluate(delta)

        assert len(derived) == 1
        fact = derived[0]
        assert fact.derived_type == DerivedType.VIOLATION
        assert fact.confidence == 0.9

        # Emit to store + TMS
        emitter.emit(fact)

        # Verify store
        assert store.count == 1
        stored = store.get(fact.derived_id)
        assert stored is not None
        assert stored.status == DerivedStatus.SUPPORTED

        # Verify TMS
        belief = tms.get_belief_for_fact(fact.derived_id)
        assert belief is not None
        assert belief.is_in()
        assert belief.tenant_id == "default"

    def test_law_evaluator_full_pipeline(self) -> None:
        """LawLibrary → LawEvaluator → Rete → violations."""
        rete = ReteNetwork()
        compiler = RuleCompiler()
        library = LawLibrary()

        evaluator = LawEvaluator(rete, compiler, library)
        evaluator.register_laws()

        assert rete.rule_count >= 100

        # Fire a class node — should match naming and structural rules
        node_id = uuid4()
        delta = _make_delta(
            AddNode(node_id=node_id, node_type="class", attributes={"name": "MyClass"}),
            seq=2,
        )
        violations = evaluator.evaluate_delta(delta)
        assert len(violations) > 0

        # All violations should be DerivedFact with SUPPORTED status
        for v in violations:
            assert isinstance(v, DerivedFact)
            assert v.status == DerivedStatus.SUPPORTED

    def test_attention_updates_on_delta(self) -> None:
        """GraphAttentionLayer recomputes scores on delta."""
        gal = GraphAttentionLayer()
        node_id = uuid4()
        delta = _make_delta(
            AddNode(node_id=node_id, node_type="service", attributes={"name": "api-gw"}),
        )

        scores = gal.recompute_affected(delta)
        assert node_id in scores
        assert scores[node_id] > 0  # should have base + recency score

        # Violation boost
        gal.boost_for_violation(node_id)
        score_after = gal.compute_score(node_id)
        assert score_after > scores[node_id]

    def test_tms_retraction_cascade(self) -> None:
        """TMS retraction propagates through dependent beliefs."""
        tms = TMSEngine()

        # Create base fact
        base_fact = DerivedFact(
            derived_type=DerivedType.VIOLATION,
            payload={"rule_id": "base-rule"},
            justification=ExtendedJustification(rule_id="base-rule"),
            status=DerivedStatus.SUPPORTED,
            confidence=0.9,
        )
        base_just = base_fact.justification
        base_belief = tms.register_belief(base_fact, base_just, tenant_id="test-tenant")

        # Create dependent fact that supports on base
        dep_fact = DerivedFact(
            derived_type=DerivedType.HYPOTHESIS,
            payload={"rule_id": "dep-rule"},
            justification=ExtendedJustification(
                rule_id="dep-rule",
                supporting_facts={base_fact.derived_id},
            ),
            status=DerivedStatus.SUPPORTED,
            confidence=0.7,
        )
        dep_belief = tms.register_belief(dep_fact, dep_fact.justification, tenant_id="test-tenant")

        # Retract base
        transitioned = tms.retract_support(base_fact.derived_id)
        assert base_belief.belief_id in transitioned

        # Base should be OUT
        status, conf = tms.get_belief_status(base_belief.belief_id)
        assert status == "OUT"
        assert conf == 0.0

    def test_energy_scorer_with_violations(self) -> None:
        """EnergyScorer produces directionally correct HealthVector."""
        scorer = EnergyScorer()

        # No violations = healthy
        hv0 = scorer.compute([])
        assert hv0.overall_score == 1.0
        assert hv0.violation_count == 0

        # Some violations = degraded
        violations = []
        for i in range(5):
            violations.append(DerivedFact(
                derived_type=DerivedType.VIOLATION,
                payload={"rule_id": f"struct-rule-{i}"},
                justification=ExtendedJustification(rule_id=f"struct-rule-{i}"),
                status=DerivedStatus.SUPPORTED,
                confidence=0.8,
            ))
        hv = scorer.compute(violations)
        assert hv.overall_score < 1.0
        assert hv.violation_count == 5

    def test_hypothesis_aggregator_pipeline(self) -> None:
        """HypothesisAggregator runs strategies and produces ranked results."""
        strategies = [LawLocalStrategy(), TemporalStrategy()]
        aggregator = HypothesisAggregator(strategies=strategies)

        violations = []
        for i in range(3):
            violations.append(DerivedFact(
                derived_type=DerivedType.VIOLATION,
                payload={"rule_id": "same-rule", "entity_id": str(uuid4())},
                justification=ExtendedJustification(rule_id="same-rule"),
                status=DerivedStatus.SUPPORTED,
                confidence=0.8,
            ))

        hypotheses = aggregator.aggregate(violations)
        assert len(hypotheses) > 0
        # LawLocal should produce at least one hypothesis grouping the 3 violations
        assert any(h.strategy_id == "law_local" for h in hypotheses)
        # Results should be sorted by confidence
        for i in range(len(hypotheses) - 1):
            assert hypotheses[i].confidence >= hypotheses[i + 1].confidence

    def test_self_improving_feedback_loop(self) -> None:
        """OutcomeTracker + LawWeightUpdater form a feedback loop."""
        tracker = OutcomeTracker()
        updater = LawWeightUpdater()

        target_id = uuid4()

        # Record some outcomes
        for _ in range(3):
            tracker.record(OutcomeRecord(
                target_id=target_id,
                record_type="law_evaluation",
                outcome="correct",
            ))
        tracker.record(OutcomeRecord(
            target_id=target_id,
            record_type="law_evaluation",
            outcome="incorrect",
        ))

        rate = tracker.get_success_rate(target_id, "law_evaluation")
        assert rate == 0.75

        # Weight update based on outcome
        new_w = updater.update("STR-001", was_correct=True, current_weight=1.0)
        assert new_w > 1.0
        new_w2 = updater.update("STR-001", was_correct=False, current_weight=new_w)
        assert new_w2 < new_w

    def test_cost_tracker_budget(self) -> None:
        """OperationCostTracker tracks costs and budget."""
        cost = OperationCostTracker()
        cost.record("dfe_evaluate", cost=10.0, duration_ms=5.0)
        cost.record("dfe_evaluate", cost=20.0, duration_ms=8.0)

        assert cost.get_total_cost() == 30.0
        assert cost.get_budget_remaining(100.0) == 70.0
        assert not cost.is_budget_exhausted(100.0)

        stats = cost.get_stats("dfe_evaluate")
        assert stats.count == 2
        assert stats.mean == 15.0

    def test_law_governance_auto_quarantine(self) -> None:
        """LawGovernance escalates to REVIEW_REQUIRED (v3.3 Fix 3)."""
        gov = LawGovernance(window_size=10, failure_threshold=0.8)

        # Record 9 failures + 1 success = 90% failure rate > 80% threshold
        for i in range(9):
            gov.record_evaluation("SEC-001", success=False)
        gov.record_evaluation("SEC-001", success=True)

        state = gov.check_health("SEC-001")
        # v3.3 Fix 3: auto-escalates to REVIEW_REQUIRED (maps to degraded)
        assert state == HEALTH_DEGRADED
        assert "SEC-001" in gov.get_review_required_laws()

        # Manual quarantine + restore
        gov.approve_quarantine("SEC-001", "test-reviewer")
        assert gov.get_health("SEC-001") == HEALTH_QUARANTINED

        gov.restore("SEC-001")
        assert gov.get_health("SEC-001") == HEALTH_ACTIVE

    def test_derived_store_emitter_retraction(self) -> None:
        """DerivedFactEmitter retraction updates store status."""
        store = DerivedFactStore()
        emitter = DerivedFactEmitter(store)

        fact = DerivedFact(
            derived_type=DerivedType.VIOLATION,
            payload={"rule_id": "test"},
            justification=ExtendedJustification(rule_id="test"),
            status=DerivedStatus.SUPPORTED,
            confidence=0.8,
        )
        emitter.emit(fact)
        assert store.count == 1

        emitter.retract(fact.derived_id)
        retracted = store.get(fact.derived_id)
        assert retracted is not None
        assert retracted.status == DerivedStatus.RETRACTED
