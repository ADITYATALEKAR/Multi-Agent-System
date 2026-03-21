"""IaCAdapter: adapter for Infrastructure-as-Code (Terraform, Pulumi, CloudFormation)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import structlog

from src.repair.planner import RepairAction

logger = structlog.get_logger()

_ExecutionResult = None
_ExecutionStatus = None


def _load_types():
    global _ExecutionResult, _ExecutionStatus
    if _ExecutionResult is None:
        from src.executor.adapters import ExecutionResult, ExecutionStatus
        _ExecutionResult = ExecutionResult
        _ExecutionStatus = ExecutionStatus


class IaCAdapter:
    """Executes IaC actions across Terraform, Pulumi, and CloudFormation."""

    SUPPORTED_PLATFORMS = ("terraform", "pulumi", "cloudformation")

    def __init__(self, platform: str = "terraform") -> None:
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported IaC platform: {platform}")
        self.platform = platform
        self._action: RepairAction | None = None
        self._plan_id: str = ""
        self._state_snapshot: str = ""
        logger.info("iac_adapter_init", platform=platform)

    # ── interface ────────────────────────────────────────────────────────

    def prepare(self, action: RepairAction) -> None:
        """Store the action and snapshot current IaC state."""
        _load_types()
        self._action = action
        self._plan_id = f"plan-{uuid4().hex[:8]}"
        self._state_snapshot = f"state-{uuid4().hex[:8]}"
        logger.info(
            "iac_prepare",
            action_id=str(action.action_id),
            plan_id=self._plan_id,
            platform=self.platform,
        )

    def validate_preconditions(self) -> bool:
        """Check IaC state lock available and provider credentials valid (simulated)."""
        _load_types()
        logger.info("iac_validate_preconditions", platform=self.platform)
        return True

    def execute(self) -> Any:
        """Run plan + apply (simulated)."""
        _load_types()
        logger.info(
            "iac_execute",
            platform=self.platform,
            plan_id=self._plan_id,
        )
        resources_changed = 3  # simulated count
        result = _ExecutionResult(
            status=_ExecutionStatus.COMPLETED,
            adapter_type="iac",
            completed_at=datetime.utcnow(),
            output={
                "platform": self.platform,
                "plan_id": self._plan_id,
                "state_snapshot": self._state_snapshot,
                "resources_changed": resources_changed,
                "action_id": str(self._action.action_id) if self._action else "",
                "message": f"Plan applied, {resources_changed} resources changed (simulated)",
            },
            rollback_available=True,
        )
        logger.info("iac_execute_done", plan_id=self._plan_id, resources_changed=resources_changed)
        return result

    def verify_result(self) -> bool:
        """Check infrastructure converged to desired state (simulated)."""
        _load_types()
        ok = bool(self._plan_id)
        logger.info("iac_verify", plan_id=self._plan_id, verified=ok)
        return ok

    def rollback(self) -> bool:
        """Destroy applied resources / rollback stack (simulated)."""
        _load_types()
        logger.info(
            "iac_rollback",
            platform=self.platform,
            state_snapshot=self._state_snapshot,
        )
        self._plan_id = ""
        return True
