"""v3.3 F6 GATE: Chaos test suite -- failure simulation and recovery verification."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from src.coordination.orchestrator import (
    ESCALATION_TIMEOUT_S,
    Orchestrator,
    TRIAGE_ENTRY_PCT,
    TRIAGE_EXIT_PCT,
    TRIAGE_MIN_DWELL_S,
)
from src.coordination.blackboard import BlackboardManager, MAX_PENDING_CLAIMS
from src.coordination.agents import (
    RepoMapperAgent,
    LawEngineAgent,
    HypothesisAgent,
    CausalRCAAgent,
    ExecutorAgent as CoordExecutorAgent,
)
from src.coordination.agents.base import BaseAgent, HEARTBEAT_TIMEOUT_S
from src.coordination.reliability import AgentReliabilityTracker
from src.coordination.multitenancy import (
    NamespaceIsolator,
    TenantConfig,
    TenantRouter,
)
from src.core.coordination import Claim, WorkItem, WorkItemStatus
from src.executor.agent import ExecutorAgent
from src.executor.adapters import ExecutionResult, ExecutionStatus
from src.policy.engine import PolicyEngine
from src.repair.planner import RepairAction, RepairActionType, RepairTrajectory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_agents() -> list[BaseAgent]:
    return [
        RepoMapperAgent(),
        LawEngineAgent(),
        HypothesisAgent(),
        CausalRCAAgent(),
        CoordExecutorAgent(),
    ]


def _make_item(task_type: str = "repo_map",
               priority: float = 0.5,
               caps: set[str] | None = None) -> WorkItem:
    return WorkItem(
        task_type=task_type,
        priority=priority,
        required_capabilities=caps or set(),
    )


# ===========================================================================
# Chaos: Agent crash recovery
# ===========================================================================

class TestChaosAgentCrashRecovery:

    def test_chaos_agent_crash_recovery(self) -> None:
        """Simulate agent crash -> orchestrator detects timeout -> reassigns item."""
        start = time.perf_counter()

        # 1. Create orchestrator with agents
        orch = Orchestrator()
        mapper = RepoMapperAgent()
        orch.register_agent(mapper)

        # 2. Submit work item
        item = _make_item("repo_map", priority=0.8)
        task_id = orch.submit_task([item])

        ctx = orch.get_task(task_id)
        item_id = ctx.work_item_ids[0]

        # Claim the item manually so we can simulate crash on it
        orch._blackboard.update_item(item_id, {
            "status": WorkItemStatus.IN_PROGRESS,
            "claimed_by": mapper.agent_id,
            "last_heartbeat": datetime.utcnow(),
        })

        # 3. Simulate agent crash: stop agent, set heartbeat >60s ago
        mapper.stop()
        mapper._status.last_heartbeat = datetime.utcnow() - timedelta(
            seconds=HEARTBEAT_TIMEOUT_S + 10
        )
        assert not mapper.is_alive()

        # 4. Force heartbeat check (bypass poll interval)
        orch._last_heartbeat_check = datetime.utcnow() - timedelta(seconds=60)
        orch._check_heartbeats()

        # Item should be released back to OPEN
        released_item = orch._blackboard.get_work_item(item_id)
        assert released_item.status == WorkItemStatus.OPEN

        # 5. Register replacement agent
        orch.unregister_agent(mapper.agent_id)
        replacement = RepoMapperAgent(agent_id="repo_mapper_v2")
        orch.register_agent(replacement)

        # 6. Run another cycle (item should be reassigned)
        orch.run_cycle()

        final_item = orch._blackboard.get_work_item(item_id)
        assert final_item.status in (
            WorkItemStatus.COMPLETE,
            WorkItemStatus.IN_PROGRESS,
            WorkItemStatus.CLAIMED,
        )

        # 7. Verify recovery time < 120s
        elapsed = time.perf_counter() - start
        assert elapsed < 120.0, f"Recovery took {elapsed:.1f}s (max 120s)"


# ===========================================================================
# Chaos: Blackboard bloat recovery
# ===========================================================================

class TestChaosBlackboardBloat:

    def test_chaos_blackboard_bloat_recovery(self) -> None:
        """Flood blackboard beyond 200 claims, verify cleanup recovers."""
        start = time.perf_counter()

        bb = BlackboardManager()

        # Post a single work item that all claims reference
        item = _make_item("repo_map")
        bb.post_work_item(item)

        # Flood with 250 claims (each with unique agent to bypass dedup)
        posted_count = 0
        for i in range(250):
            claim = Claim(agent_id=f"flood_agent_{i}", work_item_id=item.item_id)
            result = bb.post_claim(claim)
            if result is not None:
                posted_count += 1

        # The hard limit prevents posting beyond 200
        assert bb.pending_claim_count <= MAX_PENDING_CLAIMS

        # Trigger cleanup to evict excess (if any got through)
        cleaned = bb.cleanup_stale()

        # Verify claims are within bounds
        assert bb.pending_claim_count <= MAX_PENDING_CLAIMS

        elapsed = time.perf_counter() - start
        assert elapsed < 120.0, f"Recovery took {elapsed:.1f}s (max 120s)"


# ===========================================================================
# Chaos: Triage mode stability (no flapping)
# ===========================================================================

class TestChaosTriageModeStability:

    def test_chaos_triage_mode_stability(self) -> None:
        """Verify triage mode hysteresis prevents flapping."""
        orch = Orchestrator()

        # 1. Force capacity to 95% -> verify triage entered
        for _ in range(95):
            it = _make_item("repo_map")
            it.status = WorkItemStatus.IN_PROGRESS
            orch._blackboard.post_work_item(it)

        orch._evaluate_triage()
        assert orch.is_triage_mode(), "Should enter triage at 95% capacity"

        # 2. Reduce to 65% but within 2min -> verify still in triage (hysteresis)
        #    Clear some items to bring capacity down to ~65
        items = orch._blackboard.get_items_by_status(WorkItemStatus.IN_PROGRESS)
        for it in items[65:]:
            orch._blackboard.update_item(it.item_id, {"status": WorkItemStatus.COMPLETE})

        # Triage entered_at is recent, so dwell time < 2min -> must stay
        orch._evaluate_triage()
        assert orch.is_triage_mode(), "Should stay in triage (dwell < 2min)"

        # 3. Advance time past 2min, reduce to 55% -> verify triage exited
        orch._triage.entered_at = datetime.utcnow() - timedelta(
            seconds=TRIAGE_MIN_DWELL_S + 10
        )
        # Clear more items to 55
        items = orch._blackboard.get_items_by_status(WorkItemStatus.IN_PROGRESS)
        for it in items[55:]:
            orch._blackboard.update_item(it.item_id, {"status": WorkItemStatus.COMPLETE})

        orch._evaluate_triage()
        assert not orch.is_triage_mode(), "Should exit triage at 55% after dwell"

        # 4. Oscillate 85% -> 65% -> 85% -> verify no flapping
        #    (stays out of triage since never hits 90%)
        orch._blackboard._work_items.clear()
        for _ in range(85):
            it = _make_item("repo_map")
            it.status = WorkItemStatus.IN_PROGRESS
            orch._blackboard.post_work_item(it)

        orch._evaluate_triage()
        assert not orch.is_triage_mode(), "85% < 90% entry threshold, should NOT enter"

        # Drop to 65
        items = orch._blackboard.get_items_by_status(WorkItemStatus.IN_PROGRESS)
        for it in items[65:]:
            orch._blackboard.update_item(it.item_id, {"status": WorkItemStatus.COMPLETE})
        orch._evaluate_triage()
        assert not orch.is_triage_mode(), "Still out of triage at 65%"

        # Back to 85
        for _ in range(20):
            it = _make_item("repo_map")
            it.status = WorkItemStatus.IN_PROGRESS
            orch._blackboard.post_work_item(it)
        orch._evaluate_triage()
        assert not orch.is_triage_mode(), "85% still below 90% entry, no flapping"


# ===========================================================================
# Chaos: Escalation timeout
# ===========================================================================

class TestChaosEscalationTimeout:

    def test_chaos_escalation_timeout(self) -> None:
        """Item claimed >31 min ago -> ABANDONED after escalation check."""
        orch = Orchestrator()

        # 1. Submit work item
        item = _make_item("repo_map")
        task_id = orch.submit_task([item])
        ctx = orch.get_task(task_id)
        item_id = ctx.work_item_ids[0]

        # 2. Claim the item
        orch._blackboard.claim_work_item(item_id, "repo_mapper")

        # 3. Set last_heartbeat to 31 minutes ago
        orch._blackboard.update_item(item_id, {
            "last_heartbeat": datetime.utcnow() - timedelta(minutes=31),
        })

        # 4. Run escalation check
        orch._check_escalation_timeouts()

        # 5. Verify item is ABANDONED
        updated = orch._blackboard.get_work_item(item_id)
        assert updated.status == WorkItemStatus.ABANDONED


# ===========================================================================
# Chaos: Multi-tenant isolation
# ===========================================================================

class TestChaosMultiTenantIsolation:

    def test_chaos_multi_tenant_isolation(self) -> None:
        """Register 3 tenants, verify cross-tenant access is blocked."""
        router = TenantRouter()
        tenant_ids = ["tenant_a", "tenant_b", "tenant_c"]
        for tid in tenant_ids:
            router.register_tenant(TenantConfig(tenant_id=tid, display_name=tid))

        isolator = NamespaceIsolator(router)

        # Create items for each tenant
        tenant_items: dict[str, list] = {}
        for tid in tenant_ids:
            items = [uuid4() for _ in range(3)]
            tenant_items[tid] = items
            for iid in items:
                isolator.register_item(tid, iid)

        # Verify own-tenant access allowed
        for tid in tenant_ids:
            for iid in tenant_items[tid]:
                assert isolator.get_owner(iid) == tid

        # Verify cross-tenant access blocked
        assert not isolator.validate_access("tenant_a", "tenant_b")
        assert not isolator.validate_access("tenant_b", "tenant_c")
        assert not isolator.validate_access("tenant_c", "tenant_a")

        # Self-access is allowed
        assert isolator.validate_access("tenant_a", "tenant_a")

        # Verify no data leak: tenant_a cannot own tenant_b's items
        for iid in tenant_items["tenant_b"]:
            assert isolator.get_owner(iid) != "tenant_a"


# ===========================================================================
# Chaos: Executor adapter failure
# ===========================================================================

class TestChaosExecutorAdapterFailure:

    def test_chaos_executor_adapter_failure(self) -> None:
        """Simulate adapter returning failed status, verify graceful handling."""
        engine = PolicyEngine()
        executor = ExecutorAgent(policy_engine=engine)

        # Create a mock adapter that always returns FAILED
        class FailingAdapter:
            def prepare(self, action: RepairAction) -> None:
                pass

            def validate_preconditions(self) -> bool:
                return True

            def execute(self) -> ExecutionResult:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    adapter_type="failing",
                    error="Simulated adapter failure",
                    rollback_available=False,
                )

            def verify_result(self) -> bool:
                return False

            def rollback(self) -> bool:
                return False

        # Register the failing adapter under "git" to intercept git actions
        executor.register_adapter("git", FailingAdapter())

        trajectory = RepairTrajectory(
            actions=[
                RepairAction(
                    action_type=RepairActionType.UPDATE_ATTRIBUTE,
                    target_entity=uuid4(),
                    parameters={"key": "x", "new_value": "y"},
                    confidence=0.9,
                    risk=0.2,
                ),
            ],
            strategy="test",
            confidence=0.9,
            risk=0.2,
        )

        # Execute -- should NOT crash, should return FAILED result
        results = executor.execute(trajectory, environment="staging")
        assert len(results) == 1
        assert results[0].status == ExecutionStatus.FAILED
        assert results[0].error is not None
