"""Phase 5 integration tests: coordination pipeline end-to-end."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from src.coordination.agents import (
    CausalRCAAgent,
    ExplainerAgent,
    ExecutorAgent,
    HypothesisAgent,
    InfraOpsAgent,
    LawEngineAgent,
    MemoryAgent,
    RepairPlannerAgent,
    RepoMapperAgent,
    VerificationAgent,
)
from src.coordination.agents.base import BaseAgent, HEARTBEAT_TIMEOUT_S
from src.coordination.bidding import BiddingProtocol
from src.coordination.blackboard import BlackboardManager, MAX_PENDING_CLAIMS, MAX_PENDING_QUESTIONS
from src.coordination.execution_policy import ExecutionPolicy, Operation
from src.coordination.multitenancy import (
    NamespaceIsolator,
    QuotaManager,
    TenantConfig,
    TenantRouter,
)
from src.coordination.orchestrator import (
    ESCALATION_TIMEOUT_S,
    Orchestrator,
    TRIAGE_ENTRY_PCT,
    TRIAGE_EXIT_PCT,
    TRIAGE_MIN_DWELL_S,
)
from src.coordination.reliability import AgentReliabilityTracker
from src.core.coordination import AgentBid, Claim, Question, WorkItem, WorkItemStatus
from src.self_improving.attention_regressor import AttentionRegressor, AttentionSample


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_agents() -> list[BaseAgent]:
    """Return a fresh instance of every specialist agent."""
    return [
        RepoMapperAgent(),
        LawEngineAgent(),
        HypothesisAgent(),
        CausalRCAAgent(),
        MemoryAgent(),
        RepairPlannerAgent(),
        VerificationAgent(),
        InfraOpsAgent(),
        ExplainerAgent(),
        ExecutorAgent(),
    ]


def _make_item(task_type: str = "repo_map", priority: float = 0.5,
               caps: set[str] | None = None) -> WorkItem:
    return WorkItem(
        task_type=task_type,
        priority=priority,
        required_capabilities=caps or set(),
    )


# ===========================================================================
# Orchestrator lifecycle
# ===========================================================================

class TestOrchestratorLifecycle:

    def test_orchestrator_register_agents(self) -> None:
        """Register all 10 agents, verify count."""
        orch = Orchestrator()
        agents = _all_agents()
        for a in agents:
            orch.register_agent(a)
        assert orch.agent_count == 10

    def test_orchestrator_submit_task(self) -> None:
        """Submit work items, verify task created."""
        orch = Orchestrator()
        for a in _all_agents():
            orch.register_agent(a)

        items = [_make_item("repo_map"), _make_item("law_check")]
        task_id = orch.submit_task(items)

        assert orch.task_count == 1
        ctx = orch.get_task(task_id)
        assert ctx is not None
        assert len(ctx.work_item_ids) == 2

    def test_orchestrator_run_cycle_fast_path(self) -> None:
        """Submit a repo_map task, run cycle, verify fast-path assigns to repo_mapper."""
        orch = Orchestrator()
        for a in _all_agents():
            orch.register_agent(a)

        items = [_make_item("repo_map", priority=0.8)]
        task_id = orch.submit_task(items)

        processed = orch.run_cycle()
        assert processed >= 1

        ctx = orch.get_task(task_id)
        item = orch._blackboard.get_work_item(ctx.work_item_ids[0])
        assert item.status == WorkItemStatus.COMPLETE

    def test_orchestrator_run_cycle_bidding(self) -> None:
        """Submit causal_analysis task, run cycle, verify assignment via bidding."""
        orch = Orchestrator()
        for a in _all_agents():
            orch.register_agent(a)

        items = [_make_item("causal_analysis", priority=0.7,
                            caps={"causal_analysis"})]
        task_id = orch.submit_task(items)

        processed = orch.run_cycle()
        assert processed >= 1

        ctx = orch.get_task(task_id)
        item = orch._blackboard.get_work_item(ctx.work_item_ids[0])
        assert item.status in (WorkItemStatus.COMPLETE, WorkItemStatus.FAILED)

    def test_orchestrator_check_termination(self) -> None:
        """Submit, run, verify termination."""
        orch = Orchestrator()
        for a in _all_agents():
            orch.register_agent(a)

        items = [_make_item("repo_map"), _make_item("explain")]
        task_id = orch.submit_task(items)

        for _ in range(10):
            orch.run_cycle()
            if orch.check_termination(task_id):
                break

        assert orch.check_termination(task_id)


# ===========================================================================
# Triage mode (v3.3 Fix 6)
# ===========================================================================

class TestTriageMode:

    def test_triage_entry_at_90pct(self) -> None:
        """Force capacity to 90%, verify triage entered."""
        orch = Orchestrator()
        # Post 90 items directly as IN_PROGRESS to simulate 90% capacity
        for _ in range(90):
            item = _make_item("repo_map")
            item.status = WorkItemStatus.IN_PROGRESS
            orch._blackboard.post_work_item(item)

        orch._evaluate_triage()
        assert orch.is_triage_mode()

    def test_triage_exit_at_60pct(self) -> None:
        """Verify stays in triage until capacity drops below 60%."""
        orch = Orchestrator()

        # Enter triage first
        orch.enter_triage_mode()
        # Set entered_at far in the past to satisfy dwell time
        orch._triage.entered_at = datetime.utcnow() - timedelta(seconds=TRIAGE_MIN_DWELL_S + 10)
        assert orch.is_triage_mode()

        # At 70% capacity -- should NOT exit (above 60% threshold)
        for _ in range(70):
            item = _make_item("repo_map")
            item.status = WorkItemStatus.IN_PROGRESS
            orch._blackboard.post_work_item(item)

        orch._evaluate_triage()
        assert orch.is_triage_mode(), "Should stay in triage at 70% capacity"

        # Clear items and bring capacity below 60%
        orch._blackboard._work_items.clear()
        orch._evaluate_triage()
        assert not orch.is_triage_mode(), "Should exit triage at 0% capacity"

    def test_triage_min_dwell_2min(self) -> None:
        """Verify triage does not exit before 2 minutes even if capacity drops."""
        orch = Orchestrator()
        orch.enter_triage_mode()
        # entered_at is just now -- dwell < 2 min
        assert orch.is_triage_mode()

        # Capacity is 0% (no items), but dwell time not met
        orch._evaluate_triage()
        assert orch.is_triage_mode(), "Must not exit triage before 2 minute dwell"


# ===========================================================================
# Heartbeat (v3.3 D3)
# ===========================================================================

class TestHeartbeat:

    def test_heartbeat_timeout(self) -> None:
        """Create agent, set last_heartbeat to >60s ago, verify is_alive returns False."""
        agent = RepoMapperAgent()
        agent._status.last_heartbeat = datetime.utcnow() - timedelta(
            seconds=HEARTBEAT_TIMEOUT_S + 5
        )
        assert not agent.is_alive()


# ===========================================================================
# Escalation (v3.3 D2)
# ===========================================================================

class TestEscalation:

    def test_escalation_timeout_30min(self) -> None:
        """Set item heartbeat to >30min ago, verify ABANDONED."""
        orch = Orchestrator()
        item = _make_item("repo_map")
        item.status = WorkItemStatus.CLAIMED
        item.claimed_by = "repo_mapper"
        item.last_heartbeat = datetime.utcnow() - timedelta(
            seconds=ESCALATION_TIMEOUT_S + 10
        )
        orch._blackboard.post_work_item(item)

        orch._check_escalation_timeouts()

        updated = orch._blackboard.get_work_item(item.item_id)
        assert updated.status == WorkItemStatus.ABANDONED


# ===========================================================================
# Blackboard integration
# ===========================================================================

class TestBlackboardIntegration:

    def test_blackboard_limits_integration(self) -> None:
        """Verify 200 claim / 100 question limits work with orchestrator."""
        bb = BlackboardManager()
        item = _make_item("repo_map")
        bb.post_work_item(item)

        # Post 200 claims (each with unique agent_id to avoid dedup)
        for i in range(MAX_PENDING_CLAIMS):
            claim = Claim(agent_id=f"agent_{i}", work_item_id=item.item_id)
            bb.post_claim(claim)

        assert bb.pending_claim_count == MAX_PENDING_CLAIMS

        # 201st claim should be rejected
        overflow = Claim(agent_id="overflow_agent", work_item_id=item.item_id)
        result = bb.post_claim(overflow)
        assert result is None

        # Post 100 questions
        for i in range(MAX_PENDING_QUESTIONS):
            q = Question(asked_by=f"agent_{i}", question_type="clarify")
            bb.post_question(q)

        assert bb.pending_question_count == MAX_PENDING_QUESTIONS

        # 101st question should be rejected
        overflow_q = Question(asked_by="overflow", question_type="clarify")
        result_q = bb.post_question(overflow_q)
        assert result_q is None

    def test_claim_dedup_through_orchestrator(self) -> None:
        """Verify claim deduplication: same agent + same item = rejected."""
        bb = BlackboardManager()
        item = _make_item("repo_map")
        bb.post_work_item(item)

        c1 = Claim(agent_id="agent_x", work_item_id=item.item_id)
        c2 = Claim(agent_id="agent_x", work_item_id=item.item_id)

        r1 = bb.post_claim(c1)
        r2 = bb.post_claim(c2)

        assert r1 is not None
        assert r2 is None  # deduped
        assert bb.pending_claim_count == 1


# ===========================================================================
# Bidding integration
# ===========================================================================

class TestBiddingIntegration:

    def test_slot_reservation_cap_5(self) -> None:
        """Verify max 5 bidders per item."""
        bp = BiddingProtocol(slot_cap=5)
        item_id = uuid4()

        for i in range(5):
            assert bp.reserve_slot(item_id), f"Slot {i} should succeed"

        assert not bp.reserve_slot(item_id), "6th slot should fail"

    def test_bid_evaluation_selects_best(self) -> None:
        """Verify bid evaluator picks the highest-utility bid."""
        bp = BiddingProtocol()
        item_id = uuid4()

        bid_low = AgentBid(
            agent_id="agent_low",
            work_item_id=item_id,
            capability_match=0.3,
            estimated_time=5.0,
            agent_reliability=0.5,
        )
        bid_high = AgentBid(
            agent_id="agent_high",
            work_item_id=item_id,
            capability_match=0.95,
            estimated_time=1.0,
            agent_reliability=0.95,
        )

        bp.submit_bid(bid_low)
        bp.submit_bid(bid_high)

        winner = bp.evaluate_bids(item_id)
        assert winner is not None
        assert winner.agent_id == "agent_high"


# ===========================================================================
# Execution policy integration
# ===========================================================================

class TestExecutionPolicyIntegration:

    def test_mandatory_ops_always_execute(self) -> None:
        """Create mix of mandatory/ranked ops, verify mandatory execute first."""
        policy = ExecutionPolicy()
        item_id = uuid4()

        mandatory_op = Operation(
            operation_type="law_check",
            agent_id="law_engine",
            work_item_id=item_id,
            estimated_cost=0.1,
            priority=0.2,
            mandatory=True,
        )
        ranked_op = Operation(
            operation_type="hypothesis_generate",
            agent_id="hypothesis",
            work_item_id=item_id,
            estimated_cost=0.2,
            priority=0.9,
        )

        result = policy.execute_with_floors(
            [ranked_op, mandatory_op], total_budget=1.0
        )

        # Mandatory op first
        assert result[0].operation_type == "law_check"
        assert len(result) == 2

    def test_floor_budget_40pct_cap(self) -> None:
        """Verify floor budget does not exceed 40% of total budget."""
        policy = ExecutionPolicy()
        item_id = uuid4()

        # Create expensive mandatory ops totalling 0.6
        ops = [
            Operation(
                operation_type="law_check",
                agent_id="law_engine",
                work_item_id=item_id,
                estimated_cost=0.3,
                mandatory=True,
            ),
            Operation(
                operation_type="heartbeat_check",
                agent_id="orchestrator",
                work_item_id=item_id,
                estimated_cost=0.3,
                mandatory=True,
            ),
        ]

        result = policy.execute_with_floors(ops, total_budget=1.0)
        # Floor budget = min(0.6, 0.40) = 0.40
        # Only the first mandatory op (0.3) fits within 0.40
        mandatory_in_result = [o for o in result if o.mandatory]
        total_mandatory_cost = sum(o.estimated_cost for o in mandatory_in_result)
        assert total_mandatory_cost <= 0.40 + 0.001


# ===========================================================================
# Multi-tenancy integration
# ===========================================================================

class TestMultiTenancyIntegration:

    def test_tenant_isolation(self) -> None:
        """Register 3 tenants, register items for each, verify cross-tenant access blocked."""
        router = TenantRouter()
        for tid in ("alpha", "beta", "gamma"):
            router.register_tenant(TenantConfig(tenant_id=tid, display_name=tid))

        isolator = NamespaceIsolator(router)

        items_alpha = [uuid4() for _ in range(3)]
        items_beta = [uuid4() for _ in range(2)]

        for iid in items_alpha:
            isolator.register_item("alpha", iid)
        for iid in items_beta:
            isolator.register_item("beta", iid)

        # alpha can access its own items
        for iid in items_alpha:
            assert isolator.validate_access("alpha", "alpha")
            assert isolator.get_owner(iid) == "alpha"

        # alpha CANNOT access beta's items
        assert not isolator.validate_access("alpha", "beta")

        # gamma has no items registered
        assert isolator.get_owner(uuid4()) is None

    def test_quota_enforcement(self) -> None:
        """Consume quota to limit, verify next consume fails."""
        router = TenantRouter()
        router.register_tenant(TenantConfig(
            tenant_id="limited",
            max_concurrent_items=5,
        ))

        quota = QuotaManager(router)

        # Consume 5 items
        for _ in range(5):
            assert quota.consume("limited", "items")

        # 6th should fail
        assert not quota.consume("limited", "items")

        # Release one
        quota.release("limited", "items")
        assert quota.consume("limited", "items")


# ===========================================================================
# Reliability tracking
# ===========================================================================

class TestReliabilityTracking:

    def test_reliability_degrades_on_failure(self) -> None:
        """Reliability should decrease after failures."""
        tracker = AgentReliabilityTracker()
        assert tracker.get_reliability("agent_x") == 1.0

        tracker.record_success("agent_x")
        tracker.record_failure("agent_x")

        rel = tracker.get_reliability("agent_x")
        assert rel < 1.0

    def test_crash_penalty_harsh(self) -> None:
        """Crashes should carry harsher penalty than failures."""
        tracker = AgentReliabilityTracker()

        # Agent with 1 failure
        tracker.record_success("fail_agent")
        tracker.record_failure("fail_agent")
        rel_fail = tracker.get_reliability("fail_agent")

        # Agent with 1 crash (same number of successes)
        tracker.record_success("crash_agent")
        tracker.record_crash("crash_agent")
        rel_crash = tracker.get_reliability("crash_agent")

        # Crash penalty is harsher: 0.1 * crashes subtracted
        assert rel_crash < rel_fail


# ===========================================================================
# Attention regressor integration
# ===========================================================================

class TestAttentionRegressorIntegration:

    def test_fit_and_predict_workflow(self) -> None:
        """Add samples, fit, predict, verify result."""
        regressor = AttentionRegressor(learning_rate=0.01, min_samples=5)

        # Add 15 samples with two features
        for i in range(15):
            sample = AttentionSample(
                node_id=uuid4(),
                features={"complexity": 0.5 + i * 0.01, "centrality": 0.3 + i * 0.02},
                attention_weight=0.0,
                outcome_score=0.4 + i * 0.03,
                timestamp=datetime.utcnow(),
            )
            regressor.add_sample(sample)

        assert regressor.sample_count == 15
        assert not regressor.is_fitted

        result = regressor.fit()
        assert result is not None
        assert result.samples_used == 15
        assert regressor.is_fitted

        # Predict
        pred = regressor.predict({"complexity": 0.6, "centrality": 0.5})
        assert 0.0 <= pred <= 1.0
