"""IIE Pass 5: DeterminismPass — detect non-deterministic component interactions."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)

# Component types that are inherently non-deterministic
_NONDETERMINISTIC_TYPES = {"external_api", "random_source", "clock", "sensor"}

# Connection types that introduce non-determinism when combined
_RACE_PRONE_TYPES = {"publishes_to", "subscribes_to"}


class DeterminismPass(BasePass):
    """Detects non-deterministic component interactions.

    Checks performed:
    - Components with multiple unordered inbound connections (potential races)
    - Interactions with inherently non-deterministic component types
    - Dataflows with unordered delivery to deterministic consumers
    - Multiple writers to the same target without coordination
    """

    PASS_ID: int = 5
    PASS_NAME: str = "determinism"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []

        # 1. Detect components with inherently non-deterministic types
        for cid, comp in ir.components.items():
            if comp.component_type in _NONDETERMINISTIC_TYPES:
                # Check if there's no determinism annotation
                if not comp.properties.get("deterministic_override", False):
                    violations.append(
                        self._violation(
                            severity="info",
                            message=(
                                f"Component '{comp.name}' ({cid}) has non-deterministic type "
                                f"'{comp.component_type}'"
                            ),
                            component_id=cid,
                            component_type=comp.component_type,
                        )
                    )

        # 2. Multiple inbound connections to a single target (potential race)
        inbound_counts: dict[str, list[str]] = {}
        for conn in ir.connections:
            inbound_counts.setdefault(conn.target, []).append(conn.source)

        for target_id, source_ids in inbound_counts.items():
            if len(source_ids) > 1:
                target_comp = ir.get_component(target_id)
                if target_comp and not target_comp.properties.get("concurrent_safe", False):
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Component '{target_id}' receives from {len(source_ids)} sources "
                                f"without 'concurrent_safe' annotation — potential race condition"
                            ),
                            component_id=target_id,
                            sources=source_ids,
                        )
                    )

        # 3. Unordered dataflows to deterministic consumers
        for flow in ir.dataflows:
            if flow.ordering == "unordered":
                target_comp = ir.get_component(flow.target)
                if target_comp and target_comp.properties.get("requires_ordered_input", False):
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Dataflow '{flow.flow_id}' delivers unordered data to "
                                f"'{target_comp.name}' which requires ordered input"
                            ),
                            component_id=flow.target,
                            flow_id=flow.flow_id,
                        )
                    )

        log.info("determinism_pass.complete", violations=len(violations))
        return violations
