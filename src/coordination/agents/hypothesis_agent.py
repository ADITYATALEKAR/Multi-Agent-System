"""HypothesisAgent — generates hypotheses from violations / anomalies (v3.3 D4 split).

Uses src.hypothesis.generator.HypothesisGenerator when available; falls back to stub.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class HypothesisAgent(BaseAgent):
    """Specialist agent for hypothesis generation, anomaly correlation, and pattern matching."""

    AGENT_ID: str = "hypothesis"
    CAPABILITIES: set[str] = {"hypothesis_generate", "anomaly_correlate", "pattern_match"}

    def execute(self, item: WorkItem) -> Any:
        """Generate hypotheses from violations or anomalies in the payload.

        Tries the real HypothesisGenerator; returns stub on ImportError.
        """
        logger.info(
            "hypothesis.execute",
            agent_id=self._agent_id,
            task_type=item.task_type,
        )
        self.heartbeat()

        payload = getattr(item, "payload", {}) or {}
        violations = payload.get("violations", []) if isinstance(payload, dict) else []
        anomalies = payload.get("anomalies", []) if isinstance(payload, dict) else []

        try:
            from src.hypothesis.generator import HypothesisGenerator

            gen = HypothesisGenerator()
            hypotheses = gen.generate(violations=violations, anomalies=anomalies)
            count = len(hypotheses) if hypotheses else 0
            logger.info("hypothesis.generated", hypotheses_generated=count)
            return {"hypotheses_generated": count}
        except (ImportError, Exception) as exc:
            logger.warning("hypothesis.fallback", reason=str(exc))
            items: list[dict[str, str]] = []
            for violation in violations:
                rule = str(violation.get("rule", "unknown_rule"))
                message = str(violation.get("message", ""))
                items.append({
                    "title": f"Hypothesis for {rule}",
                    "summary": f"{message} This likely impacts maintainability or delivery confidence.",
                })
            if not items and anomalies:
                items.append({
                    "title": "Anomaly correlation candidate",
                    "summary": "Observed anomalies should be correlated with recent repository changes.",
                })
            return {"hypotheses_generated": len(items), "hypotheses": items}

    # ------------------------------------------------------------------
    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 3.0
