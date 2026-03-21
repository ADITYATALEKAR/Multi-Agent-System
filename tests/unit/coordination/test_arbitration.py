from __future__ import annotations

from uuid import uuid4

from src.coordination.arbitration import ConflictArbitrator, StalemateBreaker
from src.core.coordination import AgentBid, Claim


# ── helpers ──────────────────────────────────────────────────────────────

def _make_bid(agent_id: str, utility: float = 0.5, **overrides) -> AgentBid:
    defaults = dict(
        agent_id=agent_id,
        work_item_id=uuid4(),
        capability_match=0.8,
        estimated_time=1.0,
        current_load=0.3,
        agent_reliability=0.9,
        expected_information_gain=0.4,
        utility_score=utility,
    )
    defaults.update(overrides)
    return AgentBid(**defaults)


# ── ConflictArbitrator ───────────────────────────────────────────────────

def test_arbitrate_picks_highest_utility():
    arb = ConflictArbitrator()
    bid_a = _make_bid("a", utility=0.7)
    bid_b = _make_bid("b", utility=0.9)
    winner = arb.arbitrate([bid_a, bid_b])
    assert winner is not None
    assert winner.agent_id == "b"


def test_arbitrate_empty_returns_none():
    arb = ConflictArbitrator()
    assert arb.arbitrate([]) is None


def test_resolve_contested_claim_first_come():
    """First-come-first-served: earliest claimed_at wins."""
    arb = ConflictArbitrator()
    wid = uuid4()
    from datetime import datetime, timedelta

    early = Claim(agent_id="early", work_item_id=wid, claimed_at=datetime(2025, 1, 1, 0, 0, 0))
    late = Claim(agent_id="late", work_item_id=wid, claimed_at=datetime(2025, 1, 1, 0, 0, 5))

    winner = arb.resolve_contested_claim([early, late])
    assert winner is not None
    assert winner.agent_id == "early"


# ── StalemateBreaker ────────────────────────────────────────────────────

def test_detect_stalemate_within_threshold():
    """Two bids within 0.05 of each other are a stalemate."""
    sb = StalemateBreaker()
    wid = uuid4()
    bid_a = _make_bid("a", utility=0.80, work_item_id=wid)
    bid_b = _make_bid("b", utility=0.82, work_item_id=wid)
    assert sb.detect_stalemate([bid_a, bid_b]) is True


def test_detect_stalemate_no_stalemate():
    """Two bids far apart are not a stalemate."""
    sb = StalemateBreaker()
    bid_a = _make_bid("a", utility=0.3)
    bid_b = _make_bid("b", utility=0.9)
    assert sb.detect_stalemate([bid_a, bid_b]) is False


def test_break_stalemate_round1_capability():
    """Round 1 picks highest capability_match."""
    sb = StalemateBreaker()
    wid = uuid4()
    bid_a = _make_bid("a", utility=0.8, work_item_id=wid, capability_match=0.7)
    bid_b = _make_bid("b", utility=0.8, work_item_id=wid, capability_match=0.95)
    winner = sb.break_stalemate([bid_a, bid_b], wid)
    assert winner is not None
    assert winner.agent_id == "b"


def test_break_stalemate_round2_load():
    """Round 2 picks lowest current_load."""
    sb = StalemateBreaker()
    wid = uuid4()
    bid_a = _make_bid("a", utility=0.8, work_item_id=wid, current_load=0.1)
    bid_b = _make_bid("b", utility=0.8, work_item_id=wid, current_load=0.9)
    # First call -> round 1
    sb.break_stalemate([bid_a, bid_b], wid)
    # Second call -> round 2 (lowest load)
    winner = sb.break_stalemate([bid_a, bid_b], wid)
    assert winner is not None
    assert winner.agent_id == "a"


def test_break_stalemate_round3_random_deterministic():
    """Round 3 uses deterministic random based on work_item_id."""
    sb = StalemateBreaker()
    wid = uuid4()
    bid_a = _make_bid("a", utility=0.8, work_item_id=wid)
    bid_b = _make_bid("b", utility=0.8, work_item_id=wid)
    # Advance to round 3
    sb.break_stalemate([bid_a, bid_b], wid)  # round 1
    sb.break_stalemate([bid_a, bid_b], wid)  # round 2
    winner1 = sb.break_stalemate([bid_a, bid_b], wid)  # round 3

    # Reset and replay to confirm determinism
    sb.reset(wid)
    sb.break_stalemate([bid_a, bid_b], wid)  # round 1
    sb.break_stalemate([bid_a, bid_b], wid)  # round 2
    winner2 = sb.break_stalemate([bid_a, bid_b], wid)  # round 3

    assert winner1 is not None
    assert winner2 is not None
    assert winner1.agent_id == winner2.agent_id


def test_reset_round_counter():
    """reset() clears the round counter for a work_item_id."""
    sb = StalemateBreaker()
    wid = uuid4()
    bid_a = _make_bid("a", utility=0.8, work_item_id=wid, capability_match=0.95)
    bid_b = _make_bid("b", utility=0.8, work_item_id=wid, capability_match=0.7)

    # Round 1 -> capability
    first = sb.break_stalemate([bid_a, bid_b], wid)
    assert first is not None
    assert first.agent_id == "a"

    sb.reset(wid)

    # After reset, next call is round 1 again -> capability
    second = sb.break_stalemate([bid_a, bid_b], wid)
    assert second is not None
    assert second.agent_id == "a"
