"""IIE Pass 7: SplitGraphPass — verify 3-tier graph split (code/infra/runtime) integrity."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR, ComponentSpec
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)

# The three canonical graph tiers
_TIERS = {"code", "infra", "runtime"}

# Which component types belong to which tier
_TIER_MAP: dict[str, str] = {
    "service": "code",
    "library": "code",
    "module": "code",
    "function": "code",
    "database": "infra",
    "queue": "infra",
    "cache": "infra",
    "storage": "infra",
    "load_balancer": "infra",
    "network": "infra",
    "process": "runtime",
    "container": "runtime",
    "pod": "runtime",
    "instance": "runtime",
    "thread": "runtime",
}


class SplitGraphPass(BasePass):
    """Verifies the 3-tier graph split (code / infra / runtime).

    Checks performed:
    - Every component must belong to a recognized tier
    - No cross-tier direct dependencies without a bridge annotation
    - Each tier should have at least one component (if graph is non-empty)
    - Components with explicit tier annotation must match their component_type
    """

    PASS_ID: int = 7
    PASS_NAME: str = "split_graph"

    def _get_tier(self, comp: ComponentSpec) -> str | None:
        """Determine the tier for a component."""
        # Explicit annotation takes precedence
        explicit = comp.properties.get("tier")
        if explicit and explicit in _TIERS:
            return explicit
        # Fall back to type-based mapping
        return _TIER_MAP.get(comp.component_type)

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []

        if not ir.components:
            return violations

        # Build tier assignments
        tier_assignments: dict[str, str | None] = {}
        tiers_present: set[str] = set()

        for cid, comp in ir.components.items():
            tier = self._get_tier(comp)
            tier_assignments[cid] = tier

            if tier is None:
                violations.append(
                    self._violation(
                        severity="warning",
                        message=(
                            f"Component '{comp.name}' ({cid}) with type '{comp.component_type}' "
                            f"does not map to any graph tier (code/infra/runtime)"
                        ),
                        component_id=cid,
                    )
                )
            else:
                tiers_present.add(tier)

            # Check explicit tier annotation vs type-based tier
            explicit_tier = comp.properties.get("tier")
            type_tier = _TIER_MAP.get(comp.component_type)
            if explicit_tier and type_tier and explicit_tier != type_tier:
                violations.append(
                    self._violation(
                        severity="warning",
                        message=(
                            f"Component '{comp.name}' ({cid}) has explicit tier '{explicit_tier}' "
                            f"but component_type '{comp.component_type}' suggests tier '{type_tier}'"
                        ),
                        component_id=cid,
                        explicit_tier=explicit_tier,
                        inferred_tier=type_tier,
                    )
                )

        # Check cross-tier dependencies without bridge annotation
        for cid, comp in ir.components.items():
            source_tier = tier_assignments.get(cid)
            for dep_id in comp.dependencies:
                target_tier = tier_assignments.get(dep_id)
                if (
                    source_tier
                    and target_tier
                    and source_tier != target_tier
                    and not comp.properties.get("cross_tier_bridge", False)
                ):
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Cross-tier dependency: '{comp.name}' ({cid}, tier={source_tier}) "
                                f"depends on '{dep_id}' (tier={target_tier}) without bridge annotation"
                            ),
                            component_id=cid,
                            source_tier=source_tier,
                            target_tier=target_tier,
                            dependency=dep_id,
                        )
                    )

        # Check each tier has at least one component
        missing_tiers = _TIERS - tiers_present
        for tier in missing_tiers:
            violations.append(
                self._violation(
                    severity="info",
                    message=f"Graph tier '{tier}' has no components",
                    tier=tier,
                )
            )

        log.info("split_graph_pass.complete", violations=len(violations))
        return violations
