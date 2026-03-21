"""DatabaseAdapter: adapter for database migration tools (Alembic, Flyway, Liquibase)."""
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


class DatabaseAdapter:
    """Executes database migration actions across Alembic, Flyway, and Liquibase."""

    SUPPORTED_PLATFORMS = ("alembic", "flyway", "liquibase")

    def __init__(self, platform: str = "alembic") -> None:
        if platform not in self.SUPPORTED_PLATFORMS:
            raise ValueError(f"Unsupported database platform: {platform}")
        self.platform = platform
        self._action: RepairAction | None = None
        self._migration_id: str = ""
        self._previous_revision: str = ""
        self._target_revision: str = ""
        logger.info("database_adapter_init", platform=platform)

    # ── interface ────────────────────────────────────────────────────────

    def prepare(self, action: RepairAction) -> None:
        """Store the action and record current migration revision."""
        _load_types()
        self._action = action
        self._migration_id = f"migration-{uuid4().hex[:8]}"
        self._previous_revision = action.parameters.get("previous_revision", "rev_000")
        self._target_revision = action.parameters.get("target_revision", "rev_001")
        logger.info(
            "database_prepare",
            action_id=str(action.action_id),
            migration_id=self._migration_id,
            platform=self.platform,
            previous_revision=self._previous_revision,
        )

    def validate_preconditions(self) -> bool:
        """Check database reachable and no pending migrations (simulated)."""
        _load_types()
        logger.info("database_validate_preconditions", platform=self.platform)
        return True

    def execute(self) -> Any:
        """Run database migration (simulated)."""
        _load_types()
        logger.info(
            "database_execute",
            platform=self.platform,
            migration_id=self._migration_id,
            target_revision=self._target_revision,
        )
        result = _ExecutionResult(
            status=_ExecutionStatus.COMPLETED,
            adapter_type="database",
            completed_at=datetime.utcnow(),
            output={
                "platform": self.platform,
                "migration_id": self._migration_id,
                "previous_revision": self._previous_revision,
                "target_revision": self._target_revision,
                "action_id": str(self._action.action_id) if self._action else "",
                "message": f"Migration to {self._target_revision} applied (simulated)",
            },
            rollback_available=True,
        )
        logger.info("database_execute_done", migration_id=self._migration_id)
        return result

    def verify_result(self) -> bool:
        """Check migration applied and schema matches expected state (simulated)."""
        _load_types()
        ok = bool(self._migration_id)
        logger.info("database_verify", migration_id=self._migration_id, verified=ok)
        return ok

    def rollback(self) -> bool:
        """Downgrade migration back to previous revision (simulated)."""
        _load_types()
        logger.info(
            "database_rollback",
            platform=self.platform,
            rollback_to=self._previous_revision,
        )
        self._target_revision = self._previous_revision
        return True
