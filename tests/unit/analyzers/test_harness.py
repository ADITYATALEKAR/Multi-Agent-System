"""Unit tests for AnalyzerHarness and BaseAnalyzer."""

from __future__ import annotations

from uuid import UUID

import pytest

from src.analyzers.harness import AnalyzerHarness, BaseAnalyzer
from src.core.fact import AddEdge, AddNode, GraphDelta


class DummyAnalyzer(BaseAnalyzer):
    ANALYZER_ID = "dummy"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".dummy"]

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        from uuid import uuid4
        nid = uuid4()
        ops = [self._add_node("file", "test", file_path=file_path, node_id=nid)]
        return [self._make_delta(ops, file_path, {nid})]


class TestBaseAnalyzer:
    def test_add_node_creates_op(self) -> None:
        a = DummyAnalyzer()
        op = a._add_node("class", "Foo", file_path="test.py", language="python")
        assert isinstance(op, AddNode)
        assert op.node_type == "class"
        assert op.attributes["name"] == "Foo"
        assert op.attributes["analyzer"] == "dummy"

    def test_add_edge_creates_op(self) -> None:
        from uuid import uuid4
        a = DummyAnalyzer()
        src, tgt = uuid4(), uuid4()
        op = a._add_edge(src, tgt, "contains")
        assert isinstance(op, AddEdge)
        assert op.edge_type == "contains"
        assert op.attributes["source_analyzer"] == "dummy"
        assert op.attributes["confidence"] == 1.0

    def test_make_delta(self) -> None:
        from uuid import uuid4
        a = DummyAnalyzer()
        nid = uuid4()
        ops = [a._add_node("file", "x", node_id=nid)]
        delta = a._make_delta(ops, "test.py", {nid})
        assert isinstance(delta, GraphDelta)
        assert delta.source == "analyzer:dummy"
        assert len(delta.operations) == 1

    def test_analyze(self) -> None:
        a = DummyAnalyzer()
        results = a.analyze("content", "test.dummy")
        assert len(results) == 1
        assert isinstance(results[0], GraphDelta)


class TestAnalyzerHarness:
    def test_register_analyzer(self) -> None:
        h = AnalyzerHarness()
        h.register_analyzer(DummyAnalyzer())
        assert "dummy" in h.registered_analyzers

    def test_get_analyzer_for_file(self) -> None:
        h = AnalyzerHarness()
        h.register_analyzer(DummyAnalyzer())
        a = h.get_analyzer_for_file("test.dummy")
        assert a is not None
        assert a.ANALYZER_ID == "dummy"

    def test_no_analyzer_for_unknown(self) -> None:
        h = AnalyzerHarness()
        h.register_analyzer(DummyAnalyzer())
        a = h.get_analyzer_for_file("test.xyz")
        assert a is None

    def test_get_analyzer_by_id(self) -> None:
        h = AnalyzerHarness()
        h.register_analyzer(DummyAnalyzer())
        a = h.get_analyzer("dummy")
        assert a is not None
        assert a.ANALYZER_ID == "dummy"

    @pytest.mark.asyncio
    async def test_analyze_file(self) -> None:
        h = AnalyzerHarness()
        h.register_analyzer(DummyAnalyzer())
        results = await h.analyze_file("test.dummy", source="content")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_analyze_file_no_analyzer(self) -> None:
        h = AnalyzerHarness()
        results = await h.analyze_file("unknown.xyz", source="content")
        assert results == []
