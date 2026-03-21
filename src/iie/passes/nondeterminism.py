"""IIE Pass 6: NondeterminismPass — flag intentional nondeterminism without annotations."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)

# Property keys that mark intentional nondeterminism
_NONDETERMINISM_ANNOTATIONS = {
    "nondeterministic",
    "intentionally_nondeterministic",
    "nondeterminism_reason",
}


class NondeterminismPass(BasePass):
    """Flags intentional nondeterminism that lacks proper annotation.

    Checks performed:
    - Components marked as nondeterministic must have a reason annotation
    - Components using random/probabilistic strategies need nondeterminism annotation
    - Pub/sub connections without ordering guarantee need acknowledgement
    """

    PASS_ID: int = 6
    PASS_NAME: str = "nondeterminism"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []

        for cid, comp in ir.components.items():
            is_marked_nondet = comp.properties.get("nondeterministic", False)
            has_reason = bool(comp.properties.get("nondeterminism_reason", ""))

            # 1. Marked as nondeterministic but missing reason
            if is_marked_nondet and not has_reason:
                violations.append(
                    self._violation(
                        severity="warning",
                        message=(
                            f"Component '{comp.name}' ({cid}) is marked nondeterministic "
                            f"but has no 'nondeterminism_reason' annotation"
                        ),
                        component_id=cid,
                    )
                )

            # 2. Uses probabilistic/random strategy but not annotated
            strategy = comp.properties.get("strategy", "")
            if strategy in ("random", "probabilistic", "sampling", "stochastic"):
                if not is_marked_nondet:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Component '{comp.name}' ({cid}) uses strategy '{strategy}' "
                                f"but is not annotated as nondeterministic"
                            ),
                            component_id=cid,
                            strategy=strategy,
                        )
                    )

        # 3. Pub/sub connections without ordering and no nondeterminism annotation
        for conn in ir.connections:
            if conn.connection_type in ("publishes_to", "subscribes_to"):
                has_annotation = conn.properties.get("nondeterministic", False)
                has_ordering = conn.properties.get("ordering") not in (None, "unordered")
                if not has_annotation and not has_ordering:
                    violations.append(
                        self._violation(
                            severity="info",
                            message=(
                                f"Connection '{conn.connection_id}' ({conn.connection_type}) "
                                f"has no ordering guarantee and no nondeterminism annotation"
                            ),
                            connection_id=conn.connection_id,
                            connection_type=conn.connection_type,
                        )
                    )

        log.info("nondeterminism_pass.complete", violations=len(violations))
        return violations
