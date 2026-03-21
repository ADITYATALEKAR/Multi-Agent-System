"""GitAdapter: adapter for Git platform operations (GitHub, GitLab, Bitbucket)."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

import structlog

from src.repair.planner import RepairAction

logger = structlog.get_logger()

# Avoid circular import at module level; use late import in methods.
_ExecutionResult = None
_ExecutionStatus = None


def _load_types():
    global _ExecutionResult, _ExecutionStatus
    if _ExecutionResult is None:
        from src.executor.adapters import ExecutionResult, ExecutionStatus
        _ExecutionResult = ExecutionResult
        _ExecutionStatus = ExecutionStatus


class GitAdapter:
    """Executes git-related repair actions across GitHub, GitLab, and Bitbucket."""

    SUPPORTED_PLATFORMS = ("github", "gitlab", "bitbucket")

    def __init__(self, platform: str = "github") -> None:
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported git platform: {platform}")
        self.platform = platform
        self._action: RepairAction | None = None
        self._branch_name: str = ""
        self._pr_id: str = ""
        logger.info("git_adapter_init", platform=platform)

    # ── interface ────────────────────────────────────────────────────────

    def prepare(self, action: RepairAction) -> None:
        """Store the action and derive a branch name."""
        _load_types()
        self._action = action
        self._branch_name = f"repair/{action.action_id.hex[:8]}"
        logger.info(
            "git_prepare",
            action_id=str(action.action_id),
            branch=self._branch_name,
            platform=self.platform,
        )

    def validate_preconditions(self) -> bool:
        """Check branch does not conflict (simulated)."""
        _load_types()
        logger.info("git_validate_preconditions", branch=self._branch_name)
        # Simulated: branch is available and user has write permissions
        return True

    def execute(self) -> Any:
        """Create branch, apply patch, open PR (simulated)."""
        _load_types()
        logger.info("git_execute", branch=self._branch_name, platform=self.platform)
        self._pr_id = f"PR-{uuid4().hex[:6]}"
        result = _ExecutionResult(
            status=_ExecutionStatus.COMPLETED,
            adapter_type="git",
            completed_at=datetime.utcnow(),
            output={
                "platform": self.platform,
                "branch": self._branch_name,
                "pr_id": self._pr_id,
                "action_id": str(self._action.action_id) if self._action else "",
                "message": "Branch created, patch applied, PR opened (simulated)",
            },
            rollback_available=True,
        )
        logger.info("git_execute_done", pr_id=self._pr_id)
        return result

    def verify_result(self) -> bool:
        """Check PR was created successfully (simulated)."""
        _load_types()
        ok = bool(self._pr_id)
        logger.info("git_verify", pr_id=self._pr_id, verified=ok)
        return ok

    def rollback(self) -> bool:
        """Close PR and delete branch (simulated)."""
        _load_types()
        logger.info("git_rollback", pr_id=self._pr_id, branch=self._branch_name)
        self._pr_id = ""
        self._branch_name = ""
        return True
