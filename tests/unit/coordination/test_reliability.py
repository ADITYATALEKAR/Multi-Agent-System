from __future__ import annotations

from src.coordination.reliability import AgentReliabilityTracker


def test_default_reliability_is_1():
    """Unknown agent gets optimistic default of 1.0."""
    tracker = AgentReliabilityTracker()
    assert tracker.get_reliability("unknown_agent") == 1.0


def test_record_success_maintains_1():
    """A single success keeps reliability at 1.0."""
    tracker = AgentReliabilityTracker()
    tracker.record_success("agent_a")
    assert tracker.get_reliability("agent_a") == 1.0


def test_record_failure_lowers_reliability():
    """Recording a failure lowers reliability below 1.0."""
    tracker = AgentReliabilityTracker()
    tracker.record_success("agent_b")
    tracker.record_failure("agent_b")
    rel = tracker.get_reliability("agent_b")
    # base = 1/2 = 0.5, penalty = 0.1*0 = 0, reliability = 0.5
    assert rel == 0.5


def test_record_crash_harsh_penalty():
    """1 crash out of 2 total: base=0.5, penalty=0.1 -> 0.4."""
    tracker = AgentReliabilityTracker()
    tracker.record_success("agent_c")
    tracker.record_crash("agent_c")
    rel = tracker.get_reliability("agent_c")
    # successes=1, crashes=1, total=2
    # base = 1/2 = 0.5, penalty = 0.1*1 = 0.1, reliability = 0.5-0.1 = 0.4
    assert abs(rel - 0.4) < 1e-9


def test_get_record_creates_if_absent():
    """get_record creates a fresh record for a previously unseen agent."""
    tracker = AgentReliabilityTracker()
    record = tracker.get_record("new_agent")
    assert record.agent_id == "new_agent"
    assert record.successes == 0
    assert record.failures == 0
    assert record.crashes == 0
    assert record.reliability == 1.0
