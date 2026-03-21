from __future__ import annotations

from uuid import uuid4

from src.coordination.bidding import BidEvaluator, BiddingProtocol
from src.core.coordination import AgentBid, WorkItem


# ── helpers ──────────────────────────────────────────────────────────────

def _make_bid(agent_id: str = "test_agent", work_item_id=None, **overrides) -> AgentBid:
    defaults = dict(
        agent_id=agent_id,
        work_item_id=work_item_id or uuid4(),
        capability_match=0.9,
        estimated_time=1.0,
        current_load=0.2,
        agent_reliability=0.95,
        expected_information_gain=0.5,
    )
    defaults.update(overrides)
    return AgentBid(**defaults)


def _make_item(**kwargs) -> WorkItem:
    defaults = {"task_type": "repo_map", "priority": 0.8}
    defaults.update(kwargs)
    return WorkItem(**defaults)


# ── slot reservation ─────────────────────────────────────────────────────

def test_slot_reservation_under_cap():
    """Reserving a slot under the cap (5) succeeds."""
    bp = BiddingProtocol(slot_cap=5)
    wid = uuid4()
    assert bp.reserve_slot(wid) is True


def test_slot_reservation_at_cap():
    """6th slot reservation returns False."""
    bp = BiddingProtocol(slot_cap=5)
    wid = uuid4()
    for _ in range(5):
        assert bp.reserve_slot(wid) is True
    assert bp.reserve_slot(wid) is False


# ── bid lifecycle ────────────────────────────────────────────────────────

def test_submit_bid_accepted():
    bp = BiddingProtocol(slot_cap=5)
    bid = _make_bid()
    assert bp.submit_bid(bid) is True


def test_evaluate_bids_picks_highest_utility():
    bp = BiddingProtocol(slot_cap=5)
    wid = uuid4()
    bid_low = _make_bid("low_agent", wid, capability_match=0.1, agent_reliability=0.5)
    bid_high = _make_bid("high_agent", wid, capability_match=0.9, agent_reliability=0.99)
    bp.submit_bid(bid_low)
    bp.submit_bid(bid_high)

    winner = bp.evaluate_bids(wid)
    assert winner is not None
    assert winner.agent_id == "high_agent"


# ── fast path ────────────────────────────────────────────────────────────

def test_fast_path_known_type():
    """repo_map task type fast-paths to repo_mapper agent."""
    bp = BiddingProtocol()
    item = _make_item(task_type="repo_map")
    assert bp.should_fast_path(item) == "repo_mapper"


def test_fast_path_unknown_type():
    """Unknown task type returns None (no fast path)."""
    bp = BiddingProtocol()
    item = _make_item(task_type="custom")
    assert bp.should_fast_path(item) is None


# ── clear bids ───────────────────────────────────────────────────────────

def test_clear_bids():
    bp = BiddingProtocol(slot_cap=5)
    wid = uuid4()
    bid = _make_bid("agent_1", wid)
    bp.submit_bid(bid)

    bp.clear_bids(wid)
    # After clearing, evaluating should return None (no bids)
    assert bp.evaluate_bids(wid) is None


# ── utility formula ──────────────────────────────────────────────────────

def test_bid_evaluator_utility_formula():
    """Manually verify the BidEvaluator utility formula."""
    evaluator = BidEvaluator()
    bid = _make_bid(
        capability_match=0.9,
        estimated_time=1.0,
        current_load=0.2,
        agent_reliability=0.95,
        expected_information_gain=0.5,
    )

    # W_CAPABILITY=0.35, W_LOAD=0.25, W_RELIABILITY=0.20, W_INFO_GAIN=0.10, W_SPEED=0.10
    expected = (
        0.35 * 0.9             # capability_match
        + 0.25 * (1.0 - 0.2)  # 1 - current_load
        + 0.20 * 0.95          # agent_reliability
        + 0.10 * 0.5           # expected_information_gain
        + 0.10 * (1.0 / max(1.0, 0.1))  # speed = 1/estimated_time
    )

    result = evaluator.evaluate([bid])
    assert result is not None
    assert abs(result.utility_score - round(expected, 6)) < 1e-5
