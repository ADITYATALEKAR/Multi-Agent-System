"""v3.3 F6 GATE: All chaos scenarios pass, recovery < 120s."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from src.coordination.orchestrator import (
    ESCALATION_TIMEOUT_S,
    Orchestrator,
    TRIAGE_MIN_DWELL_S,
)
from src.coordination.blackboard import BlackboardManager, MAX_PENDING_CLAIMS
from src.coordination.agents import (
    RepoMapperAgent,
    ExecutorAgent as CoordExecutorAgent,
)
from src.coordination.agents.base import HEARTBEAT_TIMEOUT_S
from src.coordination.multitenancy import (
    NamespaceIsolator,
    TenantConfig,
    TenantRouter,
)
from src.core.coordination import Claim, WorkItem, WorkItemStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item(task_type: str = "repo_map",
               priority: float = 0.5,
               caps: set[str] | None = None) -> WorkItem:
    return WorkItem(
        task_type=task_type,
        priority=priority,
        required_capabilities=caps or set(),
    )


class TestChaosGate:
    """v3.3 F6 GATE: All chaos scenarios pass, recovery < 120s."""

    def test_all_chaos_scenarios_recovery_under_120s(self) -> None:
        """Run all chaos scenarios and verify recovery time."""
        scenarios = [
            ("agent_crash", self._scenario_agent_crash),
            ("blackboard_bloat", self._scenario_blackboard_bloat),
            ("escalation_timeout", self._scenario_escalation_timeout),
            ("triage_stability", self._scenario_triage_stability),
            ("tenant_isolation", self._scenario_tenant_isolation),
        ]

        results = {}
        for name, scenario_fn in scenarios:
            start = time.perf_counter()
            passed = scenario_fn()
            duration = time.perf_counter() - start
            results[name] = {"passed": passed, "duration_s": duration}

        # All must pass
        all_passed = all(r["passed"] for r in results.values())
        # All must recover in < 120s
        all_fast = all(r["duration_s"] < 120.0 for r in results.values())

        assert all_passed, f"Chaos scenarios failed: {results}"
        assert all_fast, f"Recovery too slow: {results}"

    # -------------------------------------------------------------------
    # Scenario implementations
    # -------------------------------------------------------------------

    def _scenario_agent_crash(self) -> bool:
        """Agent crash -> heartbeat timeout -> item released -> reassigned."""
        try:
            orch = Orchestrator()
            mapper = RepoMapperAgent()
            orch.register_agent(mapper)

            item = _make_item("repo_map", priority=0.8)
            task_id = orch.submit_task([item])
            ctx = orch.get_task(task_id)
            item_id = ctx.work_item_ids[0]

            # Put item IN_PROGRESS claimed by mapper
            orch._blackboard.update_item(item_id, {
                "status": WorkItemStatus.IN_PROGRESS,
                "claimed_by": mapper.agent_id,
                "last_heartbeat": datetime.utcnow(),
            })

            # Crash: stop agent, expire heartbeat
            mapper.stop()
            mapper._status.last_heartbeat = datetime.utcnow() - timedelta(
                seconds=HEARTBEAT_TIMEOUT_S + 10
            )

            # Force heartbeat check
            orch._last_heartbeat_check = datetime.utcnow() - timedelta(seconds=60)
            orch._check_heartbeats()

            released = orch._blackboard.get_work_item(item_id)
            if released.status != WorkItemStatus.OPEN:
                return False

            # Replace and reassign
            orch.unregister_agent(mapper.agent_id)
            orch.register_agent(RepoMapperAgent(agent_id="mapper_v2"))
            orch.run_cycle()

            final = orch._blackboard.get_work_item(item_id)
            return final.status in (
                WorkItemStatus.COMPLETE,
                WorkItemStatus.IN_PROGRESS,
                WorkItemStatus.CLAIMED,
            )
        except Exception:
            return False

    def _scenario_blackboard_bloat(self) -> bool:
        """Flood claims beyond 200 limit, verify cleanup keeps within bounds."""
        try:
            bb = BlackboardManager()
            item = _make_item("repo_map")
            bb.post_work_item(item)

            for i in range(250):
                claim = Claim(agent_id=f"flood_{i}", work_item_id=item.item_id)
                bb.post_claim(claim)

            # Hard limit should prevent going over
            if bb.pending_claim_count > MAX_PENDING_CLAIMS:
                return False

            bb.cleanup_stale()
            return bb.pending_claim_count <= MAX_PENDING_CLAIMS
        except Exception:
            return False

    def _scenario_escalation_timeout(self) -> bool:
        """Item stuck > 30 min -> ABANDONED."""
        try:
            orch = Orchestrator()
            item = _make_item("repo_map")
            task_id = orch.submit_task([item])
            ctx = orch.get_task(task_id)
            item_id = ctx.work_item_ids[0]

            orch._blackboard.claim_work_item(item_id, "repo_mapper")
            orch._blackboard.update_item(item_id, {
                "last_heartbeat": datetime.utcnow() - timedelta(minutes=31),
            })

            orch._check_escalation_timeouts()

            updated = orch._blackboard.get_work_item(item_id)
            return updated.status == WorkItemStatus.ABANDONED
        except Exception:
            return False

    def _scenario_triage_stability(self) -> bool:
        """Verify triage hysteresis: no flapping below 90% threshold."""
        try:
            orch = Orchestrator()

            # Enter triage at 95%
            for _ in range(95):
                it = _make_item("repo_map")
                it.status = WorkItemStatus.IN_PROGRESS
                orch._blackboard.post_work_item(it)
            orch._evaluate_triage()
            if not orch.is_triage_mode():
                return False

            # Drop to 65%, dwell < 2min -> stays in triage
            items = orch._blackboard.get_items_by_status(WorkItemStatus.IN_PROGRESS)
            for it in items[65:]:
                orch._blackboard.update_item(it.item_id, {"status": WorkItemStatus.COMPLETE})
            orch._evaluate_triage()
            if not orch.is_triage_mode():
                return False

            # Advance past dwell, drop to 55% -> exits
            orch._triage.entered_at = datetime.utcnow() - timedelta(
                seconds=TRIAGE_MIN_DWELL_S + 10
            )
            items = orch._blackboard.get_items_by_status(WorkItemStatus.IN_PROGRESS)
            for it in items[55:]:
                orch._blackboard.update_item(it.item_id, {"status": WorkItemStatus.COMPLETE})
            orch._evaluate_triage()
            if orch.is_triage_mode():
                return False

            # Oscillate at 85% -> should NOT re-enter
            orch._blackboard._work_items.clear()
            for _ in range(85):
                it = _make_item("repo_map")
                it.status = WorkItemStatus.IN_PROGRESS
                orch._blackboard.post_work_item(it)
            orch._evaluate_triage()
            return not orch.is_triage_mode()
        except Exception:
            return False

    def _scenario_tenant_isolation(self) -> bool:
        """Three tenants, verify strict isolation."""
        try:
            router = TenantRouter()
            for tid in ("t1", "t2", "t3"):
                router.register_tenant(TenantConfig(tenant_id=tid, display_name=tid))

            isolator = NamespaceIsolator(router)

            items_per_tenant: dict[str, list] = {}
            for tid in ("t1", "t2", "t3"):
                item_ids = [uuid4() for _ in range(3)]
                items_per_tenant[tid] = item_ids
                for iid in item_ids:
                    isolator.register_item(tid, iid)

            # Self access works
            if not isolator.validate_access("t1", "t1"):
                return False

            # Cross access blocked
            if isolator.validate_access("t1", "t2"):
                return False
            if isolator.validate_access("t2", "t3"):
                return False

            # Ownership correct
            for tid, iids in items_per_tenant.items():
                for iid in iids:
                    if isolator.get_owner(iid) != tid:
                        return False

            return True
        except Exception:
            return False
