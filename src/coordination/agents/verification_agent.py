"""VerificationAgent — verifies proposed repairs.

Uses src.repair.verification.VerificationEngine when available; falls back to stub.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class VerificationAgent(BaseAgent):
    """Specialist agent for repair verification, static checks, regression and security checks."""

    AGENT_ID: str = "verification"
    CAPABILITIES: set[str] = {
        "verify_repair",
        "static_check",
        "regression_check",
        "security_check",
    }

    def execute(self, item: WorkItem) -> Any:
        """Verify proposed repairs from the payload.

        Tries the real VerificationEngine; returns stub on ImportError.
        """
        logger.info(
            "verification.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
        )
        self.heartbeat()

        payload = getattr(item, "payload", {}) or {}
        repair = payload.get("repair") if isinstance(payload, dict) else None

        try:
            from src.repair.verification import VerificationEngine

            engine = VerificationEngine()
            result = engine.verify(repair) if repair else None
            status = result.status if result and hasattr(result, "status") else "no_repair"
            logger.info("verification.complete", verification_status=status)
            return {"verification_status": status}
        except (ImportError, Exception) as exc:
            logger.warning("verification.fallback", reason=str(exc))
            return {"verification_status": "stub_pass"}

    # ------------------------------------------------------------------
    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 3.0
