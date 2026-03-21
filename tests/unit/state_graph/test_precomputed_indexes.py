"""Unit tests for PrecomputedIndexes."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.state_graph.precomputed_indexes import (
    BlastRadiusIndex,
    CallGraph,
    DependsClosure,
    ImportResolution,
    PrecomputedIndexes,
    ServiceBoundary,
)


class TestDependsClosure:
    def test_direct_dependency(self) -> None:
        dc = DependsClosure()
        a, b = uuid4(), uuid4()
        dc.add_edge(a, b)
        assert b in dc.get_dependencies(a)

    def test_transitive_dependency(self) -> None:
        dc = DependsClosure()
        a, b, c = uuid4(), uuid4(), uuid4()
        dc.add_edge(a, b)
        dc.add_edge(b, c)
        deps = dc.get_dependencies(a)
        assert b in deps
        assert c in deps

    def test_reverse_dependents(self) -> None:
        dc = DependsClosure()
        a, b = uuid4(), uuid4()
        dc.add_edge(a, b)
        assert a in dc.get_dependents(b)

    def test_no_self_in_dependencies(self) -> None:
        dc = DependsClosure()
        a, b = uuid4(), uuid4()
        dc.add_edge(a, b)
        assert a not in dc.get_dependencies(a)

    def test_remove_edge(self) -> None:
        dc = DependsClosure()
        a, b = uuid4(), uuid4()
        dc.add_edge(a, b)
        dc.remove_edge(a, b)
        assert b not in dc.get_dependencies(a)

    def test_cycle_handling(self) -> None:
        dc = DependsClosure()
        a, b, c = uuid4(), uuid4(), uuid4()
        dc.add_edge(a, b)
        dc.add_edge(b, c)
        dc.add_edge(c, a)
        # Should not infinite loop
        deps = dc.get_dependencies(a)
        assert b in deps
        assert c in deps


class TestBlastRadiusIndex:
    def test_blast_radius(self) -> None:
        dc = DependsClosure()
        bri = BlastRadiusIndex(dc)
        a, b, c = uuid4(), uuid4(), uuid4()
        dc.add_edge(b, a)  # b depends on a
        dc.add_edge(c, a)  # c depends on a
        result = bri.blast_radius(a)
        assert result["affected_nodes"] == 2

    def test_blast_radius_with_services(self) -> None:
        dc = DependsClosure()
        bri = BlastRadiusIndex(dc)
        a, b, c = uuid4(), uuid4(), uuid4()
        dc.add_edge(b, a)
        dc.add_edge(c, a)
        bri.set_service(b, "svc-A")
        bri.set_service(c, "svc-B")
        result = bri.blast_radius(a)
        assert result["cross_service"] is True
        assert len(result["affected_services"]) == 2

    def test_empty_blast_radius(self) -> None:
        dc = DependsClosure()
        bri = BlastRadiusIndex(dc)
        result = bri.blast_radius(uuid4())
        assert result["affected_nodes"] == 0


class TestCallGraph:
    def test_callers_and_callees(self) -> None:
        cg = CallGraph()
        caller, callee = uuid4(), uuid4()
        cg.add_call(caller, callee)
        assert callee in cg.get_callees(caller)
        assert caller in cg.get_callers(callee)

    def test_remove_call(self) -> None:
        cg = CallGraph()
        caller, callee = uuid4(), uuid4()
        cg.add_call(caller, callee)
        cg.remove_call(caller, callee)
        assert callee not in cg.get_callees(caller)


class TestImportResolution:
    def test_add_and_resolve(self) -> None:
        ir = ImportResolution()
        imp_id, target_id = uuid4(), uuid4()
        ir.add_import(imp_id, target_id)
        imports = ir.get_imports(imp_id)
        assert target_id in imports

    def test_register_name(self) -> None:
        ir = ImportResolution()
        nid = uuid4()
        ir.register_name("src.core.fact", nid)
        resolved = ir.resolve("src.core.fact")
        assert resolved == nid

    def test_resolve_unknown(self) -> None:
        ir = ImportResolution()
        assert ir.resolve("nonexistent") is None


class TestServiceBoundary:
    def test_assign_and_query(self) -> None:
        sb = ServiceBoundary()
        nid = uuid4()
        sb.assign(nid, "api-service")
        assert sb.get_service(nid) == "api-service"
        assert nid in sb.get_nodes("api-service")

    def test_reassign(self) -> None:
        sb = ServiceBoundary()
        nid = uuid4()
        sb.assign(nid, "svc-A")
        sb.assign(nid, "svc-B")
        assert sb.get_service(nid) == "svc-B"
        assert nid not in sb.get_nodes("svc-A")
        assert nid in sb.get_nodes("svc-B")

    def test_all_services(self) -> None:
        sb = ServiceBoundary()
        sb.assign(uuid4(), "alpha")
        sb.assign(uuid4(), "beta")
        assert set(sb.all_services()) == {"alpha", "beta"}


class TestPrecomputedIndexes:
    def test_add_node_and_query_type(self) -> None:
        pi = PrecomputedIndexes()
        nid = uuid4()
        pi.add_node(nid, "class", {"name": "MyClass"})
        results = pi.query_by_type("class")
        assert nid in results

    def test_remove_node(self) -> None:
        pi = PrecomputedIndexes()
        nid = uuid4()
        pi.add_node(nid, "function", {"name": "foo"})
        pi.remove_node(nid, "function")
        results = pi.query_by_type("function")
        assert nid not in results

    def test_add_edge_depends(self) -> None:
        pi = PrecomputedIndexes()
        src, tgt = uuid4(), uuid4()
        pi.add_edge(src, tgt, "depends_on")
        deps = pi.depends.get_dependencies(src)
        assert tgt in deps

    def test_add_edge_calls(self) -> None:
        pi = PrecomputedIndexes()
        caller, callee = uuid4(), uuid4()
        pi.add_edge(caller, callee, "calls")
        assert callee in pi.call_graph.get_callees(caller)

    def test_add_edge_imports(self) -> None:
        pi = PrecomputedIndexes()
        imp, target = uuid4(), uuid4()
        pi.add_edge(imp, target, "imports")
        assert target in pi.import_resolution.get_imports(imp)
        assert target in pi.depends.get_dependencies(imp)

    def test_query_by_attribute(self) -> None:
        pi = PrecomputedIndexes()
        nid = uuid4()
        pi.add_node(nid, "class", {"name": "Foo", "language": "python"})
        results = pi.query_by_attribute("language", "python")
        assert nid in results

    def test_rebuild(self) -> None:
        pi = PrecomputedIndexes()
        nid = uuid4()
        pi.add_node(nid, "class", {"name": "X"})
        assert len(pi.query_by_type("class")) == 1
        pi.rebuild()
        assert len(pi.query_by_type("class")) == 0

    def test_service_auto_assign(self) -> None:
        pi = PrecomputedIndexes()
        nid = uuid4()
        pi.add_node(nid, "service", {"name": "api", "service": "api-svc"})
        assert pi.service_boundary.get_service(nid) == "api-svc"
