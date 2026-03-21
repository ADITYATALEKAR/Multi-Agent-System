"""IIE Engine: orchestrates all verification passes."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation
from src.iie.passes.cache_lineage import CacheLineagePass
from src.iie.passes.circular_dep import CircularDepPass
from src.iie.passes.contract import ContractPass
from src.iie.passes.dataflow import DataflowPass
from src.iie.passes.delta_consumption import DeltaConsumptionPass
from src.iie.passes.determinism import DeterminismPass
from src.iie.passes.nondeterminism import NondeterminismPass
from src.iie.passes.solver_budget import SolverBudgetPass
from src.iie.passes.split_graph import SplitGraphPass
from src.iie.passes.stale_derived import StaleDerivedPass
from src.iie.passes.storage_budget import StorageBudgetPass
from src.iie.passes.structural import StructuralPass

log = structlog.get_logger(__name__)

# Pass IDs 9-12 are considered runtime passes
_RUNTIME_PASS_IDS = {9, 10, 11, 12}


def _default_passes() -> list[BasePass]:
    """Create the full set of 12 IIE passes in order."""
    return [
        StructuralPass(),       # 1
        CircularDepPass(),      # 2
        ContractPass(),         # 3
        DataflowPass(),         # 4
        DeterminismPass(),      # 5
        NondeterminismPass(),   # 6
        SplitGraphPass(),       # 7
        DeltaConsumptionPass(), # 8
        CacheLineagePass(),     # 9
        StaleDerivedPass(),     # 10
        SolverBudgetPass(),     # 11
        StorageBudgetPass(),    # 12
    ]


class IIEEngine:
    """Orchestrates all IIE verification passes.

    Can run all 12 passes at load time, a specific pass by ID,
    or only the runtime-triggered passes (9-12).
    """

    def __init__(self, passes: list[BasePass] | None = None) -> None:
        self._passes: list[BasePass] = passes if passes is not None else _default_passes()
        self._pass_index: dict[int, BasePass] = {p.PASS_ID: p for p in self._passes}
        log.info("iie_engine.init", num_passes=len(self._passes))

    @property
    def passes(self) -> list[BasePass]:
        """Return the list of registered passes."""
        return list(self._passes)

    def run_load_time_passes(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        """Run all passes at load time. Blocking."""
        all_violations: list[IntegrityViolation] = []
        for p in self._passes:
            log.debug("iie_engine.running_pass", pass_id=p.PASS_ID, pass_name=p.PASS_NAME)
            violations = p.run(ir)
            all_violations.extend(violations)
        log.info(
            "iie_engine.load_time_complete",
            total_violations=len(all_violations),
            passes_run=len(self._passes),
        )
        return all_violations

    def run_pass(self, pass_id: int, ir: ArchitectureIR) -> list[IntegrityViolation]:
        """Run a specific pass by ID."""
        p = self._pass_index.get(pass_id)
        if p is None:
            log.warning("iie_engine.pass_not_found", pass_id=pass_id)
            return []
        log.debug("iie_engine.running_pass", pass_id=p.PASS_ID, pass_name=p.PASS_NAME)
        return p.run(ir)

    def run_runtime_passes(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        """Run only runtime-triggered passes (passes 9-12)."""
        all_violations: list[IntegrityViolation] = []
        for p in self._passes:
            if p.PASS_ID in _RUNTIME_PASS_IDS:
                log.debug(
                    "iie_engine.running_runtime_pass",
                    pass_id=p.PASS_ID,
                    pass_name=p.PASS_NAME,
                )
                violations = p.run(ir)
                all_violations.extend(violations)
        log.info(
            "iie_engine.runtime_complete",
            total_violations=len(all_violations),
        )
        return all_violations
