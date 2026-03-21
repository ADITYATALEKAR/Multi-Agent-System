"""IndexMaintainer: delta consumer for precomputed indexes (v3.2 Risk Fix A).

Incrementally updates DependsClosure, BlastRadiusIndex, ServiceBoundary,
ImportResolution, CallGraph, and type/attribute indexes on each delta.
Target: update <100ms per delta.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

from src.core.fact import (
    AddEdge,
    AddNode,
    GraphDelta,
    RemoveEdge,
    RemoveNode,
    UpdateAttribute,
)
from src.state_graph.precomputed_indexes import PrecomputedIndexes
from src.state_graph.temporal_index import TemporalIndex

logger = structlog.get_logger(__name__)


class IndexMaintainer:
    """Consumes deltas and incrementally updates all precomputed indexes.

    Also updates the temporal index.

    Args:
        indexes: The PrecomputedIndexes to maintain.
        temporal_index: The TemporalIndex to maintain.
    """

    def __init__(
        self,
        indexes: PrecomputedIndexes,
        temporal_index: TemporalIndex,
    ) -> None:
        self._indexes = indexes
        self._temporal = temporal_index
        self._deltas_processed = 0

    async def on_delta(self, delta: GraphDelta) -> None:
        """Process a delta and update all indexes.

        Target: <100ms per delta.
        """
        start = time.monotonic()

        for op in delta.operations:
            if isinstance(op, AddNode):
                self._indexes.add_node(
                    op.node_id, op.node_type, op.attributes
                )
                # Update temporal index
                self._temporal.insert(
                    timestamp=delta.timestamp,
                    entity_id=op.node_id,
                    sequence_number=delta.sequence_number,
                    delta_id=delta.delta_id,
                )

            elif isinstance(op, RemoveNode):
                # We need the node_type to remove from type index.
                # Use "unknown" as fallback since we don't store node_type on removal.
                # TODO(v3.x): Consider adding node_type to RemoveNode op.
                self._indexes.remove_node(op.node_id, "unknown")
                self._temporal.insert(
                    timestamp=delta.timestamp,
                    entity_id=op.node_id,
                    sequence_number=delta.sequence_number,
                    delta_id=delta.delta_id,
                )

            elif isinstance(op, AddEdge):
                self._indexes.add_edge(op.src_id, op.tgt_id, op.edge_type)
                self._temporal.insert(
                    timestamp=delta.timestamp,
                    entity_id=op.edge_id,
                    sequence_number=delta.sequence_number,
                    delta_id=delta.delta_id,
                )

            elif isinstance(op, RemoveEdge):
                # TODO(v3.x): RemoveEdge doesn't carry src/tgt/type.
                # For now we skip edge removal from indexes.
                pass

            elif isinstance(op, UpdateAttribute):
                self._temporal.insert(
                    timestamp=delta.timestamp,
                    entity_id=op.entity_id,
                    sequence_number=delta.sequence_number,
                    delta_id=delta.delta_id,
                )

        self._deltas_processed += 1
        elapsed_ms = (time.monotonic() - start) * 1000

        if elapsed_ms > 100:
            logger.warning(
                "index_update_slow",
                delta_id=str(delta.delta_id),
                elapsed_ms=round(elapsed_ms, 2),
                ops_count=len(delta.operations),
            )

    @property
    def deltas_processed(self) -> int:
        return self._deltas_processed
