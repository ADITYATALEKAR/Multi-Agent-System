"""CIAdapter: adapter for CI/CD pipeline operations (GitHub Actions, GitLab CI, Jenkins)."""
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


class CIAdapter:
    """Executes CI/CD pipeline actions across GitHub Actions, GitLab CI, and Jenkins."""

    SUPPORTED_PLATFORMS = ("github_actions", "gitlab_ci", "jenkins")

    def __init__(self, platform: str = "github_actions") -> None:
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported CI platform: {platform}")
        self.platform = platform
        self._action: RepairAction | None = None
        self._pipeline_id: str = ""
        self._pipeline_status: str = ""
        logger.info("ci_adapter_init", platform=platform)

    # ── interface ────────────────────────────────────────────────────────

    def prepare(self, action: RepairAction) -> None:
        """Store the action and identify the target pipeline."""
        _load_types()
        self._action = action
        self._pipeline_id = f"pipeline-{uuid4().hex[:8]}"
        logger.info(
            "ci_prepare",
            action_id=str(action.action_id),
            pipeline_id=self._pipeline_id,
            platform=self.platform,
        )

    def validate_preconditions(self) -> bool:
        """Check CI system is reachable and pipeline config exists (simulated)."""
        _load_types()
        logger.info("ci_validate_preconditions", platform=self.platform)
        return True

    def execute(self) -> Any:
        """Trigger pipeline and wait for result (simulated)."""
        _load_types()
        logger.info(
            "ci_execute",
            platform=self.platform,
            pipeline_id=self._pipeline_id,
        )
        self._pipeline_status = "success"
        result = _ExecutionResult(
            status=_ExecutionStatus.COMPLETED,
            adapter_type="ci",
            completed_at=datetime.utcnow(),
            output={
                "platform": self.platform,
                "pipeline_id": self._pipeline_id,
                "pipeline_status": self._pipeline_status,
                "action_id": str(self._action.action_id) if self._action else "",
                "message": "Pipeline triggered and completed (simulated)",
            },
            rollback_available=True,
        )
        logger.info("ci_execute_done", pipeline_id=self._pipeline_id)
        return result

    def verify_result(self) -> bool:
        """Check pipeline finished successfully (simulated)."""
        _load_types()
        ok = self._pipeline_status == "success"
        logger.info("ci_verify", pipeline_id=self._pipeline_id, verified=ok)
        return ok

    def rollback(self) -> bool:
        """Cancel running pipeline (simulated)."""
        _load_types()
        logger.info("ci_rollback", pipeline_id=self._pipeline_id, platform=self.platform)
        self._pipeline_status = "cancelled"
        self._pipeline_id = ""
        return True
