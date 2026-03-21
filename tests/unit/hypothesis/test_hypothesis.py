"""Comprehensive unit tests for Phase 2 Hypothesis Engine components.

Covers Hypothesis model, all six strategies (LawLocal, GraphBackward,
CrossService, Temporal, MemoryAssisted, LLMAssisted), and the
HypothesisAggregator deduplication/ranking logic.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from src.hypothesis.hypothesis import Hypothesis, HypothesisStatus
from src.hypothesis.strategies import (
    LawLocalStrategy,
    GraphBackwardStrategy,
    CrossServiceStrategy,
    TemporalStrategy,
    MemoryAssistedStrategy,
    LLMAssistedStrategy,
)
from src.hypothesis.aggregator import HypothesisAggregator
from src.core.derived import (
    DerivedFact,
    DerivedType,
    DerivedStatus,
    ExtendedJustification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_violation(
    rule_id: str = "test-rule",
    service: str | None = None,
    confidence: float = 0.8,
    entity_id: str | None = None,
    timestamp: datetime | None = None,
) -> DerivedFact:
    """Create a minimal DerivedFact representing a violation."""
    payload: dict = {"rule_id": rule_id}
    if service is not None:
        payload["service"] = service
    if entity_id is not None:
        payload["entity_id"] = entity_id

    ts = timestamp or datetime.utcnow()

    return DerivedFact(
        derived_type=DerivedType.VIOLATION,
        payload=payload,
        justification=ExtendedJustification(rule_id=rule_id),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
        timestamp=ts,
    )


# ===========================================================================
# 1. test_hypothesis_model
# ===========================================================================


def test_hypothesis_model():
    """Hypothesis model should accept all required fields and expose defaults."""
    h = Hypothesis(
        description="Test hypothesis",
        confidence=0.75,
        strategy_id="test_strategy",
    )

    assert h.description == "Test hypothesis", "description should be preserved"
    assert h.confidence == 0.75, "confidence should be preserved"
    assert h.strategy_id == "test_strategy", "strategy_id should be preserved"
    assert h.status == HypothesisStatus.PROPOSED, (
        "default status should be PROPOSED"
    )
    assert h.hypothesis_id is not None, "hypothesis_id should be auto-generated"
    assert isinstance(h.supporting_evidence, list), (
        "supporting_evidence should default to an empty list"
    )

    # Verify enum values
    expected_statuses = {"proposed", "supported", "refuted", "merged"}
    actual_statuses = {s.value for s in HypothesisStatus}
    assert actual_statuses == expected_statuses, (
        f"HypothesisStatus mismatch: {actual_statuses}"
    )


# ===========================================================================
# 2. test_law_local_strategy_groups_by_rule
# ===========================================================================


def test_law_local_strategy_groups_by_rule():
    """LawLocalStrategy should group violations by rule_id into hypotheses."""
    strategy = LawLocalStrategy()

    violations = [
        _make_violation(rule_id="rule-A", entity_id="e1"),
        _make_violation(rule_id="rule-A", entity_id="e2"),
        _make_violation(rule_id="rule-B", entity_id="e3"),
    ]

    results = strategy.generate(violations, {})

    assert len(results) == 2, (
        f"Expected 2 hypotheses (one per rule), got {len(results)}"
    )

    descriptions = [h.description for h in results]

    # One hypothesis should mention rule-A
    rule_a_matches = [d for d in descriptions if "rule-A" in d]
    assert len(rule_a_matches) == 1, (
        "Expected exactly one hypothesis for rule-A"
    )

    # One hypothesis should mention rule-B
    rule_b_matches = [d for d in descriptions if "rule-B" in d]
    assert len(rule_b_matches) == 1, (
        "Expected exactly one hypothesis for rule-B"
    )

    # Results should be sorted by confidence (descending)
    confidences = [h.confidence for h in results]
    assert confidences == sorted(confidences, reverse=True), (
        "Hypotheses should be sorted by confidence descending"
    )

    # Empty violations should return empty
    assert strategy.generate([], {}) == [], (
        "Empty violations should return empty list"
    )


# ===========================================================================
# 3. test_graph_backward_strategy_with_edges
# ===========================================================================


def test_graph_backward_strategy_with_edges():
    """GraphBackwardStrategy should trace edges backward from violations."""
    strategy = GraphBackwardStrategy()

    entity_id = str(uuid4())
    upstream_id = str(uuid4())

    violations = [
        _make_violation(rule_id="dep-rule", entity_id=entity_id),
    ]

    graph_context = {
        "edges": [
            {
                "src_id": upstream_id,
                "tgt_id": entity_id,
                "edge_type": "depends_on",
            },
        ],
    }

    results = strategy.generate(violations, graph_context)

    assert len(results) == 1, (
        f"Expected 1 backward hypothesis, got {len(results)}"
    )
    assert upstream_id in results[0].description, (
        "Hypothesis should mention the upstream entity"
    )
    assert "depends_on" in results[0].description, (
        "Hypothesis should mention the edge type"
    )
    assert results[0].strategy_id == "graph_backward", (
        "strategy_id should be 'graph_backward'"
    )

    # No edges -> empty result
    assert strategy.generate(violations, {"edges": []}) == [], (
        "No edges should return empty"
    )
    assert strategy.generate(violations, {}) == [], (
        "Missing edges key should return empty"
    )


# ===========================================================================
# 4. test_cross_service_strategy_correlates
# ===========================================================================


def test_cross_service_strategy_correlates():
    """CrossServiceStrategy should correlate violations across 2+ services."""
    strategy = CrossServiceStrategy()

    violations = [
        _make_violation(rule_id="r1", service="svc-a", confidence=0.9),
        _make_violation(rule_id="r2", service="svc-b", confidence=0.8),
    ]

    results = strategy.generate(violations, {})

    assert len(results) >= 1, (
        "Expected at least 1 cross-service hypothesis"
    )
    assert "svc-a" in results[0].description, (
        "Hypothesis should mention svc-a"
    )
    assert "svc-b" in results[0].description, (
        "Hypothesis should mention svc-b"
    )
    assert results[0].strategy_id == "cross_service", (
        "strategy_id should be 'cross_service'"
    )

    # Single service should return empty
    single_svc = [_make_violation(rule_id="r1", service="svc-only")]
    assert strategy.generate(single_svc, {}) == [], (
        "Single service should return empty"
    )

    # No service info should return empty
    no_svc = [_make_violation(rule_id="r1")]
    assert strategy.generate(no_svc, {}) == [], (
        "No service info should return empty"
    )


# ===========================================================================
# 5. test_temporal_strategy_clusters
# ===========================================================================


def test_temporal_strategy_clusters():
    """TemporalStrategy should cluster violations within the time window."""
    window = timedelta(seconds=30)
    strategy = TemporalStrategy(temporal_window=window)

    base_time = datetime(2025, 1, 1, 12, 0, 0)

    violations = [
        _make_violation(
            rule_id="t1", entity_id="e1",
            timestamp=base_time,
        ),
        _make_violation(
            rule_id="t2", entity_id="e2",
            timestamp=base_time + timedelta(seconds=10),
        ),
        _make_violation(
            rule_id="t3", entity_id="e3",
            timestamp=base_time + timedelta(seconds=20),
        ),
        # This one is outside the window from the cluster
        _make_violation(
            rule_id="t4", entity_id="e4",
            timestamp=base_time + timedelta(minutes=5),
        ),
    ]

    results = strategy.generate(violations, {})

    assert len(results) >= 1, "Expected at least 1 temporal cluster hypothesis"
    assert results[0].strategy_id == "temporal", (
        "strategy_id should be 'temporal'"
    )

    # With fewer than 2 violations, should return empty
    single = [_make_violation(rule_id="solo")]
    assert strategy.generate(single, {}) == [], (
        "Single violation should return empty"
    )


# ===========================================================================
# 6. test_memory_assisted_returns_empty
# ===========================================================================


def test_memory_assisted_returns_empty():
    """MemoryAssistedStrategy (Phase 5 stub) should return an empty list."""
    strategy = MemoryAssistedStrategy()

    violations = [
        _make_violation(rule_id="r1"),
        _make_violation(rule_id="r2"),
    ]

    results = strategy.generate(violations, {})
    assert results == [], (
        "MemoryAssistedStrategy stub should return empty list"
    )
    assert strategy.STRATEGY_ID == "memory_assisted", (
        "STRATEGY_ID should be 'memory_assisted'"
    )


# ===========================================================================
# 7. test_llm_assisted_returns_empty
# ===========================================================================


def test_llm_assisted_returns_empty():
    """LLMAssistedStrategy (Phase 5 stub) should return an empty list."""
    strategy = LLMAssistedStrategy()

    violations = [
        _make_violation(rule_id="r1"),
    ]

    results = strategy.generate(violations, {})
    assert results == [], (
        "LLMAssistedStrategy stub should return empty list"
    )
    assert strategy.STRATEGY_ID == "llm_assisted", (
        "STRATEGY_ID should be 'llm_assisted'"
    )


# ===========================================================================
# 8. test_aggregator_deduplicates_and_ranks
# ===========================================================================


def test_aggregator_deduplicates_and_ranks():
    """HypothesisAggregator should run strategies, deduplicate, and rank."""
    law_local = LawLocalStrategy()
    cross_svc = CrossServiceStrategy()
    memory_stub = MemoryAssistedStrategy()

    aggregator = HypothesisAggregator(
        strategies=[law_local, cross_svc, memory_stub],
        top_k=5,
    )

    violations = [
        _make_violation(rule_id="shared-rule", service="svc-a", entity_id="e1"),
        _make_violation(rule_id="shared-rule", service="svc-b", entity_id="e2"),
    ]

    results = aggregator.aggregate(violations, graph_context={})

    assert len(results) > 0, "Aggregator should produce at least one hypothesis"
    assert len(results) <= 5, (
        f"Aggregator should respect top_k=5 limit, got {len(results)}"
    )

    # Results should be sorted by confidence descending
    confidences = [h.confidence for h in results]
    assert confidences == sorted(confidences, reverse=True), (
        "Hypotheses should be sorted by confidence descending"
    )

    # Empty violations should return empty
    empty_results = aggregator.aggregate([], graph_context={})
    assert empty_results == [], (
        "Empty violations should return empty results"
    )


# ===========================================================================
# 9. test_aggregator_merges_similar (bonus)
# ===========================================================================


def test_aggregator_merges_similar():
    """The aggregator should merge hypotheses with similar descriptions."""
    law_local = LawLocalStrategy()

    aggregator = HypothesisAggregator(
        strategies=[law_local],
        top_k=10,
        similarity_threshold=0.75,
    )

    # Create violations that will produce a single group with the same rule_id
    violations = [
        _make_violation(rule_id="merge-rule", entity_id="e1"),
        _make_violation(rule_id="merge-rule", entity_id="e2"),
        _make_violation(rule_id="merge-rule", entity_id="e3"),
    ]

    results = aggregator.aggregate(violations, graph_context={})

    # Since all violations share the same rule_id, LawLocalStrategy
    # should produce exactly one hypothesis
    assert len(results) == 1, (
        f"All same-rule violations should produce 1 hypothesis, got {len(results)}"
    )
    assert results[0].confidence > 0, "Merged hypothesis should have positive confidence"
