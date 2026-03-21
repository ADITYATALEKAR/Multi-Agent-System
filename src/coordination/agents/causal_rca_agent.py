"""CausalRCAAgent — causal root-cause analysis (v3.3 D4 split).

Uses src.causal.discriminator.CausalDiscriminator when available; falls back to stub.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class CausalRCAAgent(BaseAgent):
    """Specialist agent for causal analysis, root-cause identification, and Bayesian inference."""

    AGENT_ID: str = "causal_rca"
    CAPABILITIES: set[str] = {
        "causal_analysis",
        "root_cause",
        "bayesian_inference",
        "intervention_score",
    }

    def execute(self, item: WorkItem) -> Any:
        """Perform causal root-cause analysis on hypotheses / anomalies.

        Tries the real CausalDiscriminator; returns stub on ImportError.
        """
        logger.info(
            "causal_rca.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
        )
        self.heartbeat()

        payload = getattr(item, "payload", {}) or {}
        hypotheses = payload.get("hypotheses", []) if isinstance(payload, dict) else []

        try:
            from src.causal.discriminator import CausalDiscriminator

            disc = CausalDiscriminator()
            root_causes = disc.analyse(hypotheses=hypotheses)
            causes = list(root_causes) if root_causes else []
            logger.info("causal_rca.analysed", root_causes_count=len(causes))
            return {"root_causes": causes}
        except (ImportError, Exception) as exc:
            logger.warning("causal_rca.fallback", reason=str(exc))
            return {"root_causes": []}

    # ------------------------------------------------------------------
    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 5.0
