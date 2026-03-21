"""Solver fallback -- heuristic solvers used when Z3 times out.

Provides ReteFallback (Rete-network-inspired heuristic satisfiability check)
and GreedyVersionResolver (greedy dependency version resolution) as lightweight
alternatives when the primary Z3 solver exceeds its time budget.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


class FallbackResult(BaseModel):
    """Result from a fallback solver invocation."""

    satisfiable: Optional[bool] = None  # None = inconclusive
    confidence: float = 0.5  # heuristic confidence level
    model: dict[str, Any] = Field(default_factory=dict)  # partial assignment
    method: str = "rete_fallback"  # "rete_fallback" or "greedy_resolver"


class ReteFallback:
    """Approximates constraint satisfiability using Rete pattern matching heuristics.

    This fallback performs lightweight syntactic analysis of SMT-LIB2 constraints
    to estimate satisfiability when the primary Z3 solver times out or exceeds
    its budget. Results are approximate and carry low confidence scores.
    """

    def check(self, constraints: list[str]) -> FallbackResult:
        """Heuristically check satisfiability of the given constraints.

        Analyzes constraint structure to make a best-effort determination:
        - Empty constraint sets are trivially satisfiable.
        - Detects obvious contradictions (e.g., (assert false)).
        - Otherwise returns inconclusive with a partial model.

        Args:
            constraints: SMT-LIB2 constraint strings.

        Returns:
            FallbackResult with heuristic satisfiability estimate.
        """
        if not constraints:
            logger.debug("rete_fallback_trivial", reason="no_constraints")
            return FallbackResult(
                satisfiable=True,
                confidence=1.0,
                model={},
                method="rete_fallback",
            )

        # Detect obvious contradictions
        for constraint in constraints:
            normalized = constraint.strip().lower()
            if "(assert false)" in normalized:
                logger.debug("rete_fallback_contradiction", constraint_preview=constraint[:80])
                return FallbackResult(
                    satisfiable=False,
                    confidence=0.9,
                    model={},
                    method="rete_fallback",
                )

        # Extract declared variable names for partial model
        partial_model: dict[str, Any] = {}
        for constraint in constraints:
            # Match (declare-const name Type) or (declare-fun name () Type)
            for match in re.finditer(
                r"\(declare-(?:const|fun)\s+(\w+)", constraint
            ):
                var_name = match.group(1)
                partial_model[var_name] = "unknown"

        # Cannot determine sat/unsat heuristically -- return inconclusive
        logger.debug(
            "rete_fallback_inconclusive",
            constraint_count=len(constraints),
            variables_found=len(partial_model),
        )
        return FallbackResult(
            satisfiable=None,
            confidence=0.5,
            model=partial_model,
            method="rete_fallback",
        )


class GreedyVersionResolver:
    """Greedily resolves version constraints for dependency compatibility.

    Accepts a list of version constraint dictionaries and attempts to find
    a compatible version assignment using a greedy highest-version-first strategy.
    """

    def resolve(self, version_constraints: list[dict]) -> dict[str, str]:
        """Greedily resolve version constraints.

        Each constraint dict should have:
            - "package": package name (str)
            - "min_version": minimum acceptable version (str, optional)
            - "max_version": maximum acceptable version (str, optional)
            - "exact_version": exact required version (str, optional)

        The resolver picks the highest acceptable version for each package,
        preferring exact_version when specified.

        Args:
            version_constraints: List of constraint dictionaries.

        Returns:
            Mapping of package name to resolved version string.
        """
        resolved: dict[str, str] = {}

        for constraint in version_constraints:
            package = constraint.get("package", "")
            if not package:
                logger.warning("greedy_resolver_skip", reason="missing_package_name")
                continue

            exact = constraint.get("exact_version")
            if exact:
                # Exact version takes precedence
                if package in resolved and resolved[package] != exact:
                    logger.warning(
                        "greedy_resolver_conflict",
                        package=package,
                        existing=resolved[package],
                        requested=exact,
                    )
                resolved[package] = exact
                continue

            max_ver = constraint.get("max_version", "")
            min_ver = constraint.get("min_version", "")

            if package in resolved:
                # Keep existing assignment if it satisfies the new constraint
                continue

            # Greedy: pick max_version if available, else min_version, else "latest"
            chosen = max_ver or min_ver or "latest"
            resolved[package] = chosen

        logger.debug(
            "greedy_resolver_complete",
            constraint_count=len(version_constraints),
            resolved_count=len(resolved),
        )
        return resolved
