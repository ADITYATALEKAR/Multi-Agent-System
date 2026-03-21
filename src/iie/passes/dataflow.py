"""IIE Pass 4: DataflowPass — verify dataflow ordering, missing sinks/sources."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)

_VALID_ORDERINGS = {"unordered", "fifo", "causal_order", "total_order"}


class DataflowPass(BasePass):
    """Verifies dataflow integrity in the architecture.

    Checks performed:
    - Dataflow sources and sinks exist as components
    - Ordering values are valid
    - Components that produce data have at least one outbound dataflow
    - Components that consume data have at least one inbound dataflow
    - Self-referencing dataflows (source == target)
    """

    PASS_ID: int = 4
    PASS_NAME: str = "dataflow"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []
        component_ids = set(ir.components.keys())

        # Track which components have inbound/outbound dataflows
        sources: set[str] = set()
        sinks: set[str] = set()

        for flow in ir.dataflows:
            sources.add(flow.source)
            sinks.add(flow.target)

            # 1. Invalid ordering
            if flow.ordering not in _VALID_ORDERINGS:
                violations.append(
                    self._violation(
                        severity="warning",
                        message=f"Dataflow '{flow.flow_id}' has invalid ordering '{flow.ordering}'",
                        flow_id=flow.flow_id,
                        ordering=flow.ordering,
                    )
                )

            # 2. Self-referencing dataflow
            if flow.source == flow.target:
                violations.append(
                    self._violation(
                        severity="warning",
                        message=f"Dataflow '{flow.flow_id}' is self-referencing (source == target = '{flow.source}')",
                        component_id=flow.source,
                        flow_id=flow.flow_id,
                    )
                )

            # 3. Non-existent source
            if flow.source not in component_ids:
                violations.append(
                    self._violation(
                        severity="critical",
                        message=f"Dataflow '{flow.flow_id}' has non-existent source '{flow.source}'",
                        component_id=flow.source,
                        flow_id=flow.flow_id,
                    )
                )

            # 4. Non-existent sink
            if flow.target not in component_ids:
                violations.append(
                    self._violation(
                        severity="critical",
                        message=f"Dataflow '{flow.flow_id}' has non-existent target '{flow.target}'",
                        component_id=flow.target,
                        flow_id=flow.flow_id,
                    )
                )

        # 5. Queue/stream components should have at least one inbound and one outbound flow
        for cid, comp in ir.components.items():
            if comp.component_type in ("queue", "stream", "bus"):
                if cid not in sinks:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=f"Queue/stream component '{comp.name}' ({cid}) has no inbound dataflows",
                            component_id=cid,
                        )
                    )
                if cid not in sources:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=f"Queue/stream component '{comp.name}' ({cid}) has no outbound dataflows",
                            component_id=cid,
                        )
                    )

        log.info("dataflow_pass.complete", violations=len(violations))
        return violations
