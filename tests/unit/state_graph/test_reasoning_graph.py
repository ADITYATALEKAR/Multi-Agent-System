"""Unit tests for ReasoningGraph (pure Python fallback)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.fact import AddEdge, AddNode, GraphDelta, RemoveNode
from src.state_graph.reasoning_graph import ReasoningGraph


class TestReasoningGraph:
    def setup_method(self) -> None:
        self.rg = ReasoningGraph()

    def test_empty_graph(self) -> None:
        assert self.rg.node_count() == 0
        assert self.rg.edge_count() == 0

    def test_apply_add_node(self) -> None:
        nid = uuid4()
        delta = GraphDelta(
            sequence_number=1,
            source="test",
            operations=[
                AddNode(node_id=nid, node_type="class", attributes={"name": "Foo"}),
            ],
            scope={nid},
        )
        self.rg.apply_delta(delta)
        assert self.rg.node_count() == 1
        node = self.rg.get_node(nid)
        assert node is not None
        assert node["node_type"] == "class"
        assert node["attributes"]["name"] == "Foo"

    def test_apply_add_edge(self) -> None:
        n1, n2 = uuid4(), uuid4()
        delta = GraphDelta(
            sequence_number=1,
            source="test",
            operations=[
                AddNode(node_id=n1, node_type="class", attributes={"name": "A"}),
                AddNode(node_id=n2, node_type="method", attributes={"name": "foo"}),
                AddEdge(src_id=n1, tgt_id=n2, edge_type="contains"),
            ],
            scope={n1, n2},
        )
        self.rg.apply_delta(delta)
        assert self.rg.edge_count() == 1
        neighbors = self.rg.get_neighbors(n1)
        assert n2 in neighbors

    def test_apply_remove_node(self) -> None:
        nid = uuid4()
        d1 = GraphDelta(
            sequence_number=1, source="test",
            operations=[AddNode(node_id=nid, node_type="class", attributes={"name": "X"})],
            scope={nid},
        )
        self.rg.apply_delta(d1)
        assert self.rg.node_count() == 1

        d2 = GraphDelta(
            sequence_number=2, source="test",
            operations=[RemoveNode(node_id=nid)],
            scope={nid},
        )
        self.rg.apply_delta(d2)
        assert self.rg.node_count() == 0

    def test_get_nonexistent_node(self) -> None:
        assert self.rg.get_node(uuid4()) is None

    def test_get_nodes_by_type(self) -> None:
        ids = []
        ops = []
        for i in range(5):
            nid = uuid4()
            ids.append(nid)
            ops.append(AddNode(node_id=nid, node_type="function", attributes={"name": f"f{i}"}))
        # Add one of a different type
        other = uuid4()
        ops.append(AddNode(node_id=other, node_type="class", attributes={"name": "C"}))

        delta = GraphDelta(
            sequence_number=1, source="test",
            operations=ops, scope=set(ids) | {other},
        )
        self.rg.apply_delta(delta)

        funcs = self.rg.get_nodes_by_type("function")
        assert len(funcs) == 5

    def test_fork(self) -> None:
        nid = uuid4()
        delta = GraphDelta(
            sequence_number=1, source="test",
            operations=[AddNode(node_id=nid, node_type="class", attributes={"name": "A"})],
            scope={nid},
        )
        self.rg.apply_delta(delta)

        forked = self.rg.fork()
        assert forked.node_count() == 1

        # Modify original, fork should be unaffected
        n2 = uuid4()
        d2 = GraphDelta(
            sequence_number=2, source="test",
            operations=[AddNode(node_id=n2, node_type="class", attributes={"name": "B"})],
            scope={n2},
        )
        self.rg.apply_delta(d2)
        assert self.rg.node_count() == 2
        assert forked.node_count() == 1

    def test_checkpoint_restore(self) -> None:
        nid = uuid4()
        delta = GraphDelta(
            sequence_number=1, source="test",
            operations=[AddNode(node_id=nid, node_type="service", attributes={"name": "svc"})],
            scope={nid},
        )
        self.rg.apply_delta(delta)

        data = self.rg.checkpoint()
        assert isinstance(data, bytes)
        assert len(data) > 0

        rg2 = ReasoningGraph()
        rg2.restore(data)
        assert rg2.node_count() == 1
        node = rg2.get_node(nid)
        assert node is not None
        assert node["attributes"]["name"] == "svc"
