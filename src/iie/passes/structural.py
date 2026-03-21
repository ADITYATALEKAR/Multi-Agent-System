"""IIE Pass 1: StructuralPass — orphan components, missing deps, dangling connections."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)


class StructuralPass(BasePass):
    """Detects structural problems in the architecture IR.

    Checks performed:
    - Orphan components with no connections at all
    - Dependencies referencing non-existent components
    - Connections referencing non-existent source/target components
    - Dataflows referencing non-existent source/target components
    """

    PASS_ID: int = 1
    PASS_NAME: str = "structural"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []

        component_ids = set(ir.components.keys())

        # 1. Orphan components: no connections and no dataflows involving them
        connected_ids: set[str] = set()
        for conn in ir.connections:
            connected_ids.add(conn.source)
            connected_ids.add(conn.target)
        for flow in ir.dataflows:
            connected_ids.add(flow.source)
            connected_ids.add(flow.target)

        for cid in component_ids:
            comp = ir.components[cid]
            if cid not in connected_ids and not comp.dependencies:
                violations.append(
                    self._violation(
                        severity="warning",
                        message=f"Orphan component '{comp.name}' ({cid}) has no connections, dataflows, or dependencies",
                        component_id=cid,
                    )
                )

        # 2. Missing dependencies: component references a dependency that doesn't exist
        for cid, comp in ir.components.items():
            for dep_id in comp.dependencies:
                if dep_id not in component_ids:
                    violations.append(
                        self._violation(
                            severity="critical",
                            message=f"Component '{comp.name}' ({cid}) depends on non-existent component '{dep_id}'",
                            component_id=cid,
                            missing_dependency=dep_id,
                        )
                    )

        # 3. Dangling connections: source or target doesn't exist
        for conn in ir.connections:
            if conn.source not in component_ids:
                violations.append(
                    self._violation(
                        severity="critical",
                        message=f"Connection '{conn.connection_id}' has non-existent source '{conn.source}'",
                        component_id=conn.source,
                        connection_id=conn.connection_id,
                    )
                )
            if conn.target not in component_ids:
                violations.append(
                    self._violation(
                        severity="critical",
                        message=f"Connection '{conn.connection_id}' has non-existent target '{conn.target}'",
                        component_id=conn.target,
                        connection_id=conn.connection_id,
                    )
                )

        # 4. Dangling dataflows: source or target doesn't exist
        for flow in ir.dataflows:
            if flow.source not in component_ids:
                violations.append(
                    self._violation(
                        severity="critical",
                        message=f"Dataflow '{flow.flow_id}' has non-existent source '{flow.source}'",
                        component_id=flow.source,
                        flow_id=flow.flow_id,
                    )
                )
            if flow.target not in component_ids:
                violations.append(
                    self._violation(
                        severity="critical",
                        message=f"Dataflow '{flow.flow_id}' has non-existent target '{flow.target}'",
                        component_id=flow.target,
                        flow_id=flow.flow_id,
                    )
                )

        log.info("structural_pass.complete", violations=len(violations))
        return violations
