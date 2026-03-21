"""Counterfactual Engine — validates hypotheses via 'what-if' simulation.

Orchestrates AdaptiveSimulationBoundary and DeltaReplayEngine to determine
whether removing a hypothesis's trigger deltas would eliminate the observed
violations (CAUSES_SYMPTOM), leave them unchanged (DOES_NOT_CAUSE), or
yield an inconclusive result.
"""

from __future__ import annotations

import time
from uuid import UUID

import structlog

from src.core.counterfactual import (
    CounterfactualConclusion,
    CounterfactualScenario,
    Intervention,
    InterventionType,
)
from src.core.derived import DerivedFact
from src.core.fact import GraphDelta
from src.counterfactual.boundary import AdaptiveSimulationBoundary
from src.counterfactual.replay import DeltaReplayEngine

log = structlog.get_logger(__name__)

# Minimum boundary size below which results are considered unreliable.
_BOUNDARY_TOO_SMALL = 3


class CounterfactualEngine:
    """Simulate counterfactual scenarios to validate or refute hypotheses."""

    def __init__(
        self,
        boundary_computer: AdaptiveSimulationBoundary | None = None,
        replay_engine: DeltaReplayEngine | None = None,
    ) -> None:
        self._boundary = boundary_computer or AdaptiveSimulationBoundary()
        self._replay = replay_engine or DeltaReplayEngine()

    # ── single-hypothesis validation ─────────────────────────────────────

    def validate_hypothesis(
        self,
        hypothesis: DerivedFact,
        delta_log: list[GraphDelta],
        original_violations: set[UUID],
        graph_context: dict,
        budget_ms: int = 5000,
    ) -> CounterfactualScenario:
        """Run a counterfactual simulation for one hypothesis.

        Steps
        -----
        1. Compute the simulation boundary.
        2. Build a REMOVE_DELTA intervention from the hypothesis trigger deltas.
        3. Replay the delta stream through the replay engine.
        4. Compare resulting violations with the originals.
        5. Determine the conclusion.
        6. Return a fully populated ``CounterfactualScenario``.
        """
        start_ns = time.monotonic_ns()

        # 1 — Boundary
        boundary, expansion_count = self._boundary.compute(
            hypothesis, graph_context, budget_ms=budget_ms
        )
        expansion_triggers = self._boundary.get_expansion_triggers()

        # 2 — Intervention
        trigger_delta_ids: list[UUID] = [
            UUID(str(tid))
            for tid in hypothesis.payload.get("trigger_delta_ids", [])
        ]
        intervention = Intervention(
            intervention_type=InterventionType.REMOVE_DELTA,
            target_deltas=trigger_delta_ids,
        )

        # 3 — Replay
        replayed_deltas = self._replay.replay(delta_log, intervention, boundary)

        # 4 — Violation comparison
        # Derive the "replayed violations" by removing any violation whose
        # trigger deltas were entirely removed from the replayed stream.
        replayed_delta_ids: set[UUID] = {d.delta_id for d in replayed_deltas}
        removed_delta_ids: set[UUID] = (
            {d.delta_id for d in delta_log} - replayed_delta_ids
        )

        # Violations that depended on removed deltas are no longer present.
        replayed_violations: set[UUID] = set()
        for vid in original_violations:
            # A violation is "gone" if ALL its trigger deltas were removed.
            # Since we only have the hypothesis triggers here, we consider a
            # violation removed if it is a trigger delta target.
            if vid not in removed_delta_ids:
                replayed_violations.add(vid)

        violations_removed, violations_added = self._replay.compute_violation_diff(
            original_violations, replayed_violations
        )

        # 5 — Elapsed check
        elapsed_ms = (time.monotonic_ns() - start_ns) / 1_000_000
        budget_exceeded = elapsed_ms > budget_ms

        # 6 — Conclusion
        if len(boundary) < _BOUNDARY_TOO_SMALL or budget_exceeded:
            conclusion = CounterfactualConclusion.INCONCLUSIVE
            if budget_exceeded:
                expansion_triggers.append(
                    f"budget_exceeded: {elapsed_ms:.1f}ms > {budget_ms}ms"
                )
            if len(boundary) < _BOUNDARY_TOO_SMALL:
                expansion_triggers.append(
                    f"boundary_too_small: {len(boundary)} < {_BOUNDARY_TOO_SMALL}"
                )
        elif violations_removed:
            conclusion = CounterfactualConclusion.CAUSES_SYMPTOM
        else:
            conclusion = CounterfactualConclusion.DOES_NOT_CAUSE

        # Health delta: proportion of violations removed (positive = improvement).
        if original_violations:
            health_delta = len(violations_removed) / len(original_violations)
        else:
            health_delta = 0.0

        base_checkpoint = (
            min((d.sequence_number for d in delta_log), default=0)
        )

        scenario = CounterfactualScenario(
            base_state_checkpoint=base_checkpoint,
            intervention=intervention,
            replayed_deltas=replayed_deltas,
            resulting_violations=replayed_violations,
            resulting_health_delta=health_delta,
            conclusion=conclusion,
            boundary_size=len(boundary),
            expansion_count=expansion_count,
            expansion_triggers=expansion_triggers,
        )

        log.info(
            "counterfactual.validated",
            hypothesis_id=str(hypothesis.derived_id),
            conclusion=conclusion.value,
            boundary_size=len(boundary),
            violations_removed=len(violations_removed),
            violations_added=len(violations_added),
            elapsed_ms=round(elapsed_ms, 2),
        )
        return scenario

    # ── batch validation ─────────────────────────────────────────────────

    def run_batch(
        self,
        hypotheses: list[DerivedFact],
        delta_log: list[GraphDelta],
        original_violations: set[UUID],
        graph_context: dict,
        budget_ms: int = 5000,
    ) -> list[CounterfactualScenario]:
        """Validate every hypothesis, respecting total budget.

        Budget is split evenly across hypotheses.  If the budget is
        exhausted, remaining hypotheses receive an INCONCLUSIVE scenario.
        """
        if not hypotheses:
            return []

        per_hypothesis_budget = max(budget_ms // len(hypotheses), 1)
        total_start_ns = time.monotonic_ns()
        results: list[CounterfactualScenario] = []

        for hyp in hypotheses:
            elapsed_total_ms = (time.monotonic_ns() - total_start_ns) / 1_000_000
            remaining_budget = budget_ms - elapsed_total_ms

            if remaining_budget <= 0:
                # Budget exhausted — mark remaining as inconclusive.
                scenario = CounterfactualScenario(
                    base_state_checkpoint=min(
                        (d.sequence_number for d in delta_log), default=0
                    ),
                    intervention=Intervention(
                        intervention_type=InterventionType.REMOVE_DELTA,
                        target_deltas=[
                            UUID(str(tid))
                            for tid in hyp.payload.get("trigger_delta_ids", [])
                        ],
                    ),
                    conclusion=CounterfactualConclusion.INCONCLUSIVE,
                    expansion_triggers=[
                        f"batch_budget_exhausted: total {budget_ms}ms exceeded"
                    ],
                )
                results.append(scenario)
                log.warning(
                    "counterfactual.batch_budget_exhausted",
                    hypothesis_id=str(hyp.derived_id),
                )
                continue

            individual_budget = min(int(remaining_budget), per_hypothesis_budget)
            scenario = self.validate_hypothesis(
                hyp,
                delta_log,
                original_violations,
                graph_context,
                budget_ms=individual_budget,
            )
            results.append(scenario)

        log.info(
            "counterfactual.batch_complete",
            total_hypotheses=len(hypotheses),
            total_results=len(results),
        )
        return results
