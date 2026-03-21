"""Bidding engine for agent task allocation.

Implements the BiddingProtocol with slot reservation, bid evaluation via a
weighted utility function, and fast-path routing for single-agent task types.
v3.3 D1: atomic cap of 5 concurrent bidders per work item.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from src.core.coordination import AgentBid, WorkItem

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Slot reservation
# ---------------------------------------------------------------------------

class BidSlotReservation:
    """Tracks slot reservations per work item (in-memory, no Redis).

    v3.3 D1: atomic cap enforced via a threading lock so concurrent
    ``reserve`` / ``release`` calls remain safe.
    """

    def __init__(self, cap: int = 5) -> None:
        self.cap = cap
        self._slots: dict[UUID, int] = defaultdict(int)
        self._lock = threading.Lock()

    def reserve(self, work_item_id: UUID) -> bool:
        """Try to reserve a slot. Returns True on success, False if at cap."""
        with self._lock:
            current = self._slots[work_item_id]
            if current >= self.cap:
                logger.info(
                    "slot_reservation.at_capacity",
                    work_item_id=str(work_item_id),
                    cap=self.cap,
                )
                return False
            self._slots[work_item_id] = current + 1
            logger.debug(
                "slot_reservation.reserved",
                work_item_id=str(work_item_id),
                count=current + 1,
            )
            return True

    def release(self, work_item_id: UUID) -> None:
        """Release one slot for a work item."""
        with self._lock:
            current = self._slots.get(work_item_id, 0)
            if current > 0:
                self._slots[work_item_id] = current - 1
                logger.debug(
                    "slot_reservation.released",
                    work_item_id=str(work_item_id),
                    count=current - 1,
                )

    def count(self, work_item_id: UUID) -> int:
        """Return the current slot count for a work item."""
        with self._lock:
            return self._slots.get(work_item_id, 0)


# ---------------------------------------------------------------------------
# Bid evaluator
# ---------------------------------------------------------------------------

class BidEvaluator:
    """Scores bids using a weighted utility function and returns the best."""

    # Weight vector (sums to 1.0)
    W_CAPABILITY: float = 0.35
    W_LOAD: float = 0.25
    W_RELIABILITY: float = 0.20
    W_INFO_GAIN: float = 0.10
    W_SPEED: float = 0.10

    def evaluate(self, bids: list[AgentBid]) -> AgentBid | None:
        """Return the highest-utility bid, or *None* if *bids* is empty."""
        if not bids:
            return None

        best_bid: AgentBid | None = None
        best_score: float = -1.0

        for bid in bids:
            score = (
                self.W_CAPABILITY * bid.capability_match
                + self.W_LOAD * (1.0 - bid.current_load)
                + self.W_RELIABILITY * bid.agent_reliability
                + self.W_INFO_GAIN * bid.expected_information_gain
                + self.W_SPEED * (1.0 / max(bid.estimated_time, 0.1))
            )
            logger.debug(
                "bid_evaluator.scored",
                agent_id=bid.agent_id,
                work_item_id=str(bid.work_item_id),
                utility=round(score, 6),
            )
            if score > best_score:
                best_score = score
                best_bid = bid

        if best_bid is not None:
            best_bid.utility_score = round(best_score, 6)
            logger.info(
                "bid_evaluator.winner",
                agent_id=best_bid.agent_id,
                work_item_id=str(best_bid.work_item_id),
                utility=best_bid.utility_score,
            )
        return best_bid


# ---------------------------------------------------------------------------
# Main protocol
# ---------------------------------------------------------------------------

class BiddingProtocol:
    """Coordinator that manages slot reservation, bid collection, evaluation,
    and fast-path routing for the multi-agent system."""

    _fast_path_map: dict[str, str] = {
        "repo_map": "repo_mapper",
        "law_check": "law_engine",
        "hypothesis_generate": "hypothesis",
        "causal_analysis": "causal_rca",
        "repair_plan": "repair_planner",
        "infra_ops": "infra_ops",
        "explain": "explainer",
        "execute_repair": "executor",
        "verify_repair": "verification",
    }

    def __init__(self, slot_cap: int = 5) -> None:
        self._slots = BidSlotReservation(cap=slot_cap)
        self._evaluator = BidEvaluator()
        self._bids: dict[UUID, list[AgentBid]] = defaultdict(list)
        logger.info("bidding_protocol.init", slot_cap=slot_cap)

    # -- slot management ----------------------------------------------------

    def reserve_slot(self, work_item_id: UUID) -> bool:
        """Reserve a bidding slot. Returns True if under cap, False otherwise."""
        return self._slots.reserve(work_item_id)

    def release_slot(self, work_item_id: UUID) -> None:
        """Release one bidding slot for *work_item_id*."""
        self._slots.release(work_item_id)

    # -- bid lifecycle ------------------------------------------------------

    def submit_bid(self, bid: AgentBid) -> bool:
        """Store a bid. Returns True if accepted (slot available)."""
        if not self.reserve_slot(bid.work_item_id):
            logger.warning(
                "bidding_protocol.bid_rejected",
                agent_id=bid.agent_id,
                work_item_id=str(bid.work_item_id),
                reason="at_capacity",
            )
            return False
        self._bids[bid.work_item_id].append(bid)
        logger.info(
            "bidding_protocol.bid_accepted",
            agent_id=bid.agent_id,
            work_item_id=str(bid.work_item_id),
        )
        return True

    def evaluate_bids(self, work_item_id: UUID) -> AgentBid | None:
        """Evaluate all stored bids for *work_item_id* and return the winner."""
        bids = self._bids.get(work_item_id, [])
        winner = self._evaluator.evaluate(bids)
        if winner is not None:
            logger.info(
                "bidding_protocol.winner_selected",
                agent_id=winner.agent_id,
                work_item_id=str(work_item_id),
                utility=winner.utility_score,
            )
        else:
            logger.info(
                "bidding_protocol.no_winner",
                work_item_id=str(work_item_id),
            )
        return winner

    def clear_bids(self, work_item_id: UUID) -> None:
        """Clear all bids and release slots for *work_item_id*."""
        count = len(self._bids.pop(work_item_id, []))
        # Reset slot counter to zero by releasing all held slots
        for _ in range(self._slots.count(work_item_id)):
            self._slots.release(work_item_id)
        logger.info(
            "bidding_protocol.bids_cleared",
            work_item_id=str(work_item_id),
            cleared=count,
        )

    # -- fast-path ----------------------------------------------------------

    def should_fast_path(self, work_item: WorkItem) -> str | None:
        """Return agent_id if *work_item.task_type* maps to exactly one agent.

        Fast-path bypasses the full bidding round when only a single agent is
        known to handle the task type.
        """
        agent_id = self._fast_path_map.get(work_item.task_type)
        if agent_id is not None:
            logger.info(
                "bidding_protocol.fast_path",
                task_type=work_item.task_type,
                agent_id=agent_id,
            )
        return agent_id
