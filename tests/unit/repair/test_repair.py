"""Unit tests for the repair module: discriminator, planner, scoring, verification."""

from __future__ import annotations

import math
from uuid import UUID, uuid4

import pytest

from src.core.derived import (
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)
from src.core.fact import AddNode, GraphDelta
from src.repair.discriminator import DeltaDebugger, SBFLRanker
from src.repair.planner import (
    DependencyStrategy,
    InverseStrategy,
    RepairAction,
    RepairActionType,
    RepairPlanner,
    RepairTrajectory,
)
from src.repair.scoring import RepairScorer
from src.repair.verification import (
    GraphLawVerifier,
    SecurityVerifier,
    StaticVerifier,
    VerificationEngine,
    VerificationStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_justification() -> ExtendedJustification:
    return ExtendedJustification(rule_id="test-rule")


def _make_violation(
    entity_id: UUID | None = None,
    rule_id: str = "test-rule-001",
    trigger_operations: list[str] | None = None,
    extra: dict | None = None,
) -> DerivedFact:
    payload: dict = {
        "entity_id": str(entity_id) if entity_id else str(uuid4()),
        "rule_id": rule_id,
        "trigger_operations": trigger_operations or [],
    }
    if extra:
        payload.update(extra)
    return DerivedFact(
        derived_type=DerivedType.VIOLATION,
        payload=payload,
        justification=_make_justification(),
        status=DerivedStatus.SUPPORTED,
        confidence=0.7,
    )


def _make_trajectory(
    actions: list[RepairAction] | None = None,
    strategy: str = "test",
    confidence: float = 0.6,
    risk: float = 0.3,
    violation_ids: list[UUID] | None = None,
) -> RepairTrajectory:
    return RepairTrajectory(
        violation_ids=violation_ids or [uuid4()],
        actions=actions or [],
        strategy=strategy,
        confidence=confidence,
        risk=risk,
    )


def _make_action(
    action_type: RepairActionType = RepairActionType.UPDATE_ATTRIBUTE,
    confidence: float = 0.6,
    risk: float = 0.3,
    parameters: dict | None = None,
    description: str = "test action",
) -> RepairAction:
    return RepairAction(
        action_type=action_type,
        target_entity=uuid4(),
        parameters=parameters or {"key": "status", "old_value": "bad", "new_value": "good"},
        description=description,
        confidence=confidence,
        risk=risk,
    )


def _make_delta(node_id: UUID, seq: int = 0) -> GraphDelta:
    return GraphDelta(
        sequence_number=seq,
        source="test",
        operations=[AddNode(node_id=node_id, node_type="service")],
    )


# ===================================================================
# DeltaDebugger tests
# ===================================================================


class TestDeltaDebugger:
    """Tests for DeltaDebugger."""

    def test_minimize_finds_minimal_failing_subset(self) -> None:
        debugger = DeltaDebugger()
        nodes = [uuid4() for _ in range(6)]
        deltas = [_make_delta(n, seq=i) for i, n in enumerate(nodes)]

        # The "failing" delta is the one at index 3
        failing_delta = deltas[3]

        def test_fn(subset: list[GraphDelta]) -> bool:
            return failing_delta in subset

        result = debugger.minimize(deltas, test_fn)
        assert failing_delta in result
        assert len(result) <= len(deltas)
        # ddmin should ideally isolate to just the failing delta
        assert len(result) == 1

    def test_minimize_single_delta(self) -> None:
        debugger = DeltaDebugger()
        d = _make_delta(uuid4())
        result = debugger.minimize([d], lambda subset: True)
        assert result == [d]

    def test_minimize_returns_full_set_if_no_failure(self) -> None:
        debugger = DeltaDebugger()
        deltas = [_make_delta(uuid4(), i) for i in range(4)]
        # test_fn always returns False -> can't minimize
        result = debugger.minimize(deltas, lambda subset: False)
        assert result == deltas


# ===================================================================
# SBFLRanker tests
# ===================================================================


class TestSBFLRanker:
    """Tests for SBFLRanker."""

    def test_record_passing_and_failing_then_rank(self) -> None:
        ranker = SBFLRanker()
        e1, e2, e3 = uuid4(), uuid4(), uuid4()

        # e1 is in all failing tests, e2 in both, e3 only in passing
        ranker.record_failing({e1, e2})
        ranker.record_failing({e1})
        ranker.record_passing({e2, e3})
        ranker.record_passing({e3})

        scores = ranker.rank()
        assert len(scores) == 3
        # e1 is most suspicious (in all 2 failures, 0 passes)
        assert scores[0].entity_id == e1
        assert scores[0].combined > 0

    def test_ochiai_formula_correctness(self) -> None:
        """Verify the Ochiai formula: ef / sqrt((ef+nf) * (ef+ep))."""
        ranker = SBFLRanker()
        entity = uuid4()
        other = uuid4()

        # 3 failing tests involving entity, 1 failing not involving entity
        ranker.record_failing({entity})
        ranker.record_failing({entity})
        ranker.record_failing({entity})
        ranker.record_failing({other})
        # 1 passing test involving entity
        ranker.record_passing({entity})

        scores = ranker.rank()
        entity_score = next(s for s in scores if s.entity_id == entity)

        ef = 3  # failed tests with entity
        ep = 1  # passed tests with entity
        nf = 1  # failed tests without entity (total_fail - ef = 4 - 3 = 1)
        expected_ochiai = ef / math.sqrt((ef + nf) * (ef + ep))

        assert abs(entity_score.ochiai - expected_ochiai) < 1e-9

    def test_rank_empty(self) -> None:
        ranker = SBFLRanker()
        assert ranker.rank() == []


# ===================================================================
# RepairPlanner tests
# ===================================================================


class TestRepairPlanner:
    """Tests for RepairPlanner."""

    def test_generate_candidates_returns_list(self) -> None:
        planner = RepairPlanner()
        entity_id = uuid4()
        violations = [
            _make_violation(entity_id=entity_id, trigger_operations=["add_node"]),
        ]
        candidates = planner.generate_candidates(violations)
        assert isinstance(candidates, list)
        for c in candidates:
            assert isinstance(c, RepairTrajectory)

    def test_max_total_candidates_cap(self) -> None:
        """Total candidates should be capped at MAX_TOTAL_CANDIDATES=100."""
        planner = RepairPlanner()
        # Create many violations to generate many candidates
        violations = [
            _make_violation(
                entity_id=uuid4(),
                rule_id=f"dep-rule-{i}",
                trigger_operations=["add_node"],
            )
            for i in range(150)
        ]
        candidates = planner.generate_candidates(violations)
        assert len(candidates) <= RepairPlanner.MAX_TOTAL_CANDIDATES


# ===================================================================
# InverseStrategy tests
# ===================================================================


class TestInverseStrategy:
    """Tests for InverseStrategy."""

    def test_generates_inverse_remove_for_add(self) -> None:
        strategy = InverseStrategy()
        entity_id = uuid4()
        violations = [
            _make_violation(
                entity_id=entity_id,
                trigger_operations=["add_node"],
            ),
        ]
        candidates = strategy.generate(violations, {})
        assert len(candidates) >= 1
        # The inverse of add should be remove
        action_types = {a.action_type for c in candidates for a in c.actions}
        assert RepairActionType.REMOVE_NODE in action_types

    def test_generates_inverse_add_for_remove(self) -> None:
        strategy = InverseStrategy()
        entity_id = uuid4()
        violations = [
            _make_violation(
                entity_id=entity_id,
                trigger_operations=["remove_node"],
            ),
        ]
        candidates = strategy.generate(violations, {})
        assert len(candidates) >= 1
        action_types = {a.action_type for c in candidates for a in c.actions}
        assert RepairActionType.ADD_NODE in action_types

    def test_generates_attribute_update_by_default(self) -> None:
        strategy = InverseStrategy()
        entity_id = uuid4()
        violations = [
            _make_violation(
                entity_id=entity_id,
                trigger_operations=["something_else"],
            ),
        ]
        candidates = strategy.generate(violations, {})
        assert len(candidates) >= 1
        action_types = {a.action_type for c in candidates for a in c.actions}
        assert RepairActionType.UPDATE_ATTRIBUTE in action_types


# ===================================================================
# DependencyStrategy tests
# ===================================================================


class TestDependencyStrategy:
    """Tests for DependencyStrategy."""

    def test_generates_edge_operations_for_dep_violations(self) -> None:
        strategy = DependencyStrategy()
        entity_id = uuid4()
        violations = [
            _make_violation(
                entity_id=entity_id,
                rule_id="missing-dep-001",
            ),
        ]
        candidates = strategy.generate(violations, {})
        assert len(candidates) >= 1
        action_types = {a.action_type for c in candidates for a in c.actions}
        assert RepairActionType.ADD_EDGE in action_types

    def test_generates_remove_edge_for_circular(self) -> None:
        strategy = DependencyStrategy()
        entity_id = uuid4()
        edge_id = uuid4()
        violations = [
            _make_violation(
                entity_id=entity_id,
                rule_id="circular-dep-002",
                extra={"edge_id": str(edge_id)},
            ),
        ]
        candidates = strategy.generate(violations, {})
        assert len(candidates) >= 1
        action_types = {a.action_type for c in candidates for a in c.actions}
        assert RepairActionType.REMOVE_EDGE in action_types


# ===================================================================
# RepairScorer tests
# ===================================================================


class TestRepairScorer:
    """Tests for RepairScorer."""

    def test_score_returns_float_and_updates_trajectory(self) -> None:
        scorer = RepairScorer()
        action = _make_action(confidence=0.7, risk=0.2)
        traj = _make_trajectory(actions=[action], confidence=0.7, risk=0.2)

        result = scorer.score(traj)
        assert isinstance(result, float)
        assert traj.score == result

    def test_score_batch_sorts_by_j_descending(self) -> None:
        scorer = RepairScorer()
        # High-quality trajectory: high confidence, low risk
        good_action = _make_action(confidence=0.9, risk=0.1)
        good = _make_trajectory(
            actions=[good_action], confidence=0.9, risk=0.1,
            violation_ids=[uuid4(), uuid4()],
        )
        # Low-quality trajectory: low confidence, high risk
        bad_action = _make_action(confidence=0.2, risk=0.9)
        bad = _make_trajectory(
            actions=[bad_action], confidence=0.2, risk=0.9,
        )

        sorted_trajectories = scorer.score_batch([bad, good])
        assert sorted_trajectories[0].score >= sorted_trajectories[1].score

    def test_score_empty_trajectory(self) -> None:
        scorer = RepairScorer()
        traj = _make_trajectory(actions=[])
        result = scorer.score(traj)
        # With no actions, effectiveness=0, risk=0, complexity=0
        assert isinstance(result, float)


# ===================================================================
# VerificationEngine tests
# ===================================================================


class TestVerificationEngine:
    """Tests for VerificationEngine."""

    def test_verify_returns_verification_result(self) -> None:
        engine = VerificationEngine()
        action = _make_action()
        traj = _make_trajectory(actions=[action])
        result = engine.verify(traj)

        assert result.trajectory_id == traj.trajectory_id
        assert result.passed_count >= 0
        assert result.failed_count >= 0

    def test_verify_valid_trajectory_passes(self) -> None:
        engine = VerificationEngine()
        action = _make_action(
            confidence=0.7, risk=0.3,
            parameters={"key": "status", "old_value": "broken", "new_value": "fixed"},
        )
        traj = _make_trajectory(actions=[action])
        result = engine.verify(traj)
        # Should not have hard failures
        assert result.is_valid()


# ===================================================================
# StaticVerifier tests
# ===================================================================


class TestStaticVerifier:
    """Tests for StaticVerifier."""

    def test_detects_empty_trajectory(self) -> None:
        verifier = StaticVerifier()
        traj = _make_trajectory(actions=[])
        checks = verifier.verify(traj)
        assert len(checks) == 1
        assert checks[0].status == VerificationStatus.FAILED
        assert "Empty" in checks[0].message


# ===================================================================
# SecurityVerifier tests
# ===================================================================


class TestSecurityVerifier:
    """Tests for SecurityVerifier."""

    def test_detects_dangerous_patterns(self) -> None:
        verifier = SecurityVerifier()
        action = _make_action(
            parameters={"config": "disable_tls"},
            description="disable TLS for perf",
        )
        traj = _make_trajectory(actions=[action])
        checks = verifier.verify(traj)
        failed = [c for c in checks if c.status == VerificationStatus.FAILED]
        assert len(failed) >= 1

    def test_safe_action_passes(self) -> None:
        verifier = SecurityVerifier()
        action = _make_action(
            parameters={"key": "timeout", "new_value": "30s"},
            description="increase timeout",
        )
        traj = _make_trajectory(actions=[action])
        checks = verifier.verify(traj)
        assert all(c.status == VerificationStatus.PASSED for c in checks)


# ===================================================================
# GraphLawVerifier tests
# ===================================================================


class TestGraphLawVerifier:
    """Tests for GraphLawVerifier."""

    def test_detects_new_violations(self) -> None:
        verifier = GraphLawVerifier()
        traj = _make_trajectory(actions=[_make_action()])

        current = {uuid4()}
        new_violation = uuid4()
        simulated = current | {new_violation}

        checks = verifier.verify(traj, current, simulated)
        failed = [c for c in checks if c.status == VerificationStatus.FAILED]
        assert len(failed) == 1
        assert "new violations" in failed[0].message.lower()

    def test_passes_when_violations_resolved(self) -> None:
        verifier = GraphLawVerifier()
        traj = _make_trajectory(actions=[_make_action()])

        current = {uuid4(), uuid4()}
        simulated = set()  # all violations gone

        checks = verifier.verify(traj, current, simulated)
        assert all(c.status == VerificationStatus.PASSED for c in checks)
