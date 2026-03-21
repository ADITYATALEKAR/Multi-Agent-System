"""TMS engine for justification-based truth maintenance.

Manages beliefs, performs belief revision, confidence propagation, and
tracks which derived facts are currently believed based on their
justification support structure.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from uuid import UUID

import structlog

from src.core.derived import DerivedFact, ExtendedJustification
from src.tms.belief import BeliefNode
from src.tms.confidence import ConfidencePropagator
from src.tms.index import TMSIndex

logger = structlog.get_logger(__name__)


class TMSEngine:
    """Core truth maintenance engine.

    Responsibilities:
      - Registering new beliefs from derived facts.
      - Retracting support and propagating status/confidence changes.
      - Answering queries about belief status and consequences.

    All lookups are delegated to :class:`TMSIndex`; all confidence
    updates are delegated to :class:`ConfidencePropagator`.
    """

    def __init__(self) -> None:
        self._beliefs: dict[UUID, BeliefNode] = {}
        self._index: TMSIndex = TMSIndex()
        self._propagator: ConfidencePropagator = ConfidencePropagator()

    # ── public API ───────────────────────────────────────────────────

    def register_belief(
        self,
        derived: DerivedFact,
        justification: ExtendedJustification,
        tenant_id: str = "default",
    ) -> BeliefNode:
        """Register a new belief (or add a justification to an existing one).

        If a belief for ``derived.derived_id`` already exists, the new
        justification is added to it.  Otherwise a fresh :class:`BeliefNode`
        is created with status ``"IN"`` and the derived fact's confidence.

        Supporting beliefs are wired based on the justification's
        ``supporting_facts`` -- each supporting fact that already has a
        belief in the graph becomes a supporter of the new belief.

        Args:
            derived: The derived fact that the belief wraps.
            justification: The justification for the belief.
            tenant_id: Tenant isolation key (v3.3 A3).

        Returns:
            The created or updated :class:`BeliefNode`.
        """
        existing_bid = self._index.get_belief_for_fact(derived.derived_id)

        if existing_bid is not None and existing_bid in self._beliefs:
            # Existing belief -- add justification.
            belief = self._beliefs[existing_bid]
            belief.add_justification(justification)

            # Wire new supporting facts.
            self._wire_supporters(belief, justification)

            # Propagate confidence.
            self._propagator.propagate(
                belief.belief_id, derived.confidence, self._beliefs
            )

            logger.info(
                "justification_added_to_existing_belief",
                belief_id=str(belief.belief_id),
                justification_id=str(justification.justification_id),
            )
            return belief

        # New belief.
        belief = BeliefNode(
            derived_fact_id=derived.derived_id,
            tenant_id=tenant_id,
            status="IN",
            confidence=derived.confidence,
        )
        belief.add_justification(justification)

        # Store in graph.
        self._beliefs[belief.belief_id] = belief
        self._index.add_belief(belief)

        # Wire supporters.
        self._wire_supporters(belief, justification)

        # Propagate confidence from this new belief outward.
        self._propagator.propagate(
            belief.belief_id, belief.confidence, self._beliefs
        )

        logger.info(
            "belief_registered",
            belief_id=str(belief.belief_id),
            derived_fact_id=str(derived.derived_id),
            tenant_id=tenant_id,
            confidence=belief.confidence,
        )
        return belief

    def retract_support(self, fact_id: UUID) -> list[UUID]:
        """Retract a derived fact and propagate disbelief.

        Finds the belief associated with *fact_id*, removes all of its
        justifications, sets its status to ``"OUT"`` and confidence to
        0.0, then BFS-propagates through dependent beliefs.  A dependent
        belief transitions to ``"OUT"`` only if it has **no** remaining
        justifications with at least one ``"IN"`` supporter.

        Args:
            fact_id: The ``derived_id`` of the fact being retracted.

        Returns:
            List of belief IDs that transitioned (including the initial
            belief).
        """
        bid = self._index.get_belief_for_fact(fact_id)
        if bid is None:
            logger.warning("retract_unknown_fact", fact_id=str(fact_id))
            return []

        belief = self._beliefs.get(bid)
        if belief is None:
            return []

        transitioned: list[UUID] = []

        # Retract the root belief.
        if belief.status != "OUT":
            belief.status = "OUT"
            belief.confidence = 0.0
            belief.justifications.clear()
            belief.updated_at = datetime.utcnow()
            belief.status_change_count += 1
            transitioned.append(belief.belief_id)

        # BFS through dependents.
        queue: deque[UUID] = deque()
        for dep_id in self._index.get_dependents(belief.belief_id):
            queue.append(dep_id)

        visited: set[UUID] = {belief.belief_id}

        while queue:
            current_id = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)

            current = self._beliefs.get(current_id)
            if current is None:
                continue

            # Recompute: does this belief still have any IN supporters?
            still_supported = self._has_live_support(current)

            if not still_supported and current.status != "OUT":
                old_status = current.status
                current.status = "OUT"
                current.confidence = 0.0
                current.updated_at = datetime.utcnow()
                current.status_change_count += 1
                transitioned.append(current_id)

                logger.info(
                    "belief_retracted_cascade",
                    belief_id=str(current_id),
                    old_status=old_status,
                )

                # Enqueue this belief's dependents.
                for dep_id in self._index.get_dependents(current_id):
                    if dep_id not in visited:
                        queue.append(dep_id)
            elif still_supported:
                # Recompute confidence even if status stays IN.
                self._propagator.propagate(
                    current_id, current.confidence, self._beliefs
                )

        # Propagate the zero-confidence from retracted belief.
        self._propagator.propagate(bid, 0.0, self._beliefs)

        logger.info(
            "retraction_complete",
            fact_id=str(fact_id),
            transitioned_count=len(transitioned),
        )
        return transitioned

    def get_belief_status(self, belief_id: UUID) -> tuple[str, float]:
        """Return ``(status, confidence)`` for a belief.

        Args:
            belief_id: The UUID of the belief to query.

        Returns:
            A tuple of ``(status_string, confidence_float)``.

        Raises:
            KeyError: If the belief is not found.
        """
        belief = self._beliefs.get(belief_id)
        if belief is None:
            raise KeyError(f"Belief {belief_id} not found")
        return belief.status, belief.confidence

    def get_consequences(self, belief_id: UUID) -> set[UUID]:
        """Return the set of belief IDs that transitively depend on *belief_id*.

        Performs a BFS through the ``dependent_beliefs`` graph starting
        from *belief_id*.
        """
        result: set[UUID] = set()
        queue: deque[UUID] = deque()
        for dep_id in self._index.get_dependents(belief_id):
            queue.append(dep_id)

        while queue:
            current_id = queue.popleft()
            if current_id in result:
                continue
            result.add(current_id)
            for dep_id in self._index.get_dependents(current_id):
                if dep_id not in result:
                    queue.append(dep_id)

        return result

    def get_belief_for_fact(self, fact_id: UUID) -> BeliefNode | None:
        """Return the :class:`BeliefNode` for a derived fact, or ``None``."""
        bid = self._index.get_belief_for_fact(fact_id)
        if bid is None:
            return None
        return self._beliefs.get(bid)

    def get_all_beliefs(self, tenant_id: str = "default") -> list[BeliefNode]:
        """Return all beliefs for a given tenant.

        Args:
            tenant_id: The tenant to filter by.

        Returns:
            List of :class:`BeliefNode` instances for the tenant.
        """
        belief_ids = self._index.get_tenant_beliefs(tenant_id)
        return [
            self._beliefs[bid]
            for bid in belief_ids
            if bid in self._beliefs
        ]

    # ── internal helpers ─────────────────────────────────────────────

    def _wire_supporters(
        self, belief: BeliefNode, justification: ExtendedJustification
    ) -> None:
        """Wire supporter/dependent links based on justification.supporting_facts."""
        for sup_fact_id in justification.supporting_facts:
            sup_bid = self._index.get_belief_for_fact(sup_fact_id)
            if sup_bid is not None and sup_bid in self._beliefs:
                supporter = self._beliefs[sup_bid]
                # Update the belief objects.
                belief.supporting_beliefs.add(sup_bid)
                supporter.dependent_beliefs.add(belief.belief_id)
                # Update the index.
                self._index.add_dependency(sup_bid, belief.belief_id)

    def _has_live_support(self, belief: BeliefNode) -> bool:
        """Check if a belief has at least one justification with all IN supporters."""
        if not belief.justifications:
            return False

        for justification in belief.justifications:
            if not justification.supporting_facts:
                # A justification with no prerequisites is always live.
                return True

            all_in = True
            for sup_fact_id in justification.supporting_facts:
                sup_bid = self._index.get_belief_for_fact(sup_fact_id)
                if sup_bid is None:
                    all_in = False
                    break
                sup_belief = self._beliefs.get(sup_bid)
                if sup_belief is None or not sup_belief.is_in():
                    all_in = False
                    break

            if all_in:
                return True

        return False
