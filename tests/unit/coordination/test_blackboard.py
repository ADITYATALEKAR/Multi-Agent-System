from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.coordination.blackboard import BlackboardManager, MAX_PENDING_CLAIMS, MAX_PENDING_QUESTIONS
from src.core.coordination import Claim, Question, WorkItem, WorkItemStatus


# ── helpers ──────────────────────────────────────────────────────────────

def _make_item(**kwargs) -> WorkItem:
    defaults = {"task_type": "repo_map", "priority": 0.8}
    defaults.update(kwargs)
    return WorkItem(**defaults)


def _make_claim(agent_id: str, work_item_id) -> Claim:
    return Claim(agent_id=agent_id, work_item_id=work_item_id)


# ── tests ────────────────────────────────────────────────────────────────

def test_post_and_get_work_item():
    bb = BlackboardManager()
    item = _make_item()
    returned_id = bb.post_work_item(item)
    assert returned_id == item.item_id
    assert bb.get_work_item(item.item_id) is item


def test_get_open_items_filters_by_status():
    bb = BlackboardManager()
    open_item = _make_item(priority=0.5)
    claimed_item = _make_item(priority=0.9, status=WorkItemStatus.CLAIMED)
    bb.post_work_item(open_item)
    bb.post_work_item(claimed_item)

    open_items = bb.get_open_items()
    ids = [i.item_id for i in open_items]
    assert open_item.item_id in ids
    assert claimed_item.item_id not in ids


def test_get_open_items_filters_by_capabilities():
    bb = BlackboardManager()
    item_a = _make_item(required_capabilities={"repo_map"})
    item_b = _make_item(required_capabilities={"law_check"})
    item_c = _make_item()  # no required caps -- always included
    bb.post_work_item(item_a)
    bb.post_work_item(item_b)
    bb.post_work_item(item_c)

    filtered = bb.get_open_items(capabilities={"repo_map"})
    ids = {i.item_id for i in filtered}
    assert item_a.item_id in ids
    assert item_c.item_id in ids
    assert item_b.item_id not in ids


def test_claim_work_item():
    bb = BlackboardManager()
    item = _make_item()
    bb.post_work_item(item)

    result = bb.claim_work_item(item.item_id, "agent_1")
    assert result is True

    updated = bb.get_work_item(item.item_id)
    assert updated is not None
    assert updated.status == WorkItemStatus.CLAIMED
    assert updated.claimed_by == "agent_1"


def test_claim_dedup():
    """v3.3 Fix 6: same agent + same item second claim is rejected."""
    bb = BlackboardManager()
    item = _make_item()
    bb.post_work_item(item)

    first = bb.claim_work_item(item.item_id, "agent_1")
    assert first is True

    # Post a second claim manually (item is already CLAIMED so claim_work_item
    # would fail on status check; test the dedup path directly via post_claim).
    dup_claim = _make_claim("agent_1", item.item_id)
    result = bb.post_claim(dup_claim)
    assert result is None  # deduplicated


def test_claim_limit_200():
    """Posting the 201st claim returns None."""
    bb = BlackboardManager()
    work_item_ids = []
    for i in range(MAX_PENDING_CLAIMS + 1):
        item = _make_item()
        bb.post_work_item(item)
        work_item_ids.append(item.item_id)

    for i in range(MAX_PENDING_CLAIMS):
        claim = _make_claim(f"agent_{i}", work_item_ids[i])
        result = bb.post_claim(claim)
        assert result is not None, f"Claim {i} should succeed"

    overflow_claim = _make_claim("agent_overflow", work_item_ids[MAX_PENDING_CLAIMS])
    assert bb.post_claim(overflow_claim) is None


def test_question_limit_100():
    """Posting the 101st unresolved question returns None."""
    bb = BlackboardManager()
    for i in range(MAX_PENDING_QUESTIONS):
        q = Question(asked_by=f"agent_{i}", question_type="why")
        result = bb.post_question(q)
        assert result is not None, f"Question {i} should succeed"

    overflow_q = Question(asked_by="agent_overflow", question_type="why")
    assert bb.post_question(overflow_q) is None


def test_complete_work_item():
    bb = BlackboardManager()
    item = _make_item()
    bb.post_work_item(item)
    bb.claim_work_item(item.item_id, "agent_1")
    bb.complete_work_item(item.item_id, result={"ok": True})

    updated = bb.get_work_item(item.item_id)
    assert updated is not None
    assert updated.status == WorkItemStatus.COMPLETE
    assert updated.result == {"ok": True}
    # Claims for this item should be cleaned up
    assert bb.get_claims_for_item(item.item_id) == []


def test_fail_work_item_retries():
    """Failing an item with attempts remaining resets to OPEN."""
    bb = BlackboardManager()
    item = _make_item()
    bb.post_work_item(item)
    bb.claim_work_item(item.item_id, "agent_1")

    bb.fail_work_item(item.item_id)
    updated = bb.get_work_item(item.item_id)
    assert updated is not None
    assert updated.status == WorkItemStatus.OPEN
    assert updated.attempt_count == 1
    assert updated.claimed_by is None


def test_abandon_work_item():
    """v3.3 D2: abandon sets ABANDONED status and clears claimed_by."""
    bb = BlackboardManager()
    item = _make_item()
    bb.post_work_item(item)
    bb.claim_work_item(item.item_id, "agent_1")

    bb.abandon_work_item(item.item_id)
    updated = bb.get_work_item(item.item_id)
    assert updated is not None
    assert updated.status == WorkItemStatus.ABANDONED
    assert updated.claimed_by is None


def test_cleanup_stale():
    """Items with old heartbeat are released back to OPEN."""
    bb = BlackboardManager()
    item = _make_item()
    bb.post_work_item(item)
    bb.claim_work_item(item.item_id, "agent_1")

    # Force the heartbeat to be old (>60s)
    stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
    bb.update_item(item.item_id, {"last_heartbeat": stale_time})

    cleaned = bb.cleanup_stale()
    assert cleaned >= 1

    updated = bb.get_work_item(item.item_id)
    assert updated is not None
    assert updated.status == WorkItemStatus.OPEN
    assert updated.claimed_by is None


def test_get_items_by_incident():
    """v3.3 A4: retrieve items by incident_id."""
    bb = BlackboardManager()
    incident = uuid4()
    item_a = _make_item(incident_id=incident)
    item_b = _make_item(incident_id=incident)
    item_c = _make_item()  # different incident
    bb.post_work_item(item_a)
    bb.post_work_item(item_b)
    bb.post_work_item(item_c)

    results = bb.get_items_by_incident(incident)
    ids = {i.item_id for i in results}
    assert item_a.item_id in ids
    assert item_b.item_id in ids
    assert item_c.item_id not in ids
