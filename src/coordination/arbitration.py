"""Arbitration logic for resolving contested work-item assignments."""

from __future__ import annotations

import random
from uuid import UUID

import structlog

from src.core.coordination import AgentBid, Claim

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# ConflictArbitrator
# ---------------------------------------------------------------------------


class ConflictArbitrator:
    """Resolves conflicts when multiple agents compete for the same work item.

    Selection priority (for ``arbitrate``):
        1. Highest ``utility_score``.
        2. On tie — highest reliability (looked up from *reliability_scores*,
           falling back to ``AgentBid.agent_reliability``).
        3. On further tie — highest ``capability_match``.
    """

    def __init__(self) -> None:
        self._log = log.bind(component="ConflictArbitrator")

    # -- public API ---------------------------------------------------------

    def arbitrate(
        self,
        bids: list[AgentBid],
        reliability_scores: dict[str, float] | None = None,
    ) -> AgentBid | None:
        """Pick the winning bid from a list of competing bids.

        Args:
            bids: Bids for the same work item.
            reliability_scores: Optional external reliability map keyed by
                ``agent_id``.  When provided these override the bid's own
                ``agent_reliability`` field.

        Returns:
            The winning :class:`AgentBid`, or ``None`` when *bids* is empty.
        """
        if not bids:
            self._log.debug("arbitrate.no_bids")
            return None

        reliability_scores = reliability_scores or {}

        def _sort_key(bid: AgentBid) -> tuple[float, float, float]:
            reliability = reliability_scores.get(
                bid.agent_id, bid.agent_reliability
            )
            return (bid.utility_score, reliability, bid.capability_match)

        winner = max(bids, key=_sort_key)
        self._log.info(
            "arbitrate.winner",
            agent_id=winner.agent_id,
            utility=winner.utility_score,
            n_bids=len(bids),
        )
        return winner

    def resolve_contested_claim(
        self, claims: list[Claim]
    ) -> Claim | None:
        """Resolve multiple claims on the same work item (first-come-first-served).

        Args:
            claims: Claims to adjudicate.

        Returns:
            The earliest :class:`Claim`, or ``None`` when *claims* is empty.
        """
        if not claims:
            self._log.debug("resolve_contested_claim.no_claims")
            return None

        winner = min(claims, key=lambda c: c.claimed_at)
        self._log.info(
            "resolve_contested_claim.winner",
            agent_id=winner.agent_id,
            claimed_at=str(winner.claimed_at),
            n_claims=len(claims),
        )
        return winner


# ---------------------------------------------------------------------------
# StalemateBreaker  (v3.3 C2)
# ---------------------------------------------------------------------------


class StalemateBreaker:
    """Breaks stalemates between competing hypotheses.

    A *stalemate* is detected when two or more bids have ``utility_score``
    values within ``STALEMATE_THRESHOLD`` (0.05) of each other.

    Breaking proceeds in escalating rounds per *work_item_id*:
        - Round 1 — highest ``capability_match``.
        - Round 2 — lowest ``current_load``.
        - Round 3 — random choice seeded deterministically from the work-item id.
    """

    STALEMATE_THRESHOLD: float = 0.05

    def __init__(self, max_rounds: int = 3) -> None:
        self._max_rounds = max_rounds
        self._round_count: dict[UUID, int] = {}
        self._log = log.bind(component="StalemateBreaker")

    # -- detection ----------------------------------------------------------

    def detect_stalemate(self, bids: list[AgentBid]) -> bool:
        """Return ``True`` when *bids* contain a stalemate.

        A stalemate exists when 2+ bids have utility scores within
        :pyattr:`STALEMATE_THRESHOLD` of each other.
        """
        if len(bids) < 2:
            return False

        sorted_bids = sorted(bids, key=lambda b: b.utility_score, reverse=True)
        for i in range(len(sorted_bids) - 1):
            diff = abs(sorted_bids[i].utility_score - sorted_bids[i + 1].utility_score)
            if diff <= self.STALEMATE_THRESHOLD:
                return True
        return False

    # -- breaking -----------------------------------------------------------

    def break_stalemate(
        self, bids: list[AgentBid], work_item_id: UUID
    ) -> AgentBid | None:
        """Break a stalemate by applying escalating tie-break strategies.

        Each successive call for the same *work_item_id* advances to the next
        round (up to ``max_rounds``).

        Returns:
            The winning :class:`AgentBid`, or ``None`` when *bids* is empty.
        """
        if not bids:
            return None

        current = self._round_count.get(work_item_id, 0) + 1
        if current > self._max_rounds:
            current = self._max_rounds
        self._round_count[work_item_id] = current

        self._log.info(
            "break_stalemate",
            work_item_id=str(work_item_id),
            round=current,
            n_bids=len(bids),
        )

        if current == 1:
            winner = max(bids, key=lambda b: b.capability_match)
        elif current == 2:
            winner = min(bids, key=lambda b: b.current_load)
        else:
            rng = random.Random(work_item_id.int)
            winner = rng.choice(bids)

        self._log.info(
            "break_stalemate.winner",
            agent_id=winner.agent_id,
            round=current,
        )
        return winner

    # -- reset --------------------------------------------------------------

    def reset(self, work_item_id: UUID) -> None:
        """Reset the round counter for *work_item_id*."""
        self._round_count.pop(work_item_id, None)
        self._log.debug("reset", work_item_id=str(work_item_id))
