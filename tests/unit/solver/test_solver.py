"""Unit tests for the Solver subsystem (Phase 3).

Covers Z3Translator, SolverBudget, ReteFallback, GreedyVersionResolver,
and ConstraintSolverLayer.
"""

from __future__ import annotations

import z3
import pytest

from src.solver.translator import Z3Translator, TranslationError
from src.solver.budget import SolverBudget, ComplexityClass
from src.solver.fallback import ReteFallback, FallbackResult, GreedyVersionResolver
from src.solver.layer import ConstraintSolverLayer


# ---------------------------------------------------------------------------
# Z3Translator tests
# ---------------------------------------------------------------------------

class TestZ3Translator:
    """Tests for Z3Translator.translate and translate_batch."""

    def test_translate_simple_constraint(self):
        """translate() on valid SMT-LIB2 returns a list of z3.BoolRef."""
        translator = Z3Translator()
        smt = "(declare-const x Int) (assert (> x 0))"
        result = translator.translate(smt)

        assert isinstance(result, list)
        assert len(result) >= 1
        for item in result:
            assert isinstance(item, z3.BoolRef)

    def test_translate_batch(self):
        """translate_batch() collects constraints from multiple SMT-LIB2 strings."""
        translator = Z3Translator()
        constraints = [
            "(declare-const a Int) (assert (> a 0))",
            "(declare-const b Int) (assert (< b 10))",
        ]
        result = translator.translate_batch(constraints)

        assert isinstance(result, list)
        assert len(result) >= 2
        for item in result:
            assert isinstance(item, z3.BoolRef)

    def test_translate_invalid_returns_empty(self):
        """translate() raises TranslationError on garbage input."""
        translator = Z3Translator()
        with pytest.raises(TranslationError):
            translator.translate("this is not valid SMT-LIB2 at all %%%")


# ---------------------------------------------------------------------------
# SolverBudget tests
# ---------------------------------------------------------------------------

class TestSolverBudget:
    """Tests for SolverBudget allocation and exhaustion tracking."""

    def test_allocate_by_complexity(self):
        """allocate() returns the canonical ms for each complexity class."""
        budget = SolverBudget(total_budget_ms=5000.0)

        assert budget.allocate(ComplexityClass.SIMPLE) == 50.0
        assert budget.allocate(ComplexityClass.MODERATE) == 200.0
        assert budget.allocate(ComplexityClass.COMPLEX) == 500.0

    def test_budget_exhaustion(self):
        """record_usage until total consumed >= total budget, then is_exhausted()."""
        budget = SolverBudget(total_budget_ms=100.0)
        assert not budget.is_exhausted()

        budget.record_usage(60.0)
        assert not budget.is_exhausted()

        budget.record_usage(40.0)
        assert budget.is_exhausted()

    def test_remaining_ms(self):
        """remaining_ms() tracks what is left after recorded usage."""
        budget = SolverBudget(total_budget_ms=500.0)
        assert budget.remaining_ms() == 500.0

        budget.record_usage(150.0)
        assert budget.remaining_ms() == 350.0

        budget.record_usage(350.0)
        assert budget.remaining_ms() == 0.0

        # Further usage does not go negative
        budget.record_usage(50.0)
        assert budget.remaining_ms() == 0.0


# ---------------------------------------------------------------------------
# ReteFallback + GreedyVersionResolver tests
# ---------------------------------------------------------------------------

class TestReteFallback:
    """Tests for the heuristic Rete fallback solver."""

    def test_rete_fallback_returns_result(self):
        """check() on non-empty constraints returns a FallbackResult."""
        fb = ReteFallback()
        constraints = [
            "(declare-const x Int) (assert (> x 0))",
        ]
        result = fb.check(constraints)

        assert isinstance(result, FallbackResult)
        assert result.method == "rete_fallback"
        # For a non-trivial constraint set, result is inconclusive or has a model
        assert result.confidence >= 0.0

    def test_rete_fallback_detects_contradiction(self):
        """check() on an obvious contradiction returns satisfiable=False."""
        fb = ReteFallback()
        result = fb.check(["(assert false)"])

        assert isinstance(result, FallbackResult)
        assert result.satisfiable is False
        assert result.confidence >= 0.9


class TestGreedyVersionResolver:
    """Tests for the greedy version resolution fallback."""

    def test_greedy_resolver(self):
        """resolve() picks versions greedily from constraints."""
        resolver = GreedyVersionResolver()
        constraints = [
            {"package": "numpy", "min_version": "1.20", "max_version": "1.25"},
            {"package": "pandas", "exact_version": "2.0.0"},
            {"package": "requests", "min_version": "2.28"},
        ]
        resolved = resolver.resolve(constraints)

        assert isinstance(resolved, dict)
        assert resolved["numpy"] == "1.25"  # greedy picks max_version
        assert resolved["pandas"] == "2.0.0"  # exact takes precedence
        assert resolved["requests"] == "2.28"  # only min_version given


# ---------------------------------------------------------------------------
# ConstraintSolverLayer tests
# ---------------------------------------------------------------------------

class TestConstraintSolverLayer:
    """Tests for the top-level ConstraintSolverLayer orchestrator."""

    def test_check_satisfiability_sat(self):
        """Satisfiable constraints yield sat=True with a model."""
        layer = ConstraintSolverLayer()
        constraints = [
            "(declare-const x Int) (assert (> x 0)) (assert (< x 10))",
        ]
        result = layer.check_satisfiability(constraints)

        assert result.satisfiable is True
        assert result.model is not None
        assert "x" in result.model

    def test_check_satisfiability_unsat(self):
        """Contradictory constraints yield sat=False."""
        layer = ConstraintSolverLayer()
        constraints = [
            "(declare-const x Int) (assert (> x 10)) (assert (< x 5))",
        ]
        result = layer.check_satisfiability(constraints)

        assert result.satisfiable is False

    def test_check_with_budget(self):
        """Solving with a budget records consumption on the budget tracker."""
        budget = SolverBudget(total_budget_ms=5000.0)
        layer = ConstraintSolverLayer(budget=budget)

        constraints = [
            "(declare-const y Int) (assert (> y 0))",
        ]
        result = layer.check_satisfiability(constraints)

        # Budget should have recorded some non-zero usage
        assert budget.consumed_ms > 0.0
        assert result.duration_ms >= 0.0
        assert result.satisfiable is True
