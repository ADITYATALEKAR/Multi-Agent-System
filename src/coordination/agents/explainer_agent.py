"""ExplainerAgent — generates human-readable explanations.

No heavy external dependency; summarises analysis results for end-users.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class ExplainerAgent(BaseAgent):
    """Specialist agent for explanation generation, summarisation, and report generation."""

    AGENT_ID: str = "explainer"
    CAPABILITIES: set[str] = {"explain", "summarize", "report_generate"}

    def execute(self, item: WorkItem) -> Any:
        """Generate a human-readable explanation of analysis results.

        Builds explanation text from the work-item payload/scope.
        """
        logger.info(
            "explainer.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
        )
        self.heartbeat()

        payload = getattr(item, "payload", {}) or {}
        context = payload.get("context", {}) if isinstance(payload, dict) else {}

        summary_parts: list[str] = []
        summary_parts.append(f"Task: {item.task_type}")
        if item.scope:
            summary_parts.append(f"Scope size: {len(item.scope)} node(s)")
        if context:
            summary_parts.append(f"Context keys: {', '.join(str(k) for k in context)}")

        explanation = "; ".join(summary_parts) if summary_parts else "No data to explain."
        logger.info("explainer.complete", explanation_length=len(explanation))
        return {"explanation": explanation}

    # ------------------------------------------------------------------
    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 2.5
