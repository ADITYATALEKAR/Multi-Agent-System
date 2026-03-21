"""Solver layer -- top-level interface to the formal constraint solver.

Orchestrates Z3-based constraint solving with budget enforcement and
automatic fallback to heuristic solvers when the primary solver times out.
"""

from __future__ import annotations

import time
from typing import Optional

import structlog
import z3

from src.core.certificate import SolverResult
from src.solver.budget import ComplexityClass, SolverBudget
from src.solver.fallback import ReteFallback
from src.solver.translator import TranslationError, Z3Translator

logger = structlog.get_logger(__name__)


class ConstraintSolverLayer:
    """Orchestrates constraint solving with budget management and fallback.

    Flow:
        1. Translate SMT-LIB2 constraints via Z3Translator.
        2. Create z3.Solver with timeout from SolverBudget.
        3. Add constraints and check satisfiability.
        4. If solver times out, fall back to ReteFallback.
        5. Return a SolverResult (from src.core.certificate).
    """

    def __init__(
        self,
        translator: Optional[Z3Translator] = None,
        budget: Optional[SolverBudget] = None,
    ) -> None:
        """Initialize the solver layer.

        Args:
            translator: Z3Translator instance for SMT-LIB2 parsing.
                Defaults to a new Z3Translator.
            budget: SolverBudget for time allocation.
                Defaults to a new SolverBudget with 1000ms total.
        """
        self._translator = translator or Z3Translator()
        self._budget = budget or SolverBudget()
        self._fallback = ReteFallback()
        logger.debug("constraint_solver_layer_init")

    @property
    def translator(self) -> Z3Translator:
        """The Z3Translator used by this layer."""
        return self._translator

    @property
    def budget(self) -> SolverBudget:
        """The SolverBudget used by this layer."""
        return self._budget

    def check_satisfiability(
        self,
        constraints: list[str],
        budget: Optional[SolverBudget] = None,
    ) -> SolverResult:
        """Check satisfiability of a set of SMT-LIB2 constraints.

        Translates the constraints, runs Z3 with a time budget, and returns
        the result. Falls back to ReteFallback on timeout or translation error.

        Args:
            constraints: List of SMT-LIB2 constraint strings.
            budget: Optional per-call budget override.

        Returns:
            SolverResult with satisfiability status, model, and timing info.
        """
        active_budget = budget or self._budget
        query_str = "; ".join(c[:60] for c in constraints[:5])

        if active_budget.is_exhausted():
            logger.warning("solver_budget_exhausted_before_start")
            return SolverResult(
                solver_id="z3",
                query=query_str,
                satisfiable=None,
                model=None,
                duration_ms=0.0,
                complexity_class="simple",
            )

        # Determine complexity heuristic based on constraint count
        complexity = self._estimate_complexity(constraints)
        timeout_ms = active_budget.allocate(complexity)

        if timeout_ms <= 0:
            logger.warning("solver_zero_budget_allocated", complexity=complexity.value)
            return self._run_fallback(constraints, query_str, complexity)

        # Attempt Z3 solving
        start = time.perf_counter()
        try:
            z3_constraints = self._translator.translate_batch(constraints)
        except TranslationError as exc:
            duration_ms = (time.perf_counter() - start) * 1000.0
            active_budget.record_usage(duration_ms)
            logger.warning("solver_translation_failed", error=str(exc))
            return self._run_fallback(constraints, query_str, complexity)

        solver = z3.Solver()
        solver.set("timeout", int(timeout_ms))
        for c in z3_constraints:
            solver.add(c)

        result = solver.check()
        duration_ms = (time.perf_counter() - start) * 1000.0
        active_budget.record_usage(duration_ms)

        if result == z3.sat:
            model = solver.model()
            model_dict = {
                str(d): str(model[d]) for d in model.decls()
            }
            logger.info(
                "solver_sat",
                duration_ms=round(duration_ms, 2),
                model_size=len(model_dict),
            )
            return SolverResult(
                solver_id="z3",
                query=query_str,
                satisfiable=True,
                model=model_dict,
                duration_ms=round(duration_ms, 2),
                complexity_class=complexity.value,
            )
        elif result == z3.unsat:
            logger.info("solver_unsat", duration_ms=round(duration_ms, 2))
            return SolverResult(
                solver_id="z3",
                query=query_str,
                satisfiable=False,
                model=None,
                duration_ms=round(duration_ms, 2),
                complexity_class=complexity.value,
            )
        else:
            # z3.unknown -- typically a timeout
            logger.warning(
                "solver_unknown_falling_back",
                duration_ms=round(duration_ms, 2),
                reason=solver.reason_unknown(),
            )
            return self._run_fallback(constraints, query_str, complexity)

    def check_feasibility(
        self,
        target_laws: list[str],
        subgraph_constraints: list[str],
    ) -> bool:
        """Check whether a set of target laws is feasible given subgraph constraints.

        Combines target law constraints with subgraph constraints and checks
        satisfiability. Returns True if the combined set is satisfiable.

        Args:
            target_laws: SMT-LIB2 constraints representing target laws.
            subgraph_constraints: SMT-LIB2 constraints from the operational
                state subgraph.

        Returns:
            True if the combined constraints are satisfiable, False otherwise.
        """
        combined = target_laws + subgraph_constraints
        if not combined:
            return True

        result = self.check_satisfiability(combined)
        # Treat inconclusive (None) as infeasible to be conservative
        return result.satisfiable is True

    def _estimate_complexity(self, constraints: list[str]) -> ComplexityClass:
        """Estimate the complexity class based on constraint characteristics.

        Args:
            constraints: SMT-LIB2 constraint strings.

        Returns:
            Estimated ComplexityClass.
        """
        total_len = sum(len(c) for c in constraints)
        count = len(constraints)

        if count <= 2 and total_len < 500:
            return ComplexityClass.SIMPLE
        elif count <= 10 and total_len < 5000:
            return ComplexityClass.MODERATE
        else:
            return ComplexityClass.COMPLEX

    def _run_fallback(
        self,
        constraints: list[str],
        query_str: str,
        complexity: ComplexityClass,
    ) -> SolverResult:
        """Run the Rete fallback solver.

        Args:
            constraints: Original SMT-LIB2 constraint strings.
            query_str: Summary string for the SolverResult query field.
            complexity: The estimated complexity class.

        Returns:
            SolverResult from the fallback.
        """
        start = time.perf_counter()
        fb_result = self._fallback.check(constraints)
        duration_ms = (time.perf_counter() - start) * 1000.0

        logger.info(
            "fallback_result",
            method=fb_result.method,
            satisfiable=fb_result.satisfiable,
            confidence=fb_result.confidence,
            duration_ms=round(duration_ms, 2),
        )
        return SolverResult(
            solver_id=fb_result.method,
            query=query_str,
            satisfiable=fb_result.satisfiable,
            model=fb_result.model if fb_result.model else None,
            duration_ms=round(duration_ms, 2),
            complexity_class=complexity.value,
        )
