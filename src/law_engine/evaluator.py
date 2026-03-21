"""LawEvaluator: compiles laws into the Rete network and evaluates deltas.

Bridges the LawLibrary (declarative law definitions) with the DFE
(ReteNetwork + RuleCompiler) to produce DerivedFact violations when
graph deltas match law conditions.

Solver-mode laws (EvalMode.SOLVER) are stubbed for Phase 3.
"""

from __future__ import annotations

from collections import defaultdict

import structlog

from src.core.derived import DerivedFact
from src.core.fact import GraphDelta
from src.dfe.compiler import RuleCompiler
from src.dfe.rete import ReteNetwork
from src.law_engine.law import EvalMode, LawCategory, LawDefinition
from src.law_engine.library import LawLibrary

logger = structlog.get_logger(__name__)


class LawEvaluator:
    """Evaluates laws against graph deltas via the Rete network.

    Lifecycle:
        1. ``__init__`` — wire up Rete, compiler, library.
        2. ``register_laws`` — compile enabled laws and feed RuleIR to Rete.
        3. ``evaluate_delta`` — push a GraphDelta; collect violations.
        4. ``get_violations`` / ``get_violation_count_by_category`` — inspect.
    """

    def __init__(
        self,
        rete: ReteNetwork,
        compiler: RuleCompiler,
        library: LawLibrary,
    ) -> None:
        self._rete = rete
        self._compiler = compiler
        self._library = library
        self._compiled = False
        self._violations: list[DerivedFact] = []
        self._law_id_to_category: dict[str, LawCategory] = {}

    # ── Registration ──────────────────────────────────────────────────────

    def register_laws(self, laws: list[LawDefinition] | None = None) -> None:
        """Compile laws and register their RuleIR in the Rete network.

        Args:
            laws: Specific laws to register.  When *None*, all enabled laws
                  from the library are used.

        Solver-mode laws are skipped (Phase 3 stub).
        """
        targets = laws if laws is not None else self._library.enabled_laws()
        registered = 0
        skipped_solver = 0

        for law in targets:
            if not law.enabled:
                continue

            if law.eval_mode == EvalMode.SOLVER:
                skipped_solver += 1
                logger.debug("law_skipped_solver_mode", law_id=law.law_id)
                continue

            rule_def = self._law_to_rule_def(law)
            try:
                rule_ir = self._compiler.compile(rule_def)
                self._rete.register_rule(rule_ir)
                self._law_id_to_category[law.law_id] = law.category
                registered += 1
            except Exception:
                logger.exception("law_compile_error", law_id=law.law_id)

        self._compiled = True
        logger.info(
            "laws_registered",
            registered=registered,
            skipped_solver=skipped_solver,
            total_rete_rules=self._rete.rule_count,
        )

    # ── Evaluation ────────────────────────────────────────────────────────

    def evaluate_delta(self, delta: GraphDelta) -> list[DerivedFact]:
        """Run *delta* through the Rete network and collect violations.

        Returns:
            Newly derived facts from this delta.
        """
        if not self._compiled:
            logger.warning("evaluate_before_compile")
            self.register_laws()

        derived = self._rete.evaluate(delta)
        self._violations.extend(derived)

        if derived:
            logger.info(
                "delta_evaluated",
                delta_id=str(delta.delta_id),
                new_violations=len(derived),
                total_violations=len(self._violations),
            )
        return derived

    # ── Queries ───────────────────────────────────────────────────────────

    def get_violations(self, tenant_id: str = "default") -> list[DerivedFact]:
        """Return all accumulated violations, optionally filtered by tenant."""
        if tenant_id == "default":
            return list(self._violations)
        return [
            v for v in self._violations
            if v.payload.get("tenant_id") == tenant_id
        ]

    def get_violation_count_by_category(self) -> dict[str, int]:
        """Count violations grouped by LawCategory value."""
        counts: dict[str, int] = defaultdict(int)
        for v in self._violations:
            rule_id = v.payload.get("rule_id", "")
            category = self._law_id_to_category.get(rule_id)
            if category is not None:
                counts[category.value] += 1
            else:
                counts["unknown"] += 1
        return dict(counts)

    def clear_violations(self) -> None:
        """Reset the accumulated violation list."""
        self._violations.clear()

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _law_to_rule_def(law: LawDefinition) -> dict:
        """Convert a LawDefinition into the dict format expected by RuleCompiler."""
        return {
            "rule_id": law.law_id,
            "name": law.name,
            "description": law.description,
            "category": law.category.value,
            "weight": law.weight,
            "conditions": law.conditions,
            "action": law.action,
        }
