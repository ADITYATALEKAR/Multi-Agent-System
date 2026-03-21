"""IIE Pass 11: SolverBudgetPass — verify solver budget allocation is sane."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)

# Budget limits
_DEFAULT_MAX_SOLVER_BUDGET_MS = 30_000  # 30 seconds total
_DEFAULT_MAX_SINGLE_QUERY_MS = 10_000  # 10 seconds per query
_DEFAULT_MIN_BUDGET_MS = 100  # At least 100ms
_MAX_CONCURRENT_SOLVERS = 8


class SolverBudgetPass(BasePass):
    """Verifies that solver budget allocations are sane.

    Checks performed:
    - Total solver budget does not exceed the global maximum
    - Individual solver queries respect per-query timeout limits
    - Budget allocations are non-negative and non-zero
    - Number of concurrent solver invocations is within bounds
    - Components using solvers have budget annotations
    """

    PASS_ID: int = 11
    PASS_NAME: str = "solver_budget"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []

        max_total_ms = ir.properties.get("max_solver_budget_ms", _DEFAULT_MAX_SOLVER_BUDGET_MS)
        max_query_ms = ir.properties.get("max_single_query_ms", _DEFAULT_MAX_SINGLE_QUERY_MS)

        total_budget_ms = 0.0
        solver_components: list[str] = []

        for cid, comp in ir.components.items():
            solver_budget = comp.properties.get("solver_budget_ms")
            uses_solver = comp.properties.get("uses_solver", False)

            # Component uses solver but has no budget
            if uses_solver and solver_budget is None:
                violations.append(
                    self._violation(
                        severity="warning",
                        message=(
                            f"Component '{comp.name}' ({cid}) uses a solver but has no "
                            f"'solver_budget_ms' annotation"
                        ),
                        component_id=cid,
                    )
                )
                continue

            if solver_budget is not None:
                solver_components.append(cid)

                # Non-positive budget
                if solver_budget <= 0:
                    violations.append(
                        self._violation(
                            severity="critical",
                            message=(
                                f"Component '{comp.name}' ({cid}) has non-positive solver budget: "
                                f"{solver_budget}ms"
                            ),
                            component_id=cid,
                            solver_budget_ms=solver_budget,
                        )
                    )
                    continue

                # Below minimum threshold
                if solver_budget < _DEFAULT_MIN_BUDGET_MS:
                    violations.append(
                        self._violation(
                            severity="info",
                            message=(
                                f"Component '{comp.name}' ({cid}) has very low solver budget: "
                                f"{solver_budget}ms (min recommended: {_DEFAULT_MIN_BUDGET_MS}ms)"
                            ),
                            component_id=cid,
                            solver_budget_ms=solver_budget,
                        )
                    )

                # Exceeds per-query limit
                if solver_budget > max_query_ms:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Component '{comp.name}' ({cid}) solver budget {solver_budget}ms "
                                f"exceeds per-query limit of {max_query_ms}ms"
                            ),
                            component_id=cid,
                            solver_budget_ms=solver_budget,
                            limit_ms=max_query_ms,
                        )
                    )

                total_budget_ms += solver_budget

        # Total budget check
        if total_budget_ms > max_total_ms:
            violations.append(
                self._violation(
                    severity="critical",
                    message=(
                        f"Total solver budget {total_budget_ms:.0f}ms exceeds global limit "
                        f"of {max_total_ms}ms across {len(solver_components)} components"
                    ),
                    total_budget_ms=total_budget_ms,
                    limit_ms=max_total_ms,
                    solver_component_count=len(solver_components),
                )
            )

        # Too many concurrent solvers
        if len(solver_components) > _MAX_CONCURRENT_SOLVERS:
            violations.append(
                self._violation(
                    severity="warning",
                    message=(
                        f"{len(solver_components)} components use solvers, exceeding the "
                        f"recommended maximum of {_MAX_CONCURRENT_SOLVERS}"
                    ),
                    solver_component_count=len(solver_components),
                    max_concurrent=_MAX_CONCURRENT_SOLVERS,
                )
            )

        log.info(
            "solver_budget_pass.complete",
            violations=len(violations),
            total_budget_ms=total_budget_ms,
        )
        return violations
