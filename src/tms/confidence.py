"""Confidence propagation for the Truth Maintenance System.

Implements BFS-based confidence propagation through the belief graph with
a dampening epsilon of 0.005 (v3.3 B3).
"""

from __future__ import annotations

from collections import deque
from uuid import UUID

import structlog

from src.tms.belief import BeliefNode

logger = structlog.get_logger(__name__)

# v3.3 B3: dampening epsilon -- stop propagating when the absolute
# confidence change drops below this threshold.
EPSILON: float = 0.005


class ConfidencePropagator:
    """Propagates confidence changes through the belief dependency graph.

    Uses BFS from the initially-changed belief, recomputing each
    dependent belief's confidence as the weighted average of its
    supporting beliefs' confidences (weighted by the justification's
    ``confidence_weight``).  Propagation halts along any path where the
    absolute delta falls below ``EPSILON``.
    """

    def __init__(self, epsilon: float = EPSILON) -> None:
        self.epsilon = epsilon

    def propagate(
        self,
        belief_id: UUID,
        new_confidence: float,
        belief_graph: dict[UUID, BeliefNode],
    ) -> list[UUID]:
        """Propagate a confidence change starting from *belief_id*.

        Args:
            belief_id: The belief whose confidence was directly changed.
            new_confidence: The new confidence value for *belief_id*.
            belief_graph: Mapping of belief_id -> BeliefNode (the full graph).

        Returns:
            List of belief IDs whose confidence was modified (including
            the initial *belief_id* if it existed in the graph).
        """
        if belief_id not in belief_graph:
            logger.warning("propagate_unknown_belief", belief_id=str(belief_id))
            return []

        source = belief_graph[belief_id]
        old_confidence = source.confidence
        delta = abs(new_confidence - old_confidence)

        # Apply the initial change.
        source.confidence = max(0.0, min(1.0, new_confidence))
        affected: list[UUID] = [belief_id]

        if delta < self.epsilon:
            logger.debug(
                "propagation_dampened_at_source",
                belief_id=str(belief_id),
                delta=delta,
            )
            return affected

        # BFS through dependents.
        queue: deque[UUID] = deque()
        for dep_id in source.dependent_beliefs:
            if dep_id in belief_graph:
                queue.append(dep_id)

        visited: set[UUID] = {belief_id}

        while queue:
            current_id = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            current = belief_graph.get(current_id)
            if current is None:
                continue

            # Recompute confidence as weighted average of supporters.
            recomputed = self._recompute_confidence(current, belief_graph)
            change = abs(recomputed - current.confidence)

            if change < self.epsilon:
                logger.debug(
                    "propagation_dampened",
                    belief_id=str(current_id),
                    delta=change,
                )
                continue

            current.confidence = max(0.0, min(1.0, recomputed))
            affected.append(current_id)

            logger.debug(
                "confidence_propagated",
                belief_id=str(current_id),
                new_confidence=current.confidence,
                delta=change,
            )

            # Enqueue this belief's dependents for further propagation.
            for dep_id in current.dependent_beliefs:
                if dep_id not in visited and dep_id in belief_graph:
                    queue.append(dep_id)

        logger.info(
            "propagation_complete",
            source_belief=str(belief_id),
            affected_count=len(affected),
        )
        return affected

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _recompute_confidence(
        belief: BeliefNode,
        belief_graph: dict[UUID, BeliefNode],
    ) -> float:
        """Recompute confidence as the weighted average of supporting beliefs.

        For each supporting belief, we look at all justifications that
        reference it and use the justification's ``confidence_weight`` as
        the weight in the average.  If there are no supporters or the
        total weight is zero, confidence falls to 0.0.
        """
        if not belief.supporting_beliefs:
            # No supporters -- derive from justification weights alone.
            if belief.justifications:
                total_w = 0.0
                for j in belief.justifications:
                    total_w += j.confidence_weight
                return min(1.0, total_w / len(belief.justifications))
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0

        for supporter_id in belief.supporting_beliefs:
            supporter = belief_graph.get(supporter_id)
            if supporter is None:
                continue

            # Find the justification weight for this supporter.
            weight = _supporter_weight(belief, supporter_id)
            weighted_sum += supporter.confidence * weight
            total_weight += weight

        if total_weight == 0.0:
            return 0.0

        return weighted_sum / total_weight


def _supporter_weight(belief: BeliefNode, supporter_id: UUID) -> float:
    """Return the aggregate justification weight for a supporter.

    Looks through all justifications of *belief* and sums the
    ``confidence_weight`` of those whose ``supporting_facts`` contain
    *supporter_id*.  Falls back to 1.0 if no explicit match is found
    (defensive -- ensures propagation still works when the graph was
    wired without explicit supporting_facts entries).
    """
    total = 0.0
    found = False
    for j in belief.justifications:
        if supporter_id in j.supporting_facts:
            total += j.confidence_weight
            found = True
    return total if found else 1.0
