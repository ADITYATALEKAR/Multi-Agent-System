"""Unit tests for the IIE (Integrity Inference Engine) subsystem (Phase 3).

Covers ArchitectureIR, IIE passes (structural, circular_dep, contract,
dataflow, split_graph), IIEEngine, and IIERuntimeMonitor.
"""

from __future__ import annotations

import pytest

from src.iie.architecture_ir import (
    ArchitectureIR,
    ComponentSpec,
    Connection,
    DataflowSpec,
)
from src.iie.passes.base import BasePass, IntegrityViolation
from src.iie.passes.structural import StructuralPass
from src.iie.passes.circular_dep import CircularDepPass
from src.iie.passes.contract import ContractPass
from src.iie.passes.dataflow import DataflowPass
from src.iie.passes.split_graph import SplitGraphPass
from src.iie.engine import IIEEngine
from src.iie.runtime_monitor import IIERuntimeMonitor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_component(
    cid: str,
    name: str,
    ctype: str = "service",
    deps: list[str] | None = None,
    provided: list[str] | None = None,
    consumed: list[str] | None = None,
    props: dict | None = None,
) -> ComponentSpec:
    return ComponentSpec(
        component_id=cid,
        name=name,
        component_type=ctype,
        dependencies=deps or [],
        contracts_provided=provided or [],
        contracts_consumed=consumed or [],
        properties=props or {},
    )


def _make_connection(source: str, target: str, ctype: str = "depends_on") -> Connection:
    return Connection(source=source, target=target, connection_type=ctype)


def _make_dataflow(source: str, target: str, data_type: str = "event") -> DataflowSpec:
    return DataflowSpec(source=source, target=target, data_type=data_type)


def _build_ir(
    components: list[ComponentSpec] | None = None,
    connections: list[Connection] | None = None,
    dataflows: list[DataflowSpec] | None = None,
    contracts: dict | None = None,
) -> ArchitectureIR:
    ir = ArchitectureIR()
    for comp in (components or []):
        ir.components[comp.component_id] = comp
    ir.connections = list(connections or [])
    ir.dataflows = list(dataflows or [])
    ir.contracts = dict(contracts or {})
    return ir


# ---------------------------------------------------------------------------
# ArchitectureIR tests
# ---------------------------------------------------------------------------

class TestArchitectureIR:
    """Tests for building and querying the ArchitectureIR."""

    def test_add_components_and_connections(self):
        """Build IR, verify get_component and get_connections_for."""
        svc_a = _make_component("a", "ServiceA")
        svc_b = _make_component("b", "ServiceB")
        conn = _make_connection("a", "b")

        ir = _build_ir(components=[svc_a, svc_b], connections=[conn])

        assert ir.get_component("a") is not None
        assert ir.get_component("a").name == "ServiceA"
        assert ir.get_component("nonexistent") is None

        conns = ir.get_connections_for("a")
        assert len(conns) == 1
        assert conns[0].target == "b"

        conns_b = ir.get_connections_for("b")
        assert len(conns_b) == 1  # same connection, b is target

    def test_get_dependencies_and_dependents(self):
        """get_dependencies and get_dependents return correct sets."""
        svc_a = _make_component("a", "ServiceA", deps=["b"])
        svc_b = _make_component("b", "ServiceB")
        conn = _make_connection("a", "b")

        ir = _build_ir(components=[svc_a, svc_b], connections=[conn])

        deps_a = ir.get_dependencies("a")
        assert "b" in deps_a

        dependents_b = ir.get_dependents("b")
        assert "a" in dependents_b

    def test_detect_cycles(self):
        """detect_cycles() finds a cycle when A -> B -> C -> A."""
        svc_a = _make_component("a", "A", deps=["b"])
        svc_b = _make_component("b", "B", deps=["c"])
        svc_c = _make_component("c", "C", deps=["a"])

        ir = _build_ir(components=[svc_a, svc_b, svc_c])

        cycles = ir.detect_cycles()
        assert len(cycles) >= 1
        # At least one cycle should contain all three nodes
        found = False
        for cycle in cycles:
            if set(cycle) == {"a", "b", "c"}:
                found = True
                break
        assert found, f"Expected cycle {{a, b, c}} in {cycles}"


# ---------------------------------------------------------------------------
# IIE Pass tests
# ---------------------------------------------------------------------------

class TestStructuralPass:
    """Pass 1: StructuralPass -- orphan components, dangling references."""

    def test_structural_pass_orphan(self):
        """An orphan component (no connections, no deps) triggers a warning."""
        orphan = _make_component("orphan", "OrphanService")
        ir = _build_ir(components=[orphan])

        violations = StructuralPass().run(ir)

        assert len(violations) >= 1
        assert any("orphan" in v.message.lower() or "OrphanService" in v.message for v in violations)


class TestCircularDepPass:
    """Pass 2: CircularDepPass -- cycle detection."""

    def test_circular_dep_pass(self):
        """A cycle A -> B -> A produces a critical violation."""
        svc_a = _make_component("a", "A", deps=["b"])
        svc_b = _make_component("b", "B", deps=["a"])
        ir = _build_ir(components=[svc_a, svc_b])

        violations = CircularDepPass().run(ir)

        assert len(violations) >= 1
        assert any(v.severity == "critical" for v in violations)
        assert any("circular" in v.message.lower() for v in violations)


class TestContractPass:
    """Pass 3: ContractPass -- missing provider for consumed contract."""

    def test_contract_pass_missing_provider(self):
        """Component consuming a contract with no provider triggers critical."""
        consumer = _make_component("c1", "Consumer", consumed=["contract-x"])
        # No provider for "contract-x"
        conn = _make_connection("c1", "c1", ctype="self")  # just to avoid orphan
        ir = _build_ir(components=[consumer], connections=[conn])

        violations = ContractPass().run(ir)

        assert len(violations) >= 1
        critical_violations = [v for v in violations if v.severity == "critical"]
        assert len(critical_violations) >= 1
        assert any("contract-x" in v.message for v in critical_violations)


class TestDataflowPass:
    """Pass 4: DataflowPass -- dangling source in dataflow."""

    def test_dataflow_pass_dangling(self):
        """Dataflow referencing a missing source produces a critical violation."""
        svc_b = _make_component("b", "ServiceB")
        flow = _make_dataflow("nonexistent_source", "b", "event")
        ir = _build_ir(components=[svc_b], dataflows=[flow])

        violations = DataflowPass().run(ir)

        assert len(violations) >= 1
        critical = [v for v in violations if v.severity == "critical"]
        assert len(critical) >= 1
        assert any("nonexistent_source" in v.message for v in critical)


class TestSplitGraphPass:
    """Pass 7: SplitGraphPass -- missing tiers."""

    def test_split_graph_pass_missing_tiers(self):
        """IR with only code-tier components flags missing infra and runtime tiers."""
        svc = _make_component("s1", "Service", ctype="service")
        conn = _make_connection("s1", "s1")  # self-ref just to avoid orphan
        ir = _build_ir(components=[svc], connections=[conn])

        violations = SplitGraphPass().run(ir)

        # Should report missing "infra" and "runtime" tiers
        messages = " ".join(v.message for v in violations)
        assert "infra" in messages.lower()
        assert "runtime" in messages.lower()


# ---------------------------------------------------------------------------
# IIEEngine tests
# ---------------------------------------------------------------------------

class TestIIEEngine:
    """Tests for IIEEngine orchestration."""

    def _clean_ir(self) -> ArchitectureIR:
        """Build a small clean IR that passes all checks."""
        comps = [
            _make_component("svc", "Svc", ctype="service"),
            _make_component("db", "DB", ctype="database"),
            _make_component("proc", "Proc", ctype="process"),
        ]
        conns = [
            _make_connection("svc", "db"),
            _make_connection("proc", "svc"),
        ]
        return _build_ir(components=comps, connections=conns)

    def test_engine_run_all_passes(self):
        """run_load_time_passes on a clean IR returns a list of violations (may be 0)."""
        engine = IIEEngine()
        ir = self._clean_ir()

        violations = engine.run_load_time_passes(ir)

        assert isinstance(violations, list)
        for v in violations:
            assert isinstance(v, IntegrityViolation)

    def test_engine_run_single_pass(self):
        """run_pass(1, ...) executes only the structural pass."""
        engine = IIEEngine()
        orphan = _make_component("orphan", "Orphan")
        ir = _build_ir(components=[orphan])

        violations = engine.run_pass(1, ir)

        assert isinstance(violations, list)
        assert len(violations) >= 1
        assert all(v.pass_id == 1 for v in violations)

    def test_engine_runtime_passes(self):
        """run_runtime_passes executes only passes with IDs 9-12."""
        engine = IIEEngine()
        ir = self._clean_ir()

        violations = engine.run_runtime_passes(ir)

        assert isinstance(violations, list)
        # Every violation must come from a runtime pass (9-12)
        for v in violations:
            assert v.pass_id in {9, 10, 11, 12}, f"Unexpected pass_id {v.pass_id}"


# ---------------------------------------------------------------------------
# RuntimeMonitor tests
# ---------------------------------------------------------------------------

class TestRuntimeMonitor:
    """Tests for IIERuntimeMonitor start/stop and delta processing."""

    def test_monitor_start_stop(self):
        """start() sets running, stop() clears it."""
        engine = IIEEngine()
        monitor = IIERuntimeMonitor(engine)

        assert not monitor.is_running

        monitor.start()
        assert monitor.is_running

        monitor.stop()
        assert not monitor.is_running

    def test_monitor_on_delta(self):
        """on_delta while running triggers runtime passes and returns violations."""
        engine = IIEEngine()
        monitor = IIERuntimeMonitor(engine)

        comps = [
            _make_component("svc", "Svc", ctype="service"),
            _make_component("db", "DB", ctype="database"),
            _make_component("proc", "Proc", ctype="process"),
        ]
        conns = [_make_connection("svc", "db"), _make_connection("proc", "svc")]
        ir = _build_ir(components=comps, connections=conns)

        # Not running -- should return empty
        result_stopped = monitor.on_delta(ir)
        assert result_stopped == []

        # Running -- should return violations list (possibly empty)
        monitor.start()
        result_running = monitor.on_delta(ir)
        assert isinstance(result_running, list)
        for v in result_running:
            assert isinstance(v, IntegrityViolation)
            assert v.pass_id in {9, 10, 11, 12}
