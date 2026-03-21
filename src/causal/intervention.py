"""Intervention engine -- do-calculus operations on a CBN.

Provides :class:`InterventionScorer` which simulates do(X=v) interventions
on a :class:`CausalBayesianNetwork` to quantify causal influence and rank
candidate root causes.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.causal.cbn import CausalBayesianNetwork

logger = structlog.get_logger(__name__)


class InterventionScorer:
    """Scores candidate nodes by simulated causal interventions.

    All interventions use the *do-operator* pattern: clamp a node to a
    specific value, run inference, and measure the downstream effect.
    """

    # ── Public API ────────────────────────────────────────────────────

    def score_candidates(
        self,
        cbn: CausalBayesianNetwork,
        candidates: list[UUID],
    ) -> list[tuple[UUID, float]]:
        """Score each candidate by simulating a do(X=0) intervention.

        For every candidate node the method:

        1. Runs a *baseline* inference with no intervention.
        2. Clamps the candidate to 0.0 (``do(X=0)``).
        3. Measures the total absolute change in its children's posteriors.

        Args:
            cbn: The Causal Bayesian Network to operate on.
            candidates: Node ids to evaluate.

        Returns:
            List of ``(node_id, impact_score)`` tuples sorted by score
            descending.
        """
        if not candidates:
            return []

        # Baseline: inference with no evidence
        baseline = cbn.infer({})

        results: list[tuple[UUID, float]] = []

        for cand in candidates:
            if cbn.get_node(cand) is None:
                logger.debug(
                    "intervention.skip_missing_node", node_id=str(cand)
                )
                continue

            # Intervene: do(candidate = 0.0)
            evidence = {cand: 0.0}
            intervened = cbn.infer(evidence)

            # Measure impact on children
            children = cbn.get_children(cand)
            impact = 0.0
            for child_id in children:
                base_val = baseline.get(child_id, 0.5)
                new_val = intervened.get(child_id, 0.5)
                impact += abs(base_val - new_val)

            results.append((cand, impact))
            logger.debug(
                "intervention.scored",
                node_id=str(cand),
                impact=round(impact, 6),
                children_count=len(children),
            )

        # Sort by impact descending
        results.sort(key=lambda t: t[1], reverse=True)
        return results

    def compute_causal_effect(
        self,
        cbn: CausalBayesianNetwork,
        source: UUID,
        target: UUID,
    ) -> float:
        """Compute the causal effect of *source* on *target*.

        Defined as::

            effect = P(target | do(source=1)) - P(target | do(source=0))

        A positive value means *source* being active raises the posterior
        of *target*.

        Args:
            cbn: The Causal Bayesian Network.
            source: The intervened-on node.
            target: The node whose posterior change is measured.

        Returns:
            The signed causal effect (float in [-1, 1]).
        """
        if cbn.get_node(source) is None or cbn.get_node(target) is None:
            logger.warning(
                "intervention.missing_node",
                source=str(source),
                target=str(target),
            )
            return 0.0

        post_high = cbn.infer({source: 1.0})
        post_low = cbn.infer({source: 0.0})

        effect = post_high.get(target, 0.5) - post_low.get(target, 0.5)

        logger.debug(
            "intervention.causal_effect",
            source=str(source),
            target=str(target),
            effect=round(effect, 6),
        )
        return effect
