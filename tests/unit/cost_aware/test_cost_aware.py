"""Comprehensive unit tests for Phase 2 Cost-Aware components.

Covers RunningStats (Welford's algorithm), OperationCostTracker
(record, budget, stats), and ValueEstimator (estimate_value, should_execute).
"""

from __future__ import annotations

import math
from uuid import uuid4

import pytest

from src.cost_aware.cost_tracker import RunningStats, OperationCostTracker
from src.cost_aware.value_estimator import ValueEstimator


# ===========================================================================
# 1. RunningStats — Welford's online algorithm
# ===========================================================================


def test_running_stats_empty():
    """Empty RunningStats should have zero count, mean, variance, stddev."""
    stats = RunningStats()
    assert stats.count == 0
    assert stats.mean == 0.0
    assert stats.variance == 0.0
    assert stats.stddev == 0.0


def test_running_stats_single_value():
    """Single observation: mean == value, variance == 0 (count < 2)."""
    stats = RunningStats()
    stats.update(42.0)

    assert stats.count == 1
    assert stats.mean == 42.0
    assert stats.variance == 0.0
    assert stats.stddev == 0.0


def test_running_stats_known_sequence():
    """Verify mean and population variance against a known sequence."""
    stats = RunningStats()
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    for v in values:
        stats.update(v)

    assert stats.count == 5
    assert stats.mean == pytest.approx(30.0, abs=1e-9)

    # Population variance of [10,20,30,40,50] = 200.0
    expected_variance = 200.0
    assert stats.variance == pytest.approx(expected_variance, abs=1e-9)
    assert stats.stddev == pytest.approx(math.sqrt(expected_variance), abs=1e-9)


def test_running_stats_identical_values():
    """All identical values should yield zero variance."""
    stats = RunningStats()
    for _ in range(100):
        stats.update(7.0)

    assert stats.count == 100
    assert stats.mean == pytest.approx(7.0, abs=1e-9)
    assert stats.variance == pytest.approx(0.0, abs=1e-9)
    assert stats.stddev == pytest.approx(0.0, abs=1e-9)


# ===========================================================================
# 2. OperationCostTracker — record, stats, budget
# ===========================================================================


def test_cost_tracker_record_and_total():
    """Recording costs should accumulate total_cost."""
    tracker = OperationCostTracker()
    tracker.record("op_a", cost=10.0, duration_ms=5.0)
    tracker.record("op_a", cost=20.0, duration_ms=8.0)
    tracker.record("op_b", cost=5.0, duration_ms=2.0)

    assert tracker.get_total_cost() == pytest.approx(35.0, abs=1e-9)


def test_cost_tracker_per_operation_stats():
    """get_stats should return correct RunningStats per operation type."""
    tracker = OperationCostTracker()
    tracker.record("dfe_evaluate", cost=10.0, duration_ms=5.0)
    tracker.record("dfe_evaluate", cost=20.0, duration_ms=8.0)

    stats = tracker.get_stats("dfe_evaluate")
    assert stats.count == 2
    assert stats.mean == pytest.approx(15.0, abs=1e-9)

    dur = tracker.get_duration_stats("dfe_evaluate")
    assert dur.count == 2
    assert dur.mean == pytest.approx(6.5, abs=1e-9)


def test_cost_tracker_unseen_operation():
    """Querying an unseen operation should return empty RunningStats."""
    tracker = OperationCostTracker()
    stats = tracker.get_stats("never_recorded")
    assert stats.count == 0
    assert stats.mean == 0.0

    dur = tracker.get_duration_stats("never_recorded")
    assert dur.count == 0


def test_cost_tracker_budget_remaining():
    """get_budget_remaining should return non-negative remaining budget."""
    tracker = OperationCostTracker()
    tracker.record("op", cost=30.0, duration_ms=1.0)

    assert tracker.get_budget_remaining(100.0) == pytest.approx(70.0, abs=1e-9)
    assert tracker.get_budget_remaining(20.0) == pytest.approx(0.0, abs=1e-9)


def test_cost_tracker_budget_exhausted():
    """is_budget_exhausted should return True when cost >= budget."""
    tracker = OperationCostTracker()

    assert not tracker.is_budget_exhausted(100.0)

    tracker.record("op", cost=100.0, duration_ms=10.0)
    assert tracker.is_budget_exhausted(100.0)
    assert tracker.is_budget_exhausted(50.0)
    assert not tracker.is_budget_exhausted(200.0)


# ===========================================================================
# 3. ValueEstimator — estimate_value, should_execute
# ===========================================================================


def test_value_estimator_no_history():
    """With no cost history, avg_cost=0 so cost_factor=1.0."""
    estimator = ValueEstimator()
    tracker = OperationCostTracker()
    scope = {uuid4() for _ in range(10)}

    value = estimator.estimate_value("new_op", scope, tracker)

    # cost_factor = 1/(1+0) = 1.0
    # scope_factor = log1p(10) / log1p(100)
    expected = 1.0 * (math.log1p(10) / math.log1p(100))
    assert value == pytest.approx(expected, abs=1e-9)


def test_value_estimator_with_history():
    """Historical cost should reduce the estimated value."""
    estimator = ValueEstimator()
    tracker = OperationCostTracker()
    tracker.record("expensive_op", cost=99.0, duration_ms=50.0)

    scope = {uuid4() for _ in range(50)}
    value = estimator.estimate_value("expensive_op", scope, tracker)

    # cost_factor = 1/(1+99) = 0.01
    # scope_factor = log1p(50) / log1p(100)
    expected = (1.0 / 100.0) * (math.log1p(50) / math.log1p(100))
    assert value == pytest.approx(expected, abs=1e-9)


def test_value_estimator_empty_scope():
    """Empty scope should yield value 0.0 (log1p(0)=0)."""
    estimator = ValueEstimator()
    tracker = OperationCostTracker()

    value = estimator.estimate_value("op", set(), tracker)
    assert value == pytest.approx(0.0, abs=1e-9)


def test_should_execute_above_threshold():
    """should_execute returns True when value >= threshold."""
    estimator = ValueEstimator()
    tracker = OperationCostTracker()
    scope = {uuid4() for _ in range(50)}

    # No cost history -> high value -> should execute
    assert estimator.should_execute("op", scope, tracker, threshold=0.1)


def test_should_execute_below_threshold():
    """should_execute returns False when value < threshold."""
    estimator = ValueEstimator()
    tracker = OperationCostTracker()

    # Record very expensive operations to drive avg_cost high
    for _ in range(10):
        tracker.record("costly_op", cost=10000.0, duration_ms=100.0)

    # Small scope + high cost -> low value
    scope = {uuid4()}
    assert not estimator.should_execute("costly_op", scope, tracker, threshold=0.5)
