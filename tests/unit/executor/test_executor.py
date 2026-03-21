from __future__ import annotations

"""Unit tests for the Phase 6 executor layer: ExecutorAgent and all adapters."""

import pytest
from uuid import uuid4

from src.executor.agent import ExecutorAgent
from src.executor.adapters import (
    ExecutionResult,
    ExecutionStatus,
    GitAdapter,
    ContainerAdapter,
    CIAdapter,
    IaCAdapter,
    DatabaseAdapter,
    AlertAdapter,
)
from src.repair.planner import RepairAction, RepairActionType, RepairTrajectory
from src.policy.engine import PolicyEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _action(action_type=RepairActionType.UPDATE_ATTRIBUTE, risk=0.3, confidence=0.7):
    return RepairAction(
        action_type=action_type,
        target_entity=uuid4(),
        parameters={"key": "value"},
        confidence=confidence,
        risk=risk,
    )


def _trajectory(actions=None):
    return RepairTrajectory(
        actions=actions or [_action()],
        strategy="test",
    )


# ---------------------------------------------------------------------------
# ExecutorAgent tests
# ---------------------------------------------------------------------------


def test_executor_agent_init():
    """ExecutorAgent initialises with default adapters registered."""
    agent = ExecutorAgent()
    assert "git" in agent._adapters
    assert "container" in agent._adapters
    assert "ci" in agent._adapters
    assert "iac" in agent._adapters
    assert "database" in agent._adapters
    assert "alert" in agent._adapters


def test_executor_execute_trajectory_success():
    """Execute trajectory with 2 actions -> 2 completed results."""
    agent = ExecutorAgent()
    traj = _trajectory(actions=[_action(), _action()])
    results = agent.execute(traj, environment="staging")
    assert len(results) == 2
    completed = [r for r in results if r.status == ExecutionStatus.COMPLETED]
    assert len(completed) == 2


def test_executor_policy_deny_skips_action():
    """When policy denies an action, the result should be FAILED."""
    agent = ExecutorAgent()
    # risk=0.95 exceeds default OPA max_risk=0.8 -> DENY
    traj = _trajectory(actions=[_action(risk=0.95)])
    results = agent.execute(traj, environment="staging")
    assert len(results) == 1
    assert results[0].status == ExecutionStatus.FAILED
    assert "Policy denied" in (results[0].error or "")


def test_executor_rollback():
    """Execute, then rollback -> True."""
    agent = ExecutorAgent()
    traj = _trajectory(actions=[_action()])
    results = agent.execute(traj, environment="staging")
    assert len(results) == 1
    assert results[0].status == ExecutionStatus.COMPLETED
    success = agent.rollback(results[0].execution_id)
    assert success is True


def test_executor_rollback_not_found():
    """Rollback unknown execution_id -> False."""
    agent = ExecutorAgent()
    success = agent.rollback(uuid4())
    assert success is False


# ---------------------------------------------------------------------------
# Individual adapter tests
# ---------------------------------------------------------------------------


def test_git_adapter_execute():
    """GitAdapter: prepare + validate + execute -> completed."""
    adapter = GitAdapter()
    action = _action()
    adapter.prepare(action)
    assert adapter.validate_preconditions() is True
    result = adapter.execute()
    assert result.status == ExecutionStatus.COMPLETED
    assert result.adapter_type == "git"
    assert adapter.verify_result() is True


def test_container_adapter_execute():
    """ContainerAdapter: prepare + validate + execute -> completed."""
    adapter = ContainerAdapter()
    action = _action()
    adapter.prepare(action)
    assert adapter.validate_preconditions() is True
    result = adapter.execute()
    assert result.status == ExecutionStatus.COMPLETED
    assert result.adapter_type == "container"
    assert adapter.verify_result() is True


def test_ci_adapter_execute():
    """CIAdapter: prepare + validate + execute -> completed."""
    adapter = CIAdapter()
    action = _action()
    adapter.prepare(action)
    assert adapter.validate_preconditions() is True
    result = adapter.execute()
    assert result.status == ExecutionStatus.COMPLETED
    assert result.adapter_type == "ci"
    assert adapter.verify_result() is True


def test_iac_adapter_execute():
    """IaCAdapter: prepare + validate + execute -> completed."""
    adapter = IaCAdapter()
    action = _action(action_type=RepairActionType.RECONFIGURE)
    adapter.prepare(action)
    assert adapter.validate_preconditions() is True
    result = adapter.execute()
    assert result.status == ExecutionStatus.COMPLETED
    assert result.adapter_type == "iac"
    assert adapter.verify_result() is True


def test_database_adapter_execute():
    """DatabaseAdapter: prepare + validate + execute -> completed."""
    adapter = DatabaseAdapter()
    action = _action()
    adapter.prepare(action)
    assert adapter.validate_preconditions() is True
    result = adapter.execute()
    assert result.status == ExecutionStatus.COMPLETED
    assert result.adapter_type == "database"
    assert adapter.verify_result() is True


def test_alert_adapter_no_rollback():
    """AlertAdapter: execute result has rollback_available=False."""
    adapter = AlertAdapter()
    action = _action()
    adapter.prepare(action)
    assert adapter.validate_preconditions() is True
    result = adapter.execute()
    assert result.status == ExecutionStatus.COMPLETED
    assert result.adapter_type == "alert"
    assert result.rollback_available is False
    assert adapter.verify_result() is True
