"""Unit tests for state graph schema: NodeType, EdgeType, SchemaRegistry."""

from __future__ import annotations

import pytest

from src.state_graph.schema import (
    EdgeType,
    NodeType,
    SchemaRegistry,
)


class TestNodeType:
    def test_code_types_exist(self) -> None:
        assert NodeType.CLASS.value == "class"
        assert NodeType.FUNCTION.value == "function"
        assert NodeType.METHOD.value == "method"
        assert NodeType.MODULE.value == "module"
        assert NodeType.FILE.value == "file"

    def test_infra_types_exist(self) -> None:
        assert NodeType.SERVICE.value == "service"
        assert NodeType.CONTAINER.value == "container"
        assert NodeType.POD.value == "pod"

    def test_runtime_types_exist(self) -> None:
        assert NodeType.LOG_EVENT.value == "log_event"
        assert NodeType.SPAN.value == "span"
        assert NodeType.METRIC.value == "metric"

    def test_node_type_count(self) -> None:
        """Schema should have 100+ node types."""
        count = len(NodeType)
        assert count >= 100, f"Expected 100+ node types, got {count}"


class TestEdgeType:
    def test_structural_edges(self) -> None:
        assert EdgeType.CONTAINS.value == "contains"
        assert EdgeType.IMPORTS.value == "imports"
        assert EdgeType.INHERITS.value == "inherits"
        assert EdgeType.IMPLEMENTS.value == "implements"

    def test_dependency_edges(self) -> None:
        assert EdgeType.CALLS.value == "calls"
        assert EdgeType.DEPENDS_ON.value == "depends_on"

    def test_edge_type_count(self) -> None:
        """Schema should have 40+ edge types."""
        count = len(EdgeType)
        assert count >= 40, f"Expected 40+ edge types, got {count}"


class TestSchemaRegistry:
    def test_instantiation(self) -> None:
        r = SchemaRegistry()
        assert r is not None

    def test_validate_node_type(self) -> None:
        r = SchemaRegistry()
        assert r.validate_node_type("class") is True
        assert r.validate_node_type("nonexistent_type_xyz") is False

    def test_validate_edge_type(self) -> None:
        r = SchemaRegistry()
        assert r.validate_edge_type("contains") is True
        assert r.validate_edge_type("nonexistent_edge_xyz") is False
