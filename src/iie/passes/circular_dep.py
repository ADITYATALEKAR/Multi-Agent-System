"""IIE Pass 2: CircularDepPass — detect circular dependencies using DFS."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)


class CircularDepPass(BasePass):
    """Detects circular dependencies in the component dependency graph.

    Uses depth-first search to find all strongly-connected cycles
    in the combined dependency and connection graph.
    """

    PASS_ID: int = 2
    PASS_NAME: str = "circular_dep"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []

        cycles = ir.detect_cycles()

        for cycle in cycles:
            cycle_str = " -> ".join(cycle) + " -> " + cycle[0]
            violations.append(
                self._violation(
                    severity="critical",
                    message=f"Circular dependency detected: {cycle_str}",
                    component_id=cycle[0],
                    cycle=cycle,
                )
            )

        log.info("circular_dep_pass.complete", violations=len(violations), cycles=len(cycles))
        return violations
