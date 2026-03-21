"""ExecutorAgent: orchestrates repair action execution via adapters."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from src.executor.adapters import (
    AlertAdapter,
    CIAdapter,
    ContainerAdapter,
    DatabaseAdapter,
    ExecutionResult,
    ExecutionStatus,
    GitAdapter,
    IaCAdapter,
)
from src.policy.engine import PolicyDecision, PolicyDecisionType, PolicyEngine
from src.repair.planner import RepairAction, RepairActionType, RepairTrajectory

logger = structlog.get_logger()

# ── Action-type to adapter-type mapping ──────────────────────────────────

_ACTION_ADAPTER_MAP: dict[RepairActionType, str] = {
    RepairActionType.ADD_NODE: "git",
    RepairActionType.REMOVE_NODE: "git",
    RepairActionType.UPDATE_ATTRIBUTE: "git",
    RepairActionType.ADD_EDGE: "git",
    RepairActionType.REMOVE_EDGE: "git",
    RepairActionType.RECONFIGURE: "iac",
}


class ExecutorAgent:
    """Orchestrates execution of repair trajectories via pluggable adapters.

    Flow per action:
    1. Evaluate policy (approve / deny / require_approval)
    2. Select adapter based on action type
    3. Prepare adapter with action details
    4. Validate preconditions
    5. Execute via adapter
    6. Verify result
    """

    def __init__(self, policy_engine: PolicyEngine | None = None) -> None:
        self._policy_engine = policy_engine or PolicyEngine()
        self._adapters: dict[str, Any] = {}
        self._execution_history: dict[UUID, tuple[str, ExecutionResult]] = {}
        self._register_default_adapters()
        logger.info("executor_agent_init")

    # ── adapter registration ─────────────────────────────────────────────

    def _register_default_adapters(self) -> None:
        """Register the six default adapters."""
        self._adapters["git"] = GitAdapter()
        self._adapters["container"] = ContainerAdapter()
        self._adapters["ci"] = CIAdapter()
        self._adapters["iac"] = IaCAdapter()
        self._adapters["database"] = DatabaseAdapter()
        self._adapters["alert"] = AlertAdapter()

    def register_adapter(self, name: str, adapter: Any) -> None:
        """Register or replace an adapter by name."""
        self._adapters[name] = adapter
        logger.info("executor_adapter_registered", adapter=name)

    # ── execution ────────────────────────────────────────────────────────

    def execute(
        self,
        trajectory: RepairTrajectory,
        environment: str = "staging",
        tenant_id: str = "default",
    ) -> list[ExecutionResult]:
        """Execute all actions in a repair trajectory.

        Returns:
            List of ExecutionResult, one per action.
        """
        results: list[ExecutionResult] = []

        for action in trajectory.actions:
            result = self._execute_single(action, environment, tenant_id)
            results.append(result)

        logger.info(
            "executor_trajectory_done",
            trajectory_id=str(trajectory.trajectory_id),
            total=len(results),
            completed=sum(
                1 for r in results if r.status == ExecutionStatus.COMPLETED
            ),
            failed=sum(
                1 for r in results if r.status == ExecutionStatus.FAILED
            ),
        )
        return results

    def _execute_single(
        self,
        action: RepairAction,
        environment: str,
        tenant_id: str,
    ) -> ExecutionResult:
        """Execute a single repair action through the full pipeline."""
        # 1. Policy evaluation
        decision = self._policy_engine.evaluate(
            action_type=action.action_type.value,
            environment=environment,
            risk=action.risk,
            confidence=action.confidence,
            tenant_id=tenant_id,
        )

        if decision.decision == PolicyDecisionType.DENY:
            logger.warning(
                "executor_action_denied",
                action_id=str(action.action_id),
                reason=decision.reason,
            )
            failed = ExecutionResult(
                status=ExecutionStatus.FAILED,
                adapter_type="none",
                error=f"Policy denied: {decision.reason}",
                rollback_available=False,
            )
            return failed

        # 2. Select adapter
        adapter_type = self._select_adapter_type(action)
        adapter = self._adapters.get(adapter_type)

        if adapter is None:
            logger.error("executor_no_adapter", adapter_type=adapter_type)
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                adapter_type=adapter_type,
                error=f"No adapter registered for type: {adapter_type}",
                rollback_available=False,
            )

        # 3. Prepare
        adapter.prepare(action)

        # 4. Validate preconditions
        if not adapter.validate_preconditions():
            logger.warning(
                "executor_precondition_failed",
                action_id=str(action.action_id),
                adapter_type=adapter_type,
            )
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                adapter_type=adapter_type,
                error="Precondition validation failed",
                rollback_available=False,
            )

        # 5. Execute
        result: ExecutionResult = adapter.execute()

        # 6. Verify
        verified = adapter.verify_result()
        if not verified:
            logger.warning(
                "executor_verify_failed",
                action_id=str(action.action_id),
                adapter_type=adapter_type,
            )
            result.status = ExecutionStatus.FAILED
            result.error = "Post-execution verification failed"

        # Record in history for rollback
        self._execution_history[result.execution_id] = (adapter_type, result)

        logger.info(
            "executor_action_done",
            action_id=str(action.action_id),
            adapter_type=adapter_type,
            status=result.status.value,
        )
        return result

    # ── rollback ─────────────────────────────────────────────────────────

    def rollback(self, execution_id: UUID) -> bool:
        """Roll back a previously executed action by its execution_id."""
        entry = self._execution_history.get(execution_id)
        if entry is None:
            logger.warning("executor_rollback_not_found", execution_id=str(execution_id))
            return False

        adapter_type, result = entry
        adapter = self._adapters.get(adapter_type)
        if adapter is None:
            logger.error("executor_rollback_no_adapter", adapter_type=adapter_type)
            return False

        success = adapter.rollback()
        if success:
            result.status = ExecutionStatus.ROLLED_BACK

        logger.info(
            "executor_rollback",
            execution_id=str(execution_id),
            adapter_type=adapter_type,
            success=success,
        )
        return success

    # ── helpers ──────────────────────────────────────────────────────────

    def _select_adapter_type(self, action: RepairAction) -> str:
        """Map an action type to its adapter type string."""
        return _ACTION_ADAPTER_MAP.get(action.action_type, "git")
