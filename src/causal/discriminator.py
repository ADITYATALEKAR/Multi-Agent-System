"""Causal discriminator -- ranks hypotheses using CBN inference.

:class:`CausalDiscriminator` evaluates competing hypotheses by injecting
them as evidence into a :class:`CausalBayesianNetwork` and measuring
their downstream causal influence on observed violations.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.causal.cbn import CausalBayesianNetwork
from src.causal.intervention import InterventionScorer
from src.core.derived import DerivedFact

logger = structlog.get_logger(__name__)


class CausalDiscriminator:
    """Discriminates among competing hypotheses using causal inference.

    Hypotheses are :class:`DerivedFact` objects.  Each is evaluated by
    how strongly it influences violation nodes inside the CBN when
    injected as evidence.
    """

    def __init__(self) -> None:
        self._scorer = InterventionScorer()

    # ── Public API ────────────────────────────────────────────────────

    def discriminate(
        self,
        hypotheses: list[DerivedFact],
        cbn: CausalBayesianNetwork,
    ) -> list[DerivedFact]:
        """Re-rank hypotheses based on causal plausibility.

        For each hypothesis:

        1. Extract entity ids from ``payload["entity_ids"]``.
        2. For each entity present in the CBN, run inference with
           that entity clamped to 1.0.
        3. Compute the total influence on all *violation*-type nodes.
        4. Sum influence across all entities to produce a causal score.

        Hypotheses are returned sorted by causal score descending.

        Args:
            hypotheses: Candidate hypotheses to evaluate.
            cbn: Causal Bayesian Network encoding domain structure.

        Returns:
            Hypotheses re-ranked by causal support score.
        """
        if not hypotheses:
            return []

        # Identify violation nodes in the CBN
        violation_ids = _find_violation_nodes(cbn)

        # Baseline inference (no evidence)
        baseline = cbn.infer({})

        scored: list[tuple[DerivedFact, float]] = []

        for hyp in hypotheses:
            entity_ids = _extract_entity_ids(hyp)
            causal_score = 0.0

            for eid in entity_ids:
                if cbn.get_node(eid) is None:
                    continue

                # Inject hypothesis entity as observed (evidence=1.0)
                posterior = cbn.infer({eid: 1.0})

                # Measure influence on violation nodes
                for vid in violation_ids:
                    base_val = baseline.get(vid, 0.5)
                    new_val = posterior.get(vid, 0.5)
                    causal_score += abs(new_val - base_val)

            scored.append((hyp, causal_score))
            logger.debug(
                "discriminator.hypothesis_scored",
                hypothesis_id=str(hyp.derived_id),
                causal_score=round(causal_score, 6),
                entity_count=len(entity_ids),
            )

        # Sort by causal score descending
        scored.sort(key=lambda t: t[1], reverse=True)

        ranked = [hyp for hyp, _ in scored]
        logger.info(
            "discriminator.ranked",
            hypothesis_count=len(ranked),
            top_score=round(scored[0][1], 6) if scored else 0.0,
        )
        return ranked

    def rank_root_causes(
        self,
        cbn: CausalBayesianNetwork,
        violation_ids: list[UUID],
        candidate_ids: list[UUID],
    ) -> list[tuple[UUID, float]]:
        """Rank candidate root causes by average causal effect on violations.

        For each candidate, compute the causal effect on every violation
        (via ``do(candidate=1) - do(candidate=0)``), then average.

        Args:
            cbn: The Causal Bayesian Network.
            violation_ids: Node ids of observed violations.
            candidate_ids: Node ids of potential root causes.

        Returns:
            List of ``(candidate_id, avg_effect)`` sorted descending.
        """
        if not candidate_ids or not violation_ids:
            return []

        results: list[tuple[UUID, float]] = []

        for cand in candidate_ids:
            if cbn.get_node(cand) is None:
                continue

            total_effect = 0.0
            count = 0
            for vid in violation_ids:
                if cbn.get_node(vid) is None:
                    continue
                effect = self._scorer.compute_causal_effect(cbn, cand, vid)
                total_effect += abs(effect)
                count += 1

            avg_effect = total_effect / count if count > 0 else 0.0
            results.append((cand, avg_effect))

            logger.debug(
                "discriminator.root_cause_scored",
                candidate=str(cand),
                avg_effect=round(avg_effect, 6),
                violation_count=count,
            )

        results.sort(key=lambda t: t[1], reverse=True)

        logger.info(
            "discriminator.root_causes_ranked",
            candidates=len(results),
            top_effect=round(results[0][1], 6) if results else 0.0,
        )
        return results


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_violation_nodes(cbn: CausalBayesianNetwork) -> list[UUID]:
    """Return node ids whose type is 'violation'."""
    result: list[UUID] = []
    for nid in cbn.all_node_ids():
        info = cbn.get_node(nid)
        if info and info["node_type"] == "violation":
            result.append(nid)
    return result


def _extract_entity_ids(fact: DerivedFact) -> list[UUID]:
    """Extract entity UUID list from a DerivedFact's payload."""
    raw = fact.payload.get("entity_ids", [])
    ids: list[UUID] = []
    for item in raw:
        if isinstance(item, UUID):
            ids.append(item)
        else:
            try:
                ids.append(UUID(str(item)))
            except (ValueError, AttributeError):
                continue
    return ids
