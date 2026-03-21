"""Unit tests for IndexMaintainer."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.fact import AddEdge, AddNode, GraphDelta, RemoveNode, UpdateAttribute
from src.state_graph.index_maintainer import IndexMaintainer
from src.state_graph.precomputed_indexes import PrecomputedIndexes
from src.state_graph.temporal_index import TemporalIndex


class TestIndexMaintainer:
    def setup_method(self) -> None:
        self.indexes = PrecomputedIndexes()
        self.temporal = TemporalIndex()
        self.maintainer = IndexMaintainer(self.indexes, self.temporal)

    @pytest.mark.asyncio
    async def test_process_add_node(self) -> None:
        nid = uuid4()
        delta = GraphDelta(
            sequence_number=1, source="test",
            operations=[AddNode(node_id=nid, node_type="class", attributes={"name": "Foo"})],
            scope={nid},
        )
        await self.maintainer.on_delta(delta)

        assert self.maintainer.deltas_processed == 1
        assert nid in self.indexes.query_by_type("class")

    @pytest.mark.asyncio
    async def test_process_add_edge(self) -> None:
        n1, n2 = uuid4(), uuid4()
        delta = GraphDelta(
            sequence_number=1, source="test",
            operations=[
                AddNode(node_id=n1, node_type="class", attributes={"name": "A"}),
                AddNode(node_id=n2, node_type="method", attributes={"name": "foo"}),
                AddEdge(src_id=n1, tgt_id=n2, edge_type="contains"),
            ],
            scope={n1, n2},
        )
        await self.maintainer.on_delta(delta)
        assert self.maintainer.deltas_processed == 1

    @pytest.mark.asyncio
    async def test_process_remove_node(self) -> None:
        nid = uuid4()
        d1 = GraphDelta(
            sequence_number=1, source="test",
            operations=[AddNode(node_id=nid, node_type="function", attributes={"name": "f"})],
            scope={nid},
        )
        await self.maintainer.on_delta(d1)

        d2 = GraphDelta(
            sequence_number=2, source="test",
            operations=[RemoveNode(node_id=nid)],
            scope={nid},
        )
        await self.maintainer.on_delta(d2)
        assert self.maintainer.deltas_processed == 2

    @pytest.mark.asyncio
    async def test_temporal_index_updated(self) -> None:
        nid = uuid4()
        delta = GraphDelta(
            sequence_number=1, source="test",
            operations=[AddNode(node_id=nid, node_type="class", attributes={"name": "A"})],
            scope={nid},
        )
        await self.maintainer.on_delta(delta)

        latest = self.temporal.latest_for_entity(nid)
        assert latest is not None
        assert latest.entity_id == nid

    @pytest.mark.asyncio
    async def test_update_attribute_temporal(self) -> None:
        nid = uuid4()
        delta = GraphDelta(
            sequence_number=1, source="test",
            operations=[UpdateAttribute(entity_id=nid, key="name", old_value="a", new_value="b")],
            scope={nid},
        )
        await self.maintainer.on_delta(delta)
        assert self.maintainer.deltas_processed == 1
