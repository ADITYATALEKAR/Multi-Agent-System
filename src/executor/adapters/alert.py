"""AlertAdapter: adapter for alerting platforms (PagerDuty, OpsGenie)."""
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


class AlertAdapter:
    """Executes alert actions across PagerDuty and OpsGenie."""

    SUPPORTED_PLATFORMS = ("pagerduty", "opsgenie")

    def __init__(self, platform: str = "pagerduty") -> None:
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported alert platform: {platform}")
        self.platform = platform
        self._action: RepairAction | None = None
        self._alert_id: str = ""
        self._alert_status: str = ""
        logger.info("alert_adapter_init", platform=platform)

    # ── interface ────────────────────────────────────────────────────────

    def prepare(self, action: RepairAction) -> None:
        """Store the action and generate an alert identifier."""
        _load_types()
        self._action = action
        self._alert_id = f"alert-{uuid4().hex[:8]}"
        logger.info(
            "alert_prepare",
            action_id=str(action.action_id),
            alert_id=self._alert_id,
            platform=self.platform,
        )

    def validate_preconditions(self) -> bool:
        """Check alerting platform reachable and API key valid (simulated)."""
        _load_types()
        logger.info("alert_validate_preconditions", platform=self.platform)
        return True

    def execute(self) -> Any:
        """Create or resolve an alert (simulated)."""
        _load_types()
        operation = "create"
        if self._action:
            operation = self._action.parameters.get("alert_operation", "create")

        logger.info(
            "alert_execute",
            platform=self.platform,
            alert_id=self._alert_id,
            operation=operation,
        )
        self._alert_status = "triggered" if operation == "create" else "resolved"

        result = _ExecutionResult(
            status=_ExecutionStatus.COMPLETED,
            adapter_type="alert",
            completed_at=datetime.utcnow(),
            output={
                "platform": self.platform,
                "alert_id": self._alert_id,
                "operation": operation,
                "alert_status": self._alert_status,
                "action_id": str(self._action.action_id) if self._action else "",
                "message": f"Alert {operation}d (simulated)",
            },
            rollback_available=False,
        )
        logger.info("alert_execute_done", alert_id=self._alert_id, operation=operation)
        return result

    def verify_result(self) -> bool:
        """Check alert was created/resolved successfully (simulated)."""
        _load_types()
        ok = bool(self._alert_id and self._alert_status)
        logger.info("alert_verify", alert_id=self._alert_id, verified=ok)
        return ok

    def rollback(self) -> bool:
        """Resolve the alert; cannot un-send a notification (simulated)."""
        _load_types()
        logger.info(
            "alert_rollback",
            platform=self.platform,
            alert_id=self._alert_id,
            note="Cannot un-send; resolving alert instead",
        )
        self._alert_status = "resolved"
        return True
