"""Phase 6 integration tests: executor, policy, adapters, API, CLI, cross-phase."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.coordination.agents import (
    CausalRCAAgent,
    ExecutorAgent as CoordExecutorAgent,
    HypothesisAgent,
    LawEngineAgent,
    RepoMapperAgent,
)
from src.coordination.orchestrator import Orchestrator
from src.core.coordination import WorkItem, WorkItemStatus
from src.executor.adapters import (
    ContainerAdapter,
    DatabaseAdapter,
    ExecutionResult,
    ExecutionStatus,
    GitAdapter,
    IaCAdapter,
)
from src.executor.agent import ExecutorAgent
from src.policy.engine import PolicyDecision, PolicyDecisionType, PolicyEngine
from src.policy.opa import OPAIntegration
from src.repair.planner import RepairAction, RepairActionType, RepairTrajectory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _low_risk_trajectory() -> RepairTrajectory:
    """Build a trajectory with a single low-risk git action."""
    return RepairTrajectory(
        actions=[
            RepairAction(
                action_type=RepairActionType.UPDATE_ATTRIBUTE,
                target_entity=uuid4(),
                parameters={"key": "status", "old_value": "broken", "new_value": "fixed"},
                description="low-risk attribute update",
                confidence=0.9,
                risk=0.2,
            ),
        ],
        strategy="test",
        confidence=0.9,
        risk=0.2,
    )


def _high_risk_trajectory() -> RepairTrajectory:
    """Build a trajectory with a single high-risk action."""
    return RepairTrajectory(
        actions=[
            RepairAction(
                action_type=RepairActionType.REMOVE_NODE,
                target_entity=uuid4(),
                description="high-risk node removal",
                confidence=0.4,
                risk=0.8,
            ),
        ],
        strategy="test",
        confidence=0.4,
        risk=0.8,
    )


def _make_work_item(task_type: str = "execute_repair",
                     priority: float = 0.5,
                     caps: set[str] | None = None) -> WorkItem:
    return WorkItem(
        task_type=task_type,
        priority=priority,
        required_capabilities=caps or set(),
    )


# ===========================================================================
# Policy + Executor integration
# ===========================================================================

class TestPolicyExecutorIntegration:

    def test_executor_with_policy_approve(self) -> None:
        """Low-risk trajectory -> all actions approved and executed."""
        engine = PolicyEngine()
        executor = ExecutorAgent(policy_engine=engine)

        trajectory = _low_risk_trajectory()
        results = executor.execute(trajectory, environment="staging")

        assert len(results) == 1
        assert results[0].status == ExecutionStatus.COMPLETED

    def test_executor_with_policy_deny_high_risk(self) -> None:
        """OPA policy with max_risk=0.5, action risk=0.8 -> denied."""
        opa = OPAIntegration()
        opa.register_policy("default", {"max_risk": 0.5, "min_confidence": 0.3})

        engine = PolicyEngine(opa=opa)
        executor = ExecutorAgent(policy_engine=engine)

        trajectory = _high_risk_trajectory()
        results = executor.execute(trajectory, environment="staging")

        assert len(results) == 1
        assert results[0].status == ExecutionStatus.FAILED
        assert "denied" in (results[0].error or "").lower() or "risk" in (results[0].error or "").lower()

    def test_executor_with_policy_require_approval(self) -> None:
        """Production environment -> require_approval (still executes for now)."""
        engine = PolicyEngine()
        executor = ExecutorAgent(policy_engine=engine)

        trajectory = _low_risk_trajectory()
        results = executor.execute(trajectory, environment="production")

        # require_approval does NOT deny; the action still proceeds
        assert len(results) == 1
        assert results[0].status == ExecutionStatus.COMPLETED

    def test_executor_rollback_after_execution(self) -> None:
        """Execute trajectory, then rollback each result."""
        engine = PolicyEngine()
        executor = ExecutorAgent(policy_engine=engine)

        trajectory = _low_risk_trajectory()
        results = executor.execute(trajectory, environment="staging")

        for result in results:
            assert result.status == ExecutionStatus.COMPLETED
            rolled = executor.rollback(result.execution_id)
            assert rolled is True


# ===========================================================================
# Adapter integration (full lifecycle)
# ===========================================================================

class TestAdapterFullLifecycle:

    def _run_lifecycle(self, adapter, action: RepairAction) -> None:
        """Run prepare -> validate -> execute -> verify -> rollback."""
        adapter.prepare(action)
        assert adapter.validate_preconditions() is True

        result = adapter.execute()
        assert isinstance(result, ExecutionResult)
        assert result.status == ExecutionStatus.COMPLETED

        assert adapter.verify_result() is True
        assert adapter.rollback() is True

    def test_git_adapter_full_lifecycle(self) -> None:
        adapter = GitAdapter(platform="github")
        action = RepairAction(
            action_type=RepairActionType.UPDATE_ATTRIBUTE,
            target_entity=uuid4(),
            parameters={"key": "version", "new_value": "2.0"},
            confidence=0.8,
            risk=0.3,
        )
        self._run_lifecycle(adapter, action)

    def test_container_adapter_full_lifecycle(self) -> None:
        adapter = ContainerAdapter(platform="kubernetes")
        action = RepairAction(
            action_type=RepairActionType.RECONFIGURE,
            target_entity=uuid4(),
            parameters={"operation": "rolling_restart", "target_version": "v2.0.0"},
            confidence=0.7,
            risk=0.4,
        )
        self._run_lifecycle(adapter, action)

    def test_iac_adapter_full_lifecycle(self) -> None:
        adapter = IaCAdapter(platform="terraform")
        action = RepairAction(
            action_type=RepairActionType.RECONFIGURE,
            target_entity=uuid4(),
            parameters={"resource": "aws_instance"},
            confidence=0.8,
            risk=0.3,
        )
        self._run_lifecycle(adapter, action)

    def test_database_adapter_full_lifecycle(self) -> None:
        adapter = DatabaseAdapter(platform="alembic")
        action = RepairAction(
            action_type=RepairActionType.UPDATE_ATTRIBUTE,
            target_entity=uuid4(),
            parameters={"previous_revision": "rev_010", "target_revision": "rev_011"},
            confidence=0.9,
            risk=0.2,
        )
        self._run_lifecycle(adapter, action)


# ===========================================================================
# API integration
# ===========================================================================

class TestAPIIntegration:

    def test_api_task_lifecycle(self) -> None:
        """Submit task -> get task -> verify status."""
        app = create_app()
        client = TestClient(app)

        # Submit
        resp = client.post("/api/v1/tasks", json={
            "task_type": "analysis",
            "priority": "medium",
            "tenant_id": "default",
        })
        assert resp.status_code == 201
        body = resp.json()
        task_id = body["task_id"]
        assert body["status"] == "pending"

        # Retrieve
        resp2 = client.get(f"/api/v1/tasks/{task_id}")
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["task_id"] == task_id
        assert body2["status"] == "pending"

    def test_api_health_ready(self) -> None:
        """Both health endpoints return healthy."""
        app = create_app()
        client = TestClient(app)

        resp_health = client.get("/health")
        assert resp_health.status_code == 200
        assert resp_health.json()["status"] == "healthy"

        resp_ready = client.get("/health/ready")
        assert resp_ready.status_code == 200
        assert resp_ready.json()["status"] == "healthy"


# ===========================================================================
# CLI integration
# ===========================================================================

class TestCLIIntegration:

    def test_cli_full_workflow(self) -> None:
        """version + health + analyze + status all succeed."""
        from typer.testing import CliRunner
        from src.cli.main import app as cli_app

        runner = CliRunner()

        # version
        result = runner.invoke(cli_app, ["version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output

        # health
        result = runner.invoke(cli_app, ["health"])
        assert result.exit_code == 0
        assert "healthy" in result.output.lower()

        # analyze
        result = runner.invoke(cli_app, ["analyze"])
        assert result.exit_code == 0
        assert "Analysis complete" in result.output or "Analyzing" in result.output

        # status
        result = runner.invoke(cli_app, ["status"])
        assert result.exit_code == 0
        assert "healthy" in result.output.lower() or "Agents" in result.output


# ===========================================================================
# Cross-phase integration
# ===========================================================================

class TestCrossPhaseIntegration:

    def test_orchestrator_with_executor(self) -> None:
        """Create orchestrator with executor agent, submit execute_repair, verify completion."""
        orch = Orchestrator()

        # Register the coordination-layer ExecutorAgent (not the executor.agent one)
        executor_agent = CoordExecutorAgent()
        orch.register_agent(executor_agent)
        # Also register mapper so we have more than one agent
        orch.register_agent(RepoMapperAgent())

        item = _make_work_item(
            task_type="execute_repair",
            priority=0.8,
            caps={"execute_repair"},
        )
        task_id = orch.submit_task([item])

        # Run cycles until termination or max iterations
        for _ in range(10):
            orch.run_cycle()
            if orch.check_termination(task_id):
                break

        assert orch.check_termination(task_id)
        ctx = orch.get_task(task_id)
        final_item = orch._blackboard.get_work_item(ctx.work_item_ids[0])
        assert final_item.status == WorkItemStatus.COMPLETE
