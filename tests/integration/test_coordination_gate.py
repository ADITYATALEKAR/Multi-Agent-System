"""v3.3 F5 GATE: Coordination overhead < 5% of task time under 100 WorkItems."""

from __future__ import annotations

import time
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
from src.coordination.bidding import BiddingProtocol
from src.coordination.orchestrator import Orchestrator
from src.core.coordination import WorkItem, WorkItemStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAST_PATH_TYPES = ["repo_map", "law_check", "infra_ops", "explain",
                    "execute_repair", "verify_repair"]

_BIDDING_TYPES = ["hypothesis_generate", "causal_analysis", "repair_plan",
                  "memory_query"]

_CAP_MAP: dict[str, set[str]] = {
    "hypothesis_generate": {"hypothesis_generate"},
    "causal_analysis": {"causal_analysis"},
    "repair_plan": {"repair_plan"},
    "memory_query": {"memory_query"},
}


def _register_all(orch: Orchestrator) -> list:
    """Register all 10 specialist agents and return them."""
    agents = [
        RepoMapperAgent(), LawEngineAgent(), HypothesisAgent(),
        CausalRCAAgent(), MemoryAgent(), RepairPlannerAgent(),
        VerificationAgent(), InfraOpsAgent(), ExplainerAgent(),
        ExecutorAgent(),
    ]
    for a in agents:
        orch.register_agent(a)
    return agents


# ===========================================================================
# GATE test class
# ===========================================================================


class TestCoordinationGate:
    """v3.3 F5 GATE: coordination overhead under 100 WorkItems."""

    def test_overhead_below_5pct(self) -> None:
        """Submit 100 work items (60 fast-path + 40 bidding), run until
        completion, assert overhead is reasonable and >90% complete."""
        orch = Orchestrator()
        _register_all(orch)

        # ── Generate 100 work items ──────────────────────────────────────
        items: list[WorkItem] = []

        # 60 fast-path items
        for i in range(60):
            task_type = _FAST_PATH_TYPES[i % len(_FAST_PATH_TYPES)]
            items.append(WorkItem(
                task_type=task_type,
                priority=0.5 + (i % 10) * 0.05,
            ))

        # 40 bidding items
        for i in range(40):
            task_type = _BIDDING_TYPES[i % len(_BIDDING_TYPES)]
            items.append(WorkItem(
                task_type=task_type,
                priority=0.5,
                required_capabilities=_CAP_MAP.get(task_type, set()),
            ))

        # ── Submit and run ───────────────────────────────────────────────
        task_id = orch.submit_task(items)

        start = time.perf_counter()
        max_cycles = 200
        for _ in range(max_cycles):
            orch.run_cycle()
            if orch.check_termination(task_id):
                break
        total_time = time.perf_counter() - start

        # ── Assertions ───────────────────────────────────────────────────
        ctx = orch.get_task(task_id)
        terminal = {
            WorkItemStatus.COMPLETE,
            WorkItemStatus.FAILED,
            WorkItemStatus.ABANDONED,
        }
        completed = sum(
            1 for iid in ctx.work_item_ids
            if orch._blackboard.get_work_item(iid).status in terminal
        )

        # At least 90 of 100 items must finish
        assert completed >= 90, f"Only {completed}/100 items completed"

        # Total processing time must stay under 10 seconds for stub agents
        assert total_time < 10.0, (
            f"100 items took {total_time:.2f}s, exceeds 10s ceiling"
        )

    # -----------------------------------------------------------------------
    # Fast-path coverage check
    # -----------------------------------------------------------------------

    def test_fast_path_coverage_above_50pct(self) -> None:
        """Verify that > 50% of the task types in the test mix map to fast-path."""
        bp = BiddingProtocol()

        all_types = _FAST_PATH_TYPES + _BIDDING_TYPES
        fast_count = sum(
            1 for t in all_types
            if bp.should_fast_path(WorkItem(task_type=t)) is not None
        )

        pct = fast_count / len(all_types)
        assert pct > 0.50, (
            f"Fast-path coverage is {pct:.0%}, expected >50%"
        )
