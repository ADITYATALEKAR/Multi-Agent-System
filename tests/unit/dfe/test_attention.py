"""Unit tests for the Attention Layer (DFE Phase 2)."""

from __future__ import annotations

import time
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.core.fact import AddNode, GraphDelta, RemoveNode
from src.dfe.attention import (
    VIOLATION_BOOST,
    AttentionEntry,
    AttentionIndex,
    AttentionScorer,
    GraphAttentionLayer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_delta(operations, seq=0):
    return GraphDelta(
        sequence_number=seq,
        source="test",
        operations=operations,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAttentionEntry:
    def test_attention_entry_total_score(self) -> None:
        """total_score sums base, recency, connectivity, and custom boosts."""
        entry = AttentionEntry(
            entity_id=uuid4(),
            base_score=0.5,
            recency_score=0.3,
            connectivity_score=0.1,
            custom_boosts={"manual": 0.05},
        )
        # violation_boost should be 0 since violation_boost_expires defaults to 0
        expected = 0.5 + 0.3 + 0.1 + 0.05
        assert abs(entry.total_score - expected) < 1e-9

    def test_violation_boost_applied(self) -> None:
        """Violation boost is included in total_score when not expired."""
        entry = AttentionEntry(entity_id=uuid4(), base_score=0.2)
        entry.apply_violation_boost()

        assert entry.violation_boost == VIOLATION_BOOST
        assert entry.violation_boost_expires > time.monotonic()
        # total_score should include the boost
        assert entry.total_score >= 0.2 + VIOLATION_BOOST - 1e-9

    def test_violation_boost_expired(self) -> None:
        """Violation boost is excluded from total_score after expiry."""
        entry = AttentionEntry(entity_id=uuid4(), base_score=0.2)
        entry.violation_boost = VIOLATION_BOOST
        # Set expiry to the past
        entry.violation_boost_expires = time.monotonic() - 1.0

        # Boost should not be included
        assert abs(entry.total_score - 0.2) < 1e-9


class TestAttentionScorer:
    def test_attention_scorer_recency(self) -> None:
        """Recency decays exponentially with time."""
        scorer = AttentionScorer(recency_decay=0.95)
        now = time.monotonic()

        # Just happened => close to 1.0
        recent = scorer.compute_recency(now, now)
        assert abs(recent - 1.0) < 1e-9

        # 1 hour ago => 0.95
        one_hour_ago = scorer.compute_recency(now - 3600, now)
        assert abs(one_hour_ago - 0.95) < 1e-6

        # Older should have lower score
        two_hours_ago = scorer.compute_recency(now - 7200, now)
        assert two_hours_ago < one_hour_ago

    def test_attention_scorer_connectivity(self) -> None:
        """Connectivity score increases with edge count, capped at 1.0."""
        scorer = AttentionScorer(connectivity_weight=0.1)

        assert scorer.compute_connectivity(0) == 0.0
        assert abs(scorer.compute_connectivity(5) - 0.5) < 1e-9
        # Capped at 1.0
        assert scorer.compute_connectivity(100) == 1.0


class TestAttentionIndex:
    def test_attention_index_top_k(self) -> None:
        """top_k returns entities sorted by descending total_score."""
        index = AttentionIndex()

        ids = []
        for score in [0.1, 0.9, 0.5, 0.3]:
            uid = uuid4()
            ids.append(uid)
            entry = index.get_or_create(uid)
            entry.base_score = score

        top = index.top_k(k=2)

        assert len(top) == 2
        # Highest score first
        assert top[0][0] == pytest.approx(0.9)
        assert top[1][0] == pytest.approx(0.5)

    def test_attention_index_remove(self) -> None:
        """Removing an entity from the index reduces its length."""
        index = AttentionIndex()
        uid = uuid4()
        index.get_or_create(uid)
        assert len(index) == 1

        index.remove(uid)
        assert len(index) == 0
        assert index.get(uid) is None


class TestGraphAttentionLayer:
    def test_graph_attention_recompute_add_node(self) -> None:
        """recompute_affected updates scores when a node is added."""
        layer = GraphAttentionLayer()
        node_id = uuid4()
        delta = _make_delta([
            AddNode(node_id=node_id, node_type="service", attributes={"name": "svc"}),
        ])

        affected = layer.recompute_affected(delta)

        assert node_id in affected
        assert affected[node_id] > 0.0
        assert layer.entity_count == 1

    def test_graph_attention_recompute_remove_node(self) -> None:
        """recompute_affected removes a node's attention entry on RemoveNode."""
        layer = GraphAttentionLayer()
        node_id = uuid4()

        # First add the node
        add_delta = _make_delta([
            AddNode(node_id=node_id, node_type="class", attributes={}),
        ])
        layer.recompute_affected(add_delta)
        assert layer.entity_count == 1

        # Then remove it
        remove_delta = _make_delta([
            RemoveNode(node_id=node_id),
        ], seq=1)
        affected = layer.recompute_affected(remove_delta)

        assert node_id in affected
        assert affected[node_id] == 0.0
        assert layer.entity_count == 0
