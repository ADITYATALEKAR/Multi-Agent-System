"""InfraOpsAgent — infrastructure operations stub.

No external component dependency; provides resource monitoring and scaling stubs.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class InfraOpsAgent(BaseAgent):
    """Specialist agent for infrastructure operations, resource monitoring, and scaling."""

    AGENT_ID: str = "infra_ops"
    CAPABILITIES: set[str] = {"infra_ops", "resource_monitor", "scaling_adjust"}

    def execute(self, item: WorkItem) -> Any:
        """Perform infrastructure operations (stub).

        Returns a summary dict describing the operation performed.
        """
        logger.info(
            "infra_ops.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
        )
        self.heartbeat()

        payload = getattr(item, "payload", {}) or {}
        operation = (
            payload.get("operation", "health_check")
            if isinstance(payload, dict)
            else "health_check"
        )

        logger.info("infra_ops.complete", operation=operation)
        return {"operation": operation}

    # ------------------------------------------------------------------
    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 2.0
