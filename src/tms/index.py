"""TMS index for fast lookups across the belief graph.

Maintains four dictionaries that allow O(1) access to beliefs by
derived-fact ID, dependency direction, and tenant.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.tms.belief import BeliefNode

logger = structlog.get_logger(__name__)


class TMSIndex:
    """Fast-lookup index over the TMS belief graph.

    Indices maintained:
      - ``fact_to_belief``:       derived_fact_id  -> belief_id
      - ``belief_to_dependents``: belief_id        -> {dependent belief_ids}
      - ``belief_to_supporters``: belief_id        -> {supporting belief_ids}
      - ``tenant_beliefs``:       tenant_id        -> {belief_ids}
    """

    def __init__(self) -> None:
        self.fact_to_belief: dict[UUID, UUID] = {}
        self.belief_to_dependents: dict[UUID, set[UUID]] = {}
        self.belief_to_supporters: dict[UUID, set[UUID]] = {}
        self.tenant_beliefs: dict[str, set[UUID]] = {}

    # ── mutations ────────────────────────────────────────────────────

    def add_belief(self, belief: BeliefNode) -> None:
        """Register a belief in all indices.

        Args:
            belief: The BeliefNode to index.
        """
        bid = belief.belief_id

        # fact -> belief
        self.fact_to_belief[belief.derived_fact_id] = bid

        # dependency maps (initialise if absent)
        self.belief_to_dependents.setdefault(bid, set())
        self.belief_to_supporters.setdefault(bid, set())

        # wire supporting / dependent links
        for sup_id in belief.supporting_beliefs:
            self.belief_to_supporters.setdefault(bid, set()).add(sup_id)
            self.belief_to_dependents.setdefault(sup_id, set()).add(bid)

        for dep_id in belief.dependent_beliefs:
            self.belief_to_dependents.setdefault(bid, set()).add(dep_id)
            self.belief_to_supporters.setdefault(dep_id, set()).add(bid)

        # tenant index
        self.tenant_beliefs.setdefault(belief.tenant_id, set()).add(bid)

        logger.debug(
            "index_belief_added",
            belief_id=str(bid),
            derived_fact_id=str(belief.derived_fact_id),
            tenant_id=belief.tenant_id,
        )

    def remove_belief(self, belief: BeliefNode) -> None:
        """Remove a belief from all indices.

        Args:
            belief: The BeliefNode to deindex.
        """
        bid = belief.belief_id

        # fact -> belief
        self.fact_to_belief.pop(belief.derived_fact_id, None)

        # clean dependency maps
        for sup_id in list(self.belief_to_supporters.get(bid, set())):
            deps = self.belief_to_dependents.get(sup_id)
            if deps is not None:
                deps.discard(bid)

        for dep_id in list(self.belief_to_dependents.get(bid, set())):
            sups = self.belief_to_supporters.get(dep_id)
            if sups is not None:
                sups.discard(bid)

        self.belief_to_dependents.pop(bid, None)
        self.belief_to_supporters.pop(bid, None)

        # tenant index
        tenant_set = self.tenant_beliefs.get(belief.tenant_id)
        if tenant_set is not None:
            tenant_set.discard(bid)
            if not tenant_set:
                del self.tenant_beliefs[belief.tenant_id]

        logger.debug(
            "index_belief_removed",
            belief_id=str(bid),
            derived_fact_id=str(belief.derived_fact_id),
        )

    def add_dependency(self, supporter_id: UUID, dependent_id: UUID) -> None:
        """Record that *dependent_id* depends on *supporter_id*.

        Updates both the forward (dependents) and reverse (supporters) maps.
        """
        self.belief_to_dependents.setdefault(supporter_id, set()).add(dependent_id)
        self.belief_to_supporters.setdefault(dependent_id, set()).add(supporter_id)

    def remove_dependency(self, supporter_id: UUID, dependent_id: UUID) -> None:
        """Remove a dependency link between two beliefs."""
        deps = self.belief_to_dependents.get(supporter_id)
        if deps is not None:
            deps.discard(dependent_id)

        sups = self.belief_to_supporters.get(dependent_id)
        if sups is not None:
            sups.discard(supporter_id)

    # ── queries ──────────────────────────────────────────────────────

    def get_belief_for_fact(self, fact_id: UUID) -> UUID | None:
        """Return the belief_id for a given derived_fact_id, or ``None``."""
        return self.fact_to_belief.get(fact_id)

    def get_dependents(self, belief_id: UUID) -> set[UUID]:
        """Return all belief IDs that depend on *belief_id*."""
        return set(self.belief_to_dependents.get(belief_id, set()))

    def get_supporters(self, belief_id: UUID) -> set[UUID]:
        """Return all belief IDs that support *belief_id*."""
        return set(self.belief_to_supporters.get(belief_id, set()))

    def get_tenant_beliefs(self, tenant_id: str) -> set[UUID]:
        """Return all belief IDs belonging to *tenant_id*."""
        return set(self.tenant_beliefs.get(tenant_id, set()))
