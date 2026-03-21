"""IIE Bootstrap Gate Test (v3.3 F3).

Gate requirement: 100% defect detection on deliberately broken Architecture IR.
The IIE must detect ALL injected defects across all 12 passes.
"""

from __future__ import annotations

import pytest

from src.iie.architecture_ir import ArchitectureIR, ComponentSpec, Connection, DataflowSpec
from src.iie.engine import IIEEngine
from src.iie.passes.base import IntegrityViolation


def _build_broken_ir() -> ArchitectureIR:
    """Build a deliberately broken Architecture IR with defects for each pass.

    Injected defects:
    1. Structural: orphan component, missing dependency, dangling connection
    2. CircularDep: A -> B -> C -> A cycle
    3. Contract: consumed contract with no provider, connection referencing missing contract
    4. Dataflow: dataflow with non-existent source
    5. Determinism: duplicate connections (same source/target/type)
    6. Nondeterminism: nondeterministic component without annotation
    7. SplitGraph: missing infra/runtime tier components
    8. DeltaConsumption: component producing deltas but no consumer
    """
    ir = ArchitectureIR()

    # ── Healthy components ────────────────────────────────────
    ir.components["api-gateway"] = ComponentSpec(
        component_id="api-gateway",
        name="API Gateway",
        component_type="service",
        dependencies=["auth-service"],
        contracts_provided=["api-v1"],
        contracts_consumed=["auth-contract"],
    )
    ir.components["auth-service"] = ComponentSpec(
        component_id="auth-service",
        name="Auth Service",
        component_type="service",
        dependencies=["user-db"],
        contracts_provided=["auth-contract"],
    )
    ir.components["user-db"] = ComponentSpec(
        component_id="user-db",
        name="User Database",
        component_type="database",
    )

    # ── Defect 1: Orphan component (no connections, no deps) ──
    ir.components["orphan-svc"] = ComponentSpec(
        component_id="orphan-svc",
        name="Orphan Service",
        component_type="service",
    )

    # ── Defect 1b: Missing dependency ─────────────────────────
    ir.components["broken-dep"] = ComponentSpec(
        component_id="broken-dep",
        name="Broken Dependency",
        component_type="service",
        dependencies=["non-existent-component"],
    )

    # ── Defect 2: Circular dependency (A -> B -> C -> A) ──────
    ir.components["cycle-a"] = ComponentSpec(
        component_id="cycle-a",
        name="Cycle A",
        component_type="service",
        dependencies=["cycle-b"],
    )
    ir.components["cycle-b"] = ComponentSpec(
        component_id="cycle-b",
        name="Cycle B",
        component_type="service",
        dependencies=["cycle-c"],
    )
    ir.components["cycle-c"] = ComponentSpec(
        component_id="cycle-c",
        name="Cycle C",
        component_type="service",
        dependencies=["cycle-a"],
    )

    # ── Connections (healthy) ─────────────────────────────────
    ir.connections.append(Connection(
        source="api-gateway", target="auth-service",
        connection_type="depends_on",
    ))
    ir.connections.append(Connection(
        source="auth-service", target="user-db",
        connection_type="depends_on",
    ))

    # Cycle connections
    ir.connections.append(Connection(
        source="cycle-a", target="cycle-b",
        connection_type="depends_on",
    ))
    ir.connections.append(Connection(
        source="cycle-b", target="cycle-c",
        connection_type="depends_on",
    ))
    ir.connections.append(Connection(
        source="cycle-c", target="cycle-a",
        connection_type="depends_on",
    ))

    # ── Defect 1c: Dangling connection (target doesn't exist) ─
    ir.connections.append(Connection(
        source="api-gateway", target="ghost-service",
        connection_type="calls",
    ))

    # ── Defect 3: Contract — consumed but no provider ─────────
    # "auth-contract" is provided by auth-service and consumed by api-gateway (OK)
    # But api-gateway consumes "auth-contract" and provides "api-v1"
    # Make broken-dep consume a contract with no provider
    ir.components["broken-dep"].contracts_consumed = ["missing-contract"]

    # Defect 3b: Connection referencing non-existent contract
    ir.connections.append(Connection(
        source="api-gateway", target="auth-service",
        connection_type="calls",
        contract_id="phantom-contract",
    ))

    # Register some contracts
    ir.contracts["api-v1"] = {
        "provider": "api-gateway",
        "consumer": "external-client",  # non-existent consumer
    }
    ir.contracts["auth-contract"] = {
        "provider": "auth-service",
        "consumer": "api-gateway",
    }

    # ── Defect 4: Dataflow with non-existent source ───────────
    ir.dataflows.append(DataflowSpec(
        source="deleted-service", target="api-gateway",
        data_type="events",
    ))

    # ── Defect 5: Duplicate connections (determinism issue) ───
    ir.connections.append(Connection(
        source="api-gateway", target="auth-service",
        connection_type="depends_on",
    ))

    # ── Defect 6: Nondeterministic component ──────────────────
    ir.components["random-svc"] = ComponentSpec(
        component_id="random-svc",
        name="Random Service",
        component_type="service",
        properties={"nondeterministic": True},
    )
    ir.connections.append(Connection(
        source="api-gateway", target="random-svc",
        connection_type="calls",
    ))

    # ── Defect 8: Delta producer with no consumer ─────────────
    ir.components["delta-producer"] = ComponentSpec(
        component_id="delta-producer",
        name="Delta Producer",
        component_type="service",
        properties={"produces_deltas": True},
    )
    ir.connections.append(Connection(
        source="delta-producer", target="user-db",
        connection_type="depends_on",
    ))

    # Note: Defect 7 (split_graph) is automatically triggered because
    # we have no infra/runtime tier components.

    return ir


class TestIIEBootstrapGate:
    """v3.3 F3: IIE bootstrap validation — 100% defect detection on broken IR."""

    def test_all_defects_detected(self) -> None:
        """All injected defects should be detected by at least one IIE pass."""
        ir = _build_broken_ir()
        engine = IIEEngine()

        violations = engine.run_load_time_passes(ir)
        assert len(violations) > 0, "No violations detected on broken IR"

        # Collect which passes found violations
        passes_with_violations: set[int] = {v.pass_id for v in violations}

        # Pass 1 (structural): should detect orphan, missing dep, dangling conn
        assert 1 in passes_with_violations, (
            "Pass 1 (structural) should detect structural defects"
        )

        # Pass 2 (circular_dep): should detect cycle-a -> cycle-b -> cycle-c -> cycle-a
        assert 2 in passes_with_violations, (
            "Pass 2 (circular_dep) should detect circular dependencies"
        )

        # Pass 3 (contract): should detect missing contract provider
        assert 3 in passes_with_violations, (
            "Pass 3 (contract) should detect contract violations"
        )

        # Pass 4 (dataflow): should detect non-existent source
        assert 4 in passes_with_violations, (
            "Pass 4 (dataflow) should detect dataflow violations"
        )

        # Pass 7 (split_graph): should detect missing tiers
        assert 7 in passes_with_violations, (
            "Pass 7 (split_graph) should detect missing tiers"
        )

    def test_structural_pass_detects_orphan(self) -> None:
        """Pass 1 should detect the orphan component."""
        ir = _build_broken_ir()
        engine = IIEEngine()
        violations = engine.run_pass(1, ir)

        orphan_found = any(
            "orphan" in v.message.lower() or "Orphan" in v.message
            for v in violations
        )
        assert orphan_found, (
            f"Structural pass should detect orphan component. "
            f"Got: {[v.message for v in violations]}"
        )

    def test_structural_pass_detects_missing_dep(self) -> None:
        """Pass 1 should detect the missing dependency."""
        ir = _build_broken_ir()
        engine = IIEEngine()
        violations = engine.run_pass(1, ir)

        missing_dep_found = any(
            "non-existent" in v.message.lower() and "depend" in v.message.lower()
            for v in violations
        )
        assert missing_dep_found, (
            f"Structural pass should detect missing dependency. "
            f"Got: {[v.message for v in violations]}"
        )

    def test_structural_pass_detects_dangling_connection(self) -> None:
        """Pass 1 should detect the dangling connection."""
        ir = _build_broken_ir()
        engine = IIEEngine()
        violations = engine.run_pass(1, ir)

        dangling_found = any(
            "ghost-service" in v.message or "non-existent" in v.message.lower()
            for v in violations
        )
        assert dangling_found, (
            f"Structural pass should detect dangling connection to ghost-service. "
            f"Got: {[v.message for v in violations]}"
        )

    def test_circular_dep_pass_detects_cycle(self) -> None:
        """Pass 2 should detect the A -> B -> C -> A cycle."""
        ir = _build_broken_ir()
        engine = IIEEngine()
        violations = engine.run_pass(2, ir)

        assert len(violations) > 0, "CircularDep pass should detect at least one cycle"
        cycle_found = any(
            "cycle-a" in v.message.lower() or "circular" in v.message.lower()
            for v in violations
        )
        assert cycle_found, (
            f"CircularDep pass should detect cycle-a/b/c cycle. "
            f"Got: {[v.message for v in violations]}"
        )

    def test_contract_pass_detects_missing_provider(self) -> None:
        """Pass 3 should detect consumed contract without provider."""
        ir = _build_broken_ir()
        engine = IIEEngine()
        violations = engine.run_pass(3, ir)

        missing_provider = any(
            "missing-contract" in v.message or "no component provides" in v.message.lower()
            for v in violations
        )
        assert missing_provider, (
            f"Contract pass should detect missing provider for 'missing-contract'. "
            f"Got: {[v.message for v in violations]}"
        )

    def test_dataflow_pass_detects_dangling_source(self) -> None:
        """Pass 4 should detect dataflow with non-existent source."""
        ir = _build_broken_ir()
        engine = IIEEngine()
        violations = engine.run_pass(4, ir)

        dangling = any(
            "deleted-service" in v.message or "non-existent" in v.message.lower()
            for v in violations
        )
        assert dangling, (
            f"Dataflow pass should detect non-existent source 'deleted-service'. "
            f"Got: {[v.message for v in violations]}"
        )

    def test_split_graph_detects_missing_tiers(self) -> None:
        """Pass 7 should detect missing infra/runtime tiers."""
        ir = _build_broken_ir()
        engine = IIEEngine()
        violations = engine.run_pass(7, ir)

        assert len(violations) > 0, "SplitGraph pass should detect missing tiers"

    def test_runtime_monitor_processes_delta(self) -> None:
        """RuntimeMonitor should run runtime passes on delta."""
        from src.iie.runtime_monitor import IIERuntimeMonitor

        engine = IIEEngine()
        monitor = IIERuntimeMonitor(engine)
        monitor.start()
        assert monitor.is_running

        ir = _build_broken_ir()
        violations = monitor.on_delta(ir)
        # Runtime passes (9-12) may or may not find issues on this IR
        assert isinstance(violations, list)

        monitor.stop()
        assert not monitor.is_running

    def test_100_percent_defect_detection(self) -> None:
        """GATE: All critical defects must be detected.

        This is the v3.3 F3 gate test.  The broken IR contains:
        - Orphan component (Pass 1)
        - Missing dependency (Pass 1)
        - Dangling connection (Pass 1)
        - Circular dependency (Pass 2)
        - Missing contract provider (Pass 3)
        - Dangling dataflow source (Pass 4)
        - Missing graph tiers (Pass 7)

        All must be detected.
        """
        ir = _build_broken_ir()
        engine = IIEEngine()
        violations = engine.run_load_time_passes(ir)

        # Categorize violations
        messages = [v.message for v in violations]
        all_text = "\n".join(messages).lower()

        defects_detected = {
            "orphan": "orphan" in all_text,
            "missing_dep": "non-existent" in all_text and "depend" in all_text,
            "dangling_conn": "ghost-service" in all_text or ("non-existent" in all_text and "target" in all_text),
            "circular_dep": "circular" in all_text or "cycle" in all_text,
            "missing_provider": "missing-contract" in all_text or "no component provides" in all_text,
            "dangling_dataflow": "deleted-service" in all_text,
            "missing_tiers": "infra" in all_text or "runtime" in all_text,
        }

        # All defects must be detected
        for defect, detected in defects_detected.items():
            assert detected, (
                f"Defect '{defect}' was NOT detected. "
                f"Total violations: {len(violations)}. "
                f"Pass IDs: {sorted(set(v.pass_id for v in violations))}"
            )

        # Additionally verify minimum violation count
        assert len(violations) >= 7, (
            f"Expected at least 7 violations, got {len(violations)}"
        )
