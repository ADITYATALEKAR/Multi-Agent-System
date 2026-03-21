"""Comprehensive unit tests for Phase 2 Self-Improving components.

Covers OutcomeTracker (record, query, success rate), LawWeightUpdater
(increase, decrease, clamp), and StrategyPriorUpdater.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.self_improving.outcome_tracker import OutcomeTracker, OutcomeRecord
from src.self_improving.law_weight_updater import LawWeightUpdater
from src.self_improving.strategy_prior_updater import StrategyPriorUpdater


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_outcome(
    target_id=None,
    record_type: str = "law_evaluation",
    outcome: str = "correct",
    details: dict | None = None,
) -> OutcomeRecord:
    """Create a minimal OutcomeRecord."""
    return OutcomeRecord(
        target_id=target_id or uuid4(),
        record_type=record_type,
        outcome=outcome,
        details=details or {},
    )


# ===========================================================================
# 1. test_outcome_tracker_record_and_query
# ===========================================================================


def test_outcome_tracker_record_and_query():
    """OutcomeTracker should store records and retrieve them by target_id."""
    tracker = OutcomeTracker()

    target = uuid4()
    rec1 = _make_outcome(target_id=target, outcome="correct")
    rec2 = _make_outcome(target_id=target, outcome="incorrect")
    rec_other = _make_outcome(target_id=uuid4(), outcome="correct")

    tracker.record(rec1)
    tracker.record(rec2)
    tracker.record(rec_other)

    # Query by target_id
    results = tracker.get_records(target)
    assert len(results) == 2, (
        f"Expected 2 records for target, got {len(results)}"
    )

    # Query with record_type filter
    results_typed = tracker.get_records(target, record_type="law_evaluation")
    assert len(results_typed) == 2, (
        "Both records have type 'law_evaluation'"
    )

    results_missing_type = tracker.get_records(target, record_type="nonexistent")
    assert len(results_missing_type) == 0, (
        "No records should match a nonexistent type"
    )


# ===========================================================================
# 2. test_outcome_tracker_success_rate
# ===========================================================================


def test_outcome_tracker_success_rate():
    """get_success_rate should return the fraction of 'correct' outcomes."""
    tracker = OutcomeTracker()

    target = uuid4()

    # No records -> 0.0
    assert tracker.get_success_rate(target) == 0.0, (
        "Success rate with no records should be 0.0"
    )

    # Add 3 correct, 1 incorrect
    for _ in range(3):
        tracker.record(_make_outcome(target_id=target, outcome="correct"))
    tracker.record(_make_outcome(target_id=target, outcome="incorrect"))

    rate = tracker.get_success_rate(target)
    assert rate == pytest.approx(0.75, abs=1e-9), (
        f"Success rate should be 0.75 (3/4), got {rate}"
    )

    # All incorrect
    target2 = uuid4()
    for _ in range(5):
        tracker.record(_make_outcome(target_id=target2, outcome="incorrect"))
    assert tracker.get_success_rate(target2) == 0.0, (
        "Success rate with all incorrect should be 0.0"
    )

    # All correct
    target3 = uuid4()
    for _ in range(4):
        tracker.record(_make_outcome(target_id=target3, outcome="correct"))
    assert tracker.get_success_rate(target3) == 1.0, (
        "Success rate with all correct should be 1.0"
    )


# ===========================================================================
# 3. test_law_weight_updater_increase
# ===========================================================================


def test_law_weight_updater_increase():
    """Correct outcomes should increase the law weight."""
    updater = LawWeightUpdater(learning_rate=0.05)

    current = 1.0
    new_weight = updater.update("LAW-001", was_correct=True, current_weight=current)

    assert new_weight == pytest.approx(1.05, abs=1e-9), (
        f"Weight should increase by learning_rate, got {new_weight}"
    )
    assert new_weight > current, (
        "Weight should be higher after a correct outcome"
    )


# ===========================================================================
# 4. test_law_weight_updater_decrease
# ===========================================================================


def test_law_weight_updater_decrease():
    """Incorrect outcomes should decrease the law weight."""
    updater = LawWeightUpdater(learning_rate=0.05)

    current = 1.0
    new_weight = updater.update("LAW-001", was_correct=False, current_weight=current)

    assert new_weight == pytest.approx(0.95, abs=1e-9), (
        f"Weight should decrease by learning_rate, got {new_weight}"
    )
    assert new_weight < current, (
        "Weight should be lower after an incorrect outcome"
    )


# ===========================================================================
# 5. test_law_weight_updater_clamp
# ===========================================================================


def test_law_weight_updater_clamp():
    """Weight updates should be clamped to [min_weight, max_weight]."""
    updater = LawWeightUpdater(
        learning_rate=0.5,
        min_weight=0.1,
        max_weight=2.0,
    )

    # Trying to decrease below min_weight
    new_weight = updater.update("LAW-MIN", was_correct=False, current_weight=0.1)
    assert new_weight == pytest.approx(0.1, abs=1e-9), (
        f"Weight should be clamped to min_weight=0.1, got {new_weight}"
    )

    # Trying to increase above max_weight
    new_weight = updater.update("LAW-MAX", was_correct=True, current_weight=2.0)
    assert new_weight == pytest.approx(2.0, abs=1e-9), (
        f"Weight should be clamped to max_weight=2.0, got {new_weight}"
    )

    # Batch update should also clamp
    results = updater.batch_update([
        ("LAW-BATCH-1", True, 1.9),
        ("LAW-BATCH-2", False, 0.3),
    ])
    assert results["LAW-BATCH-1"] <= 2.0, (
        "Batch update should clamp to max_weight"
    )
    assert results["LAW-BATCH-2"] >= 0.1, (
        "Batch update should clamp to min_weight"
    )


# ===========================================================================
# 6. test_strategy_prior_updater
# ===========================================================================


def test_strategy_prior_updater():
    """StrategyPriorUpdater should adjust priors toward 1.0 or 0.01."""
    updater = StrategyPriorUpdater(learning_rate=0.1)

    # Correct outcome should increase prior
    current_prior = 0.5
    new_prior = updater.update("law_local", was_correct=True, current_prior=current_prior)
    assert new_prior > current_prior, (
        f"Correct outcome should increase prior: {new_prior} > {current_prior}"
    )
    # Expected: 0.5 + 0.1 * (1.0 - 0.5) = 0.55
    assert new_prior == pytest.approx(0.55, abs=1e-9), (
        f"Expected 0.55, got {new_prior}"
    )

    # Incorrect outcome should decrease prior
    new_prior2 = updater.update("graph_backward", was_correct=False, current_prior=0.5)
    assert new_prior2 < 0.5, (
        f"Incorrect outcome should decrease prior: {new_prior2} < 0.5"
    )
    # Expected: 0.5 - 0.1 * 0.5 = 0.45
    assert new_prior2 == pytest.approx(0.45, abs=1e-9), (
        f"Expected 0.45, got {new_prior2}"
    )

    # Prior should be clamped to [0.01, 1.0]
    very_low = updater.update("edge_case", was_correct=False, current_prior=0.01)
    assert very_low >= 0.01, (
        f"Prior should be clamped at min 0.01, got {very_low}"
    )

    very_high = updater.update("edge_case_high", was_correct=True, current_prior=1.0)
    assert very_high <= 1.0, (
        f"Prior should be clamped at max 1.0, got {very_high}"
    )

    # get_priors should reflect updates
    priors = updater.get_priors()
    assert "law_local" in priors, "Updated strategy should appear in get_priors()"
    assert "graph_backward" in priors, (
        "Updated strategy should appear in get_priors()"
    )


# ===========================================================================
# 7. test_outcome_record_model (bonus)
# ===========================================================================


def test_outcome_record_model():
    """OutcomeRecord pydantic model should accept required fields correctly."""
    target = uuid4()
    rec = OutcomeRecord(
        target_id=target,
        record_type="hypothesis_resolution",
        outcome="partial",
        details={"reason": "insufficient evidence"},
    )

    assert rec.target_id == target, "target_id should be preserved"
    assert rec.record_type == "hypothesis_resolution", "record_type should be preserved"
    assert rec.outcome == "partial", "outcome should be preserved"
    assert rec.details == {"reason": "insufficient evidence"}, (
        "details should be preserved"
    )
    assert rec.record_id is not None, "record_id should be auto-generated"
    assert rec.timestamp is not None, "timestamp should be auto-generated"
    assert rec.tenant_id == "default", "tenant_id should default to 'default'"


# ===========================================================================
# 8. test_law_weight_updater_batch (bonus)
# ===========================================================================


def test_law_weight_updater_batch():
    """batch_update should apply updates to multiple laws at once."""
    updater = LawWeightUpdater(learning_rate=0.01)

    outcomes = [
        ("LAW-A", True, 1.0),
        ("LAW-B", False, 1.0),
        ("LAW-C", True, 0.5),
    ]

    results = updater.batch_update(outcomes)

    assert len(results) == 3, "batch_update should return results for all inputs"
    assert results["LAW-A"] == pytest.approx(1.01, abs=1e-9), (
        "LAW-A should increase"
    )
    assert results["LAW-B"] == pytest.approx(0.99, abs=1e-9), (
        "LAW-B should decrease"
    )
    assert results["LAW-C"] == pytest.approx(0.51, abs=1e-9), (
        "LAW-C should increase from 0.5"
    )
