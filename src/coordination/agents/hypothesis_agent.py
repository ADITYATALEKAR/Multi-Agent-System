"""HypothesisAgent generates hypotheses from violations and anomalies."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

import structlog

from src.coordination.agents.base import BaseAgent
from src.core.derived import DerivedFact, DerivedStatus, DerivedType, ExtendedJustification
from src.hypothesis_engine.base import HypothesisContext

if TYPE_CHECKING:
    from src.core.coordination import WorkItem

logger = structlog.get_logger(__name__)


class HypothesisAgent(BaseAgent):
    """Specialist agent for hypothesis generation, anomaly correlation, and pattern matching."""

    AGENT_ID: str = "hypothesis"
    CAPABILITIES: set[str] = {"hypothesis_generate", "anomaly_correlate", "pattern_match"}

    def execute(self, item: WorkItem) -> Any:
        """Generate hypotheses from violations or anomalies in the payload."""
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
            from src.hypothesis_engine.generator import HypothesisGenerator

            derived_violations = [self._to_derived_violation(raw) for raw in violations]
            if not derived_violations and anomalies:
                raise ValueError("No structured violations available for hypothesis generation.")

            generator = HypothesisGenerator()
            hypotheses = asyncio.run(
                generator.generate(
                    violations=derived_violations,
                    context=HypothesisContext(),
                )
            )
            count = len(hypotheses) if hypotheses else 0
            logger.info("hypothesis.generated", hypotheses_generated=count)
            return {
                "hypotheses_generated": count,
                "hypotheses": [self._serialize_hypothesis(hypothesis) for hypothesis in hypotheses],
            }
        except (ImportError, Exception) as exc:
            logger.warning("hypothesis.fallback", reason=str(exc))
            items: list[dict[str, str]] = []
            for violation in violations:
                rule = str(violation.get("rule", "unknown_rule"))
                message = str(violation.get("message", ""))
                items.append(
                    {
                        "title": f"Hypothesis for {rule}",
                        "summary": (
                            f"{message} This likely impacts maintainability or delivery confidence."
                        ),
                    }
                )
            if not items and anomalies:
                items.append(
                    {
                        "title": "Anomaly correlation candidate",
                        "summary": (
                            "Observed anomalies should be correlated with "
                            "recent repository changes."
                        ),
                    }
                )
            return {"hypotheses_generated": len(items), "hypotheses": items}

    def _estimate_time(self, item: WorkItem) -> float:  # noqa: ARG002
        return 3.0

    def _to_derived_violation(self, violation: dict[str, Any]) -> DerivedFact:
        rule = str(violation.get("rule", "unknown_rule"))
        file_path = str(violation.get("file_path", ""))
        entity_id = str(violation.get("entity_id") or uuid5(NAMESPACE_URL, file_path or rule))
        severity = str(violation.get("severity", "medium"))
        payload = {
            "rule_id": rule,
            "entity_id": entity_id,
            "message": str(violation.get("message", "")),
            "file_path": file_path,
            "severity": severity,
        }
        return DerivedFact(
            derived_type=DerivedType.VIOLATION,
            payload=payload,
            justification=ExtendedJustification(
                rule_id=rule,
                source_strategy="law_engine_agent",
            ),
            status=DerivedStatus.SUPPORTED,
            confidence=self._severity_confidence(severity),
        )

    def _serialize_hypothesis(self, hypothesis: DerivedFact) -> dict[str, str]:
        payload = hypothesis.payload if isinstance(hypothesis.payload, dict) else {}
        strategy = str(payload.get("strategy", "generator")).replace("_", " ")
        reasoning = str(payload.get("reasoning", "")).strip()
        return {
            "title": f"Hypothesis via {strategy.title()}",
            "summary": (
                reasoning
                or "MAS generated a likely root-cause hypothesis from the current violations."
            ),
        }

    def _severity_confidence(self, severity: str) -> float:
        return {
            "critical": 0.95,
            "high": 0.9,
            "medium": 0.8,
            "low": 0.65,
        }.get(severity.lower(), 0.75)
