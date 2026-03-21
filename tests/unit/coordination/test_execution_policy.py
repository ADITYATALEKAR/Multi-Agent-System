from __future__ import annotations

from uuid import uuid4

from src.coordination.execution_policy import ExecutionPolicy, FloorPolicy, Operation, RankingPolicy


# ── helpers ──────────────────────────────────────────────────────────────

def _op(op_type: str = "repo_map", priority: float = 0.5, cost: float = 0.1, mandatory: bool = False) -> Operation:
    return Operation(
        operation_type=op_type,
        agent_id="test_agent",
        work_item_id=uuid4(),
        estimated_cost=cost,
        priority=priority,
        mandatory=mandatory,
    )


# ── FloorPolicy.classify ────────────────────────────────────────────────

def test_classify_mandatory_types():
    """Operations with MANDATORY_TYPES are classified as mandatory."""
    policy = ExecutionPolicy()
    ops = [_op("law_check"), _op("heartbeat_check"), _op("repo_map")]
    mandatory, ranked = policy.classify_operations(ops)
    assert len(mandatory) == 2
    assert len(ranked) == 1
    assert all(o.operation_type in FloorPolicy.MANDATORY_TYPES for o in mandatory)


def test_classify_mandatory_flag():
    """Operations with mandatory=True are classified as mandatory regardless of type."""
    policy = ExecutionPolicy()
    ops = [_op("custom_op", mandatory=True), _op("normal_op")]
    mandatory, ranked = policy.classify_operations(ops)
    assert len(mandatory) == 1
    assert mandatory[0].operation_type == "custom_op"
    assert len(ranked) == 1


# ── RankingPolicy.rank ───────────────────────────────────────────────────

def test_ranking_by_priority_then_cost():
    """Ranked operations are sorted by priority desc, then cost asc."""
    ranker = RankingPolicy()
    op_a = _op("a", priority=0.5, cost=0.3)
    op_b = _op("b", priority=0.9, cost=0.2)
    op_c = _op("c", priority=0.5, cost=0.1)

    result = ranker.rank([op_a, op_b, op_c])
    assert result[0].operation_type == "b"   # highest priority
    assert result[1].operation_type == "c"   # same priority as a, but lower cost
    assert result[2].operation_type == "a"


# ── execute_with_floors ──────────────────────────────────────────────────

def test_execute_with_floors_mandatory_first():
    """Mandatory operations appear before ranked operations."""
    policy = ExecutionPolicy()
    m = _op("law_check", priority=0.3, cost=0.1)
    r = _op("repo_map", priority=0.9, cost=0.1)
    result = policy.execute_with_floors([m, r], total_budget=1.0)

    # Mandatory comes first in the list
    assert result[0].operation_type == "law_check"
    assert len(result) == 2


def test_floor_budget_cap_40pct():
    """Floor budget is capped at 40% of total budget."""
    policy = ExecutionPolicy()
    # Mandatory ops that cost more than 40% of the budget
    m1 = _op("law_check", cost=0.3)
    m2 = _op("heartbeat_check", cost=0.3)
    r1 = _op("repo_map", priority=0.9, cost=0.2)
    # total_budget=1.0, floor_cap=0.4
    # mandatory_total=0.6, floor_budget=min(0.6, 0.4)=0.4
    # m1 fits (0.3 <= 0.4), m2 doesn't (0.3+0.3=0.6 > 0.4)
    result = policy.execute_with_floors([m1, m2, r1], total_budget=1.0)

    mandatory_in_result = [o for o in result if o.operation_type in FloorPolicy.MANDATORY_TYPES]
    assert len(mandatory_in_result) == 1  # only m1 fits floor budget


# ── should_execute ───────────────────────────────────────────────────────

def test_should_execute_mandatory_always_true():
    policy = ExecutionPolicy()
    op = _op("law_check", priority=0.0)
    assert policy.should_execute(op) is True


# ── get_approval_level ───────────────────────────────────────────────────

def test_approval_level_auto_for_mandatory():
    policy = ExecutionPolicy()
    op = _op("security_verify")
    assert policy.get_approval_level(op) == "auto"


def test_approval_level_review_for_high_priority():
    policy = ExecutionPolicy()
    op = _op("repo_map", priority=0.8)
    assert policy.get_approval_level(op) == "review"


# ── floor_budget_consumed_pct ────────────────────────────────────────────

def test_floor_budget_consumed_pct():
    policy = ExecutionPolicy()
    ops = [_op("law_check", cost=0.2), _op("repo_map", cost=0.3)]
    # Only law_check is mandatory: 0.2 / 1.0 * 100 = 20.0%
    pct = policy.floor_budget_consumed_pct(ops, total_budget=1.0)
    assert abs(pct - 20.0) < 1e-9
