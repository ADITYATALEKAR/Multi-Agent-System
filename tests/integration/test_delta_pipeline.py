"""Integration test: delta pipeline end-to-end (no external services)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.fact import AddEdge, AddNode, GraphDelta, validate_schema_version
from src.state_graph.index_maintainer import IndexMaintainer
from src.state_graph.precomputed_indexes import PrecomputedIndexes
from src.state_graph.reasoning_graph import ReasoningGraph
from src.state_graph.temporal_index import TemporalIndex


class TestDeltaPipelineIntegration:
    """End-to-end test: create deltas, apply to ReasoningGraph, maintain indexes."""

    def setup_method(self) -> None:
        self.rg = ReasoningGraph()
        self.indexes = PrecomputedIndexes()
        self.temporal = TemporalIndex()
        self.maintainer = IndexMaintainer(self.indexes, self.temporal)

    def _make_delta(self, ops: list, scope: set) -> GraphDelta:
        return GraphDelta(
            sequence_number=0,
            source="test",
            operations=ops,
            scope=scope,
        )

    @pytest.mark.asyncio
    async def test_full_pipeline(self) -> None:
        # Create a small code graph
        file_id = uuid4()
        cls_id = uuid4()
        method_id = uuid4()

        delta = self._make_delta(
            [
                AddNode(node_id=file_id, node_type="file", attributes={"name": "app.py"}),
                AddNode(node_id=cls_id, node_type="class", attributes={"name": "App"}),
                AddNode(node_id=method_id, node_type="method", attributes={"name": "run"}),
                AddEdge(src_id=file_id, tgt_id=cls_id, edge_type="contains"),
                AddEdge(src_id=cls_id, tgt_id=method_id, edge_type="contains"),
            ],
            {file_id, cls_id, method_id},
        )

        # Validate schema
        validate_schema_version(delta)

        # Apply to reasoning graph
        self.rg.apply_delta(delta)

        # Maintain indexes
        await self.maintainer.on_delta(delta)

        # Verify reasoning graph
        assert self.rg.node_count() == 3
        assert self.rg.edge_count() == 2
        assert cls_id in self.rg.get_neighbors(file_id)
        assert method_id in self.rg.get_neighbors(cls_id)

        # Verify indexes
        assert file_id in self.indexes.query_by_type("file")
        assert cls_id in self.indexes.query_by_type("class")
        assert method_id in self.indexes.query_by_type("method")

        # Verify temporal
        assert self.temporal.latest_for_entity(file_id) is not None

    @pytest.mark.asyncio
    async def test_analyzer_to_graph(self) -> None:
        """Test analyzer output flowing through the pipeline."""
        from src.analyzers.tier1.python_analyzer import PythonAnalyzer

        analyzer = PythonAnalyzer()
        src = (
            "import os\n"
            "\n"
            "class Service:\n"
            "    def start(self):\n"
            "        pass\n"
            "\n"
            "def main():\n"
            "    svc = Service()\n"
            "    svc.start()\n"
        )
        deltas = analyzer.analyze(src, "service.py")
        assert len(deltas) == 1

        delta = deltas[0]
        validate_schema_version(delta)

        self.rg.apply_delta(delta)
        await self.maintainer.on_delta(delta)

        assert self.rg.node_count() > 0
        # Should have file, class, method, function, import nodes
        classes = self.rg.get_nodes_by_type("class")
        assert len(classes) >= 1
        methods = self.rg.get_nodes_by_type("method")
        assert len(methods) >= 1

    @pytest.mark.asyncio
    async def test_multi_file_analysis(self) -> None:
        """Test analyzing multiple files and building cumulative graph."""
        from src.analyzers.tier1.python_analyzer import PythonAnalyzer
        from src.analyzers.tier1.typescript_analyzer import TypeScriptAnalyzer

        py_analyzer = PythonAnalyzer()
        ts_analyzer = TypeScriptAnalyzer()

        py_src = "class UserService:\n    def get_user(self, uid):\n        pass\n"
        ts_src = "export class UserComponent {\n  render() {}\n}\n"

        py_deltas = py_analyzer.analyze(py_src, "user_service.py")
        ts_deltas = ts_analyzer.analyze(ts_src, "UserComponent.tsx")

        for d in py_deltas + ts_deltas:
            validate_schema_version(d)
            self.rg.apply_delta(d)
            await self.maintainer.on_delta(d)

        # Should have nodes from both files
        files = self.rg.get_nodes_by_type("file")
        assert len(files) == 2
        classes = self.rg.get_nodes_by_type("class")
        assert len(classes) >= 2

    @pytest.mark.asyncio
    async def test_fork_isolation(self) -> None:
        """Test that forked reasoning graph is independent."""
        nid = uuid4()
        delta = self._make_delta(
            [AddNode(node_id=nid, node_type="service", attributes={"name": "svc"})],
            {nid},
        )
        self.rg.apply_delta(delta)

        forked = self.rg.fork()

        # Add to original
        n2 = uuid4()
        d2 = self._make_delta(
            [AddNode(node_id=n2, node_type="service", attributes={"name": "svc2"})],
            {n2},
        )
        self.rg.apply_delta(d2)

        assert self.rg.node_count() == 2
        assert forked.node_count() == 1

    @pytest.mark.asyncio
    async def test_checkpoint_round_trip(self) -> None:
        """Test checkpoint and restore preserves graph state."""
        from src.analyzers.tier1.python_analyzer import PythonAnalyzer

        analyzer = PythonAnalyzer()
        src = "class A:\n    pass\n\nclass B(A):\n    pass\n"
        deltas = analyzer.analyze(src, "test.py")

        for d in deltas:
            self.rg.apply_delta(d)

        original_count = self.rg.node_count()
        data = self.rg.checkpoint()

        rg2 = ReasoningGraph()
        rg2.restore(data)
        assert rg2.node_count() == original_count
