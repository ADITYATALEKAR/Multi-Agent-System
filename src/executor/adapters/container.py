"""ContainerAdapter: adapter for container orchestration (Kubernetes, Docker, ECS)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

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


class ContainerAdapter:
    """Executes container-related repair actions across Kubernetes, Docker, and ECS."""

    SUPPORTED_PLATFORMS = ("kubernetes", "docker", "ecs")

    def __init__(self, platform: str = "kubernetes") -> None:
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported container platform: {platform}")
        self.platform = platform
        self._action: RepairAction | None = None
        self._previous_version: str = "v1.0.0"
        self._current_version: str = ""
        logger.info("container_adapter_init", platform=platform)

    # ── interface ────────────────────────────────────────────────────────

    def prepare(self, action: RepairAction) -> None:
        """Store the action and snapshot the current deployment version."""
        _load_types()
        self._action = action
        self._previous_version = action.parameters.get("previous_version", "v1.0.0")
        self._current_version = action.parameters.get("target_version", "v1.1.0")
        logger.info(
            "container_prepare",
            action_id=str(action.action_id),
            platform=self.platform,
            previous_version=self._previous_version,
        )

    def validate_preconditions(self) -> bool:
        """Check cluster reachable and namespace exists (simulated)."""
        _load_types()
        logger.info("container_validate_preconditions", platform=self.platform)
        return True

    def execute(self) -> Any:
        """Perform rolling restart / scale / deploy (simulated)."""
        _load_types()
        operation = "rolling_restart"
        if self._action:
            operation = self._action.parameters.get("operation", "rolling_restart")

        logger.info(
            "container_execute",
            platform=self.platform,
            operation=operation,
            target_version=self._current_version,
        )

        result = _ExecutionResult(
            status=_ExecutionStatus.COMPLETED,
            adapter_type="container",
            completed_at=datetime.utcnow(),
            output={
                "platform": self.platform,
                "operation": operation,
                "previous_version": self._previous_version,
                "current_version": self._current_version,
                "action_id": str(self._action.action_id) if self._action else "",
                "message": f"{operation} completed (simulated)",
            },
            rollback_available=True,
        )
        logger.info("container_execute_done", operation=operation)
        return result

    def verify_result(self) -> bool:
        """Check deployment health (simulated)."""
        _load_types()
        logger.info("container_verify", platform=self.platform)
        return True

    def rollback(self) -> bool:
        """Roll back to the previous deployment version (simulated)."""
        _load_types()
        logger.info(
            "container_rollback",
            platform=self.platform,
            rollback_to=self._previous_version,
        )
        self._current_version = self._previous_version
        return True
