"""Graph Attention Layer (v3.1 + v3.2 upgrade + v3.3 C1).

Computes attention scores for graph nodes, directing DFE evaluation,
hypothesis budget, and RCA scope toward the most relevant areas.

v3.2: Attention score formula, gates DFE, hypothesis budget, RCA scope.
v3.3 C1: New-violation attention boost (+0.3 for 5 min).
"""

from __future__ import annotations

import heapq
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog

from src.core.fact import AddEdge, AddNode, GraphDelta, RemoveNode, UpdateAttribute

logger = structlog.get_logger(__name__)

# v3.3 C1: New-violation boost parameters
VIOLATION_BOOST = 0.3
VIOLATION_BOOST_DURATION_SEC = 300  # 5 minutes


@dataclass
class AttentionEntry:
    """Attention score for a single entity."""

    entity_id: UUID
    base_score: float = 0.0
    recency_score: float = 0.0
    connectivity_score: float = 0.0
    violation_boost: float = 0.0
    violation_boost_expires: float = 0.0  # monotonic timestamp
    custom_boosts: dict[str, float] = field(default_factory=dict)

    @property
    def total_score(self) -> float:
        """Compute total attention score."""
        boost = self.violation_boost if time.monotonic() < self.violation_boost_expires else 0.0
        custom = sum(self.custom_boosts.values())
        return self.base_score + self.recency_score + self.connectivity_score + boost + custom

    def apply_violation_boost(self) -> None:
        """Apply v3.3 C1 new-violation boost (+0.3 for 5 min)."""
        self.violation_boost = VIOLATION_BOOST
        self.violation_boost_expires = time.monotonic() + VIOLATION_BOOST_DURATION_SEC


class AttentionScorer:
    """Computes individual attention score components."""

    def __init__(
        self,
        recency_decay: float = 0.95,
        connectivity_weight: float = 0.1,
    ) -> None:
        self._recency_decay = recency_decay
        self._connectivity_weight = connectivity_weight

    def compute_recency(self, last_modified: float, now: float) -> float:
        """Recency score: exponential decay from last modification time."""
        age_seconds = max(0.0, now - last_modified)
        age_hours = age_seconds / 3600.0
        return self._recency_decay ** age_hours

    def compute_connectivity(self, edge_count: int) -> float:
        """Connectivity score: higher for well-connected nodes."""
        return min(1.0, edge_count * self._connectivity_weight)

    def compute_base(self, node_type: str, attributes: dict[str, Any]) -> float:
        """Base attention from node type and attributes."""
        # Higher base attention for more important entity types
        type_weights: dict[str, float] = {
            "service": 0.8,
            "class": 0.5,
            "function": 0.4,
            "method": 0.3,
            "file": 0.2,
            "import": 0.1,
            "variable": 0.1,
        }
        return type_weights.get(node_type, 0.2)


class AttentionIndex:
    """Index for fast attention score lookups and priority queue retrieval."""

    def __init__(self) -> None:
        self._entries: dict[UUID, AttentionEntry] = {}
        self._dirty = True  # Whether priority queue needs rebuild

    def get_or_create(self, entity_id: UUID) -> AttentionEntry:
        if entity_id not in self._entries:
            self._entries[entity_id] = AttentionEntry(entity_id=entity_id)
            self._dirty = True
        return self._entries[entity_id]

    def get(self, entity_id: UUID) -> AttentionEntry | None:
        return self._entries.get(entity_id)

    def remove(self, entity_id: UUID) -> None:
        self._entries.pop(entity_id, None)
        self._dirty = True

    def top_k(self, k: int = 100) -> list[tuple[float, UUID]]:
        """Get top-K entities by attention score."""
        scored = [(e.total_score, e.entity_id) for e in self._entries.values()]
        return heapq.nlargest(k, scored)

    def above_threshold(self, threshold: float) -> list[UUID]:
        """Get all entities with attention above threshold."""
        return [
            e.entity_id for e in self._entries.values()
            if e.total_score >= threshold
        ]

    def __len__(self) -> int:
        return len(self._entries)


class GraphAttentionLayer:
    """Graph Attention Layer — directs computation toward relevant graph regions.

    v3.2 upgrade: attention score formula, gates DFE, hypothesis budget, RCA scope.
    v3.3 C1: New-violation attention boost (+0.3 for 5 min).
    """

    def __init__(
        self,
        recency_decay: float = 0.95,
        connectivity_weight: float = 0.1,
        default_threshold: float = 0.1,
    ) -> None:
        self._scorer = AttentionScorer(recency_decay, connectivity_weight)
        self._index = AttentionIndex()
        self._threshold = default_threshold
        self._entity_edges: dict[UUID, int] = defaultdict(int)
        self._entity_modified: dict[UUID, float] = {}

    def compute_score(self, node_id: UUID) -> float:
        """Compute current attention score for a node."""
        entry = self._index.get(node_id)
        if entry is None:
            return 0.0
        return entry.total_score

    def recompute_affected(self, delta: GraphDelta) -> dict[UUID, float]:
        """Recompute attention scores for entities affected by a delta.

        Returns dict of {entity_id: new_score} for all affected entities.
        """
        now = time.monotonic()
        affected: dict[UUID, float] = {}

        for op in delta.operations:
            if isinstance(op, AddNode):
                entry = self._index.get_or_create(op.node_id)
                entry.base_score = self._scorer.compute_base(
                    op.node_type, op.attributes,
                )
                entry.recency_score = 1.0  # just added — max recency
                self._entity_modified[op.node_id] = now
                affected[op.node_id] = entry.total_score

            elif isinstance(op, AddEdge):
                # Update connectivity for both endpoints
                self._entity_edges[op.src_id] += 1
                self._entity_edges[op.tgt_id] += 1
                for eid in (op.src_id, op.tgt_id):
                    entry = self._index.get_or_create(eid)
                    entry.connectivity_score = self._scorer.compute_connectivity(
                        self._entity_edges[eid],
                    )
                    affected[eid] = entry.total_score

            elif isinstance(op, RemoveNode):
                self._index.remove(op.node_id)
                self._entity_edges.pop(op.node_id, None)
                self._entity_modified.pop(op.node_id, None)
                affected[op.node_id] = 0.0

            elif isinstance(op, UpdateAttribute):
                entry = self._index.get_or_create(op.entity_id)
                entry.recency_score = 1.0
                self._entity_modified[op.entity_id] = now
                affected[op.entity_id] = entry.total_score

        return affected

    def boost_for_violation(self, entity_id: UUID) -> None:
        """Apply v3.3 C1 new-violation attention boost to an entity."""
        entry = self._index.get_or_create(entity_id)
        entry.apply_violation_boost()
        logger.debug(
            "attention_violation_boost",
            entity_id=str(entity_id),
            new_score=entry.total_score,
        )

    def get_priority_queue(self, limit: int = 100) -> list[tuple[float, UUID]]:
        """Get entities ordered by attention score (highest first)."""
        return self._index.top_k(limit)

    def get_high_attention_entities(self, threshold: float | None = None) -> list[UUID]:
        """Get entities with attention above threshold."""
        t = threshold if threshold is not None else self._threshold
        return self._index.above_threshold(t)

    def decay_scores(self) -> None:
        """Apply time-based decay to recency scores."""
        now = time.monotonic()
        for entity_id, entry in list(self._index._entries.items()):
            last_mod = self._entity_modified.get(entity_id, 0.0)
            if last_mod > 0:
                entry.recency_score = self._scorer.compute_recency(last_mod, now)

    @property
    def entity_count(self) -> int:
        return len(self._index)
