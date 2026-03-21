"""Unit tests for core fact primitives."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import pytest

from src.core.fact import (
    CURRENT_SCHEMA_VERSION,
    AddEdge,
    AddNode,
    AddRuntimeEvent,
    AttachObservation,
    Fact,
    FactType,
    GraphDelta,
    GraphTier,
    RemoveEdge,
    RemoveNode,
    UpdateAttribute,
    validate_schema_version,
)


class TestFact:
    def test_create_fact(self) -> None:
        fid = uuid4()
        f = Fact(
            fact_type=FactType.NODE_FACT,
            subject_id=fid,
            predicate="has_type",
            object_value="class",
            source_analyzer="python",
        )
        assert f.fact_type == FactType.NODE_FACT
        assert f.subject_id == fid
        assert f.confidence == 1.0
        assert f.graph_tier == GraphTier.DELTA_LOG

    def test_fact_auto_id(self) -> None:
        f = Fact(
            fact_type=FactType.EDGE_FACT,
            subject_id=uuid4(),
            predicate="calls",
            object_value="foo",
            source_analyzer="test",
        )
        assert isinstance(f.fact_id, UUID)

    def test_confidence_bounds(self) -> None:
        with pytest.raises(Exception):
            Fact(
                fact_type=FactType.NODE_FACT,
                subject_id=uuid4(),
                predicate="p",
                object_value="v",
                source_analyzer="test",
                confidence=1.5,
            )


class TestDeltaOps:
    def test_add_node(self) -> None:
        nid = uuid4()
        op = AddNode(node_id=nid, node_type="class", attributes={"name": "Foo"})
        assert op.op == "add_node"
        assert op.node_id == nid
        assert op.attributes["name"] == "Foo"

    def test_add_node_auto_id(self) -> None:
        op = AddNode(node_type="function", attributes={})
        assert isinstance(op.node_id, UUID)

    def test_remove_node(self) -> None:
        nid = uuid4()
        op = RemoveNode(node_id=nid)
        assert op.op == "remove_node"
        assert op.node_id == nid

    def test_add_edge(self) -> None:
        src, tgt = uuid4(), uuid4()
        op = AddEdge(src_id=src, tgt_id=tgt, edge_type="contains")
        assert op.op == "add_edge"
        assert op.src_id == src
        assert op.tgt_id == tgt
        assert isinstance(op.edge_id, UUID)

    def test_remove_edge(self) -> None:
        eid = uuid4()
        op = RemoveEdge(edge_id=eid)
        assert op.op == "remove_edge"

    def test_update_attribute(self) -> None:
        eid = uuid4()
        op = UpdateAttribute(entity_id=eid, key="name", old_value="old", new_value="new")
        assert op.op == "update_attribute"
        assert op.key == "name"

    def test_attach_observation(self) -> None:
        eid = uuid4()
        op = AttachObservation(entity_id=eid, observation_data={"latency": 42})
        assert op.op == "attach_observation"
        assert op.observation_data["latency"] == 42

    def test_add_runtime_event(self) -> None:
        op = AddRuntimeEvent(event_type="request", participants=[uuid4()])
        assert op.op == "add_runtime_event"
        assert len(op.participants) == 1


class TestGraphDelta:
    def test_create_delta(self) -> None:
        nid = uuid4()
        d = GraphDelta(
            sequence_number=1,
            source="test",
            operations=[AddNode(node_id=nid, node_type="file", attributes={"name": "a.py"})],
            scope={nid},
        )
        assert d.schema_version == CURRENT_SCHEMA_VERSION
        assert d.sequence_number == 1
        assert len(d.operations) == 1
        assert d.tenant_id == "default"

    def test_delta_auto_fields(self) -> None:
        d = GraphDelta(sequence_number=0, source="test", operations=[])
        assert isinstance(d.delta_id, UUID)
        assert isinstance(d.timestamp, datetime)

    def test_validate_schema_version_ok(self) -> None:
        d = GraphDelta(sequence_number=0, source="test", operations=[])
        validate_schema_version(d)  # Should not raise

    def test_validate_schema_version_bad(self) -> None:
        d = GraphDelta(
            sequence_number=0, source="test", operations=[],
            schema_version=999,
        )
        with pytest.raises(ValueError, match="Unknown GraphDelta schema_version=999"):
            validate_schema_version(d)

    def test_negative_sequence_rejected(self) -> None:
        with pytest.raises(Exception):
            GraphDelta(sequence_number=-1, source="test", operations=[])

    def test_multiple_operations(self) -> None:
        n1, n2 = uuid4(), uuid4()
        d = GraphDelta(
            sequence_number=5,
            source="analyzer:python",
            operations=[
                AddNode(node_id=n1, node_type="class", attributes={"name": "A"}),
                AddNode(node_id=n2, node_type="function", attributes={"name": "foo"}),
                AddEdge(src_id=n1, tgt_id=n2, edge_type="contains"),
            ],
            scope={n1, n2},
        )
        assert len(d.operations) == 3
        assert len(d.scope) == 2

    def test_causal_predecessor(self) -> None:
        pred = uuid4()
        d = GraphDelta(
            sequence_number=2, source="test", operations=[],
            causal_predecessor=pred,
        )
        assert d.causal_predecessor == pred
