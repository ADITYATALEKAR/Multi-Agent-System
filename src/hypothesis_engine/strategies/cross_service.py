"""Cross-service strategy — generates hypotheses spanning service boundaries."""

from __future__ import annotations

import structlog

from src.core.derived import (
    ConfidenceContribution,
    ConfidenceSource,
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)
from src.hypothesis_engine.base import HypothesisContext, HypothesisStrategy

log = structlog.get_logger(__name__)


def _extract_service(violation: DerivedFact) -> str | None:
    """Return the service name from a violation's payload, if present."""
    return violation.payload.get("service") or violation.payload.get("service_name")


class CrossServiceStrategy(HypothesisStrategy):
    """Derives hypotheses that span multiple services or components.

    Groups violations by service boundary and hypothesises that when
    violations appear in multiple services simultaneously, an upstream
    cross-service dependency is the root cause.
    """

    STRATEGY_ID: str = "cross_service"
    PRIORITY: int = 3

    async def generate(
        self,
        violations: list[DerivedFact],
        context: HypothesisContext,
    ) -> list[DerivedFact]:
        hypotheses: list[DerivedFact] = []

        # Group violations by service.
        service_map: dict[str, list[DerivedFact]] = {}
        for v in violations:
            svc = _extract_service(v)
            if svc:
                service_map.setdefault(svc, []).append(v)

        # Only produce hypotheses when violations span more than one service.
        if len(service_map) < 2:
            log.debug(
                "cross_service.skipped",
                reason="single_or_no_service",
                service_count=len(service_map),
            )
            return hypotheses

        services_involved = sorted(service_map.keys())
        all_violation_ids = {v.derived_id for v in violations if _extract_service(v)}

        # Build pairwise cross-service hypotheses.
        for i, svc_a in enumerate(services_involved):
            for svc_b in services_involved[i + 1 :]:
                combined_violations = service_map[svc_a] + service_map[svc_b]
                avg_confidence = (
                    sum(v.confidence for v in combined_violations)
                    / len(combined_violations)
                )
                confidence = min(avg_confidence * 0.7, 1.0)

                hypothesis = DerivedFact(
                    derived_type=DerivedType.HYPOTHESIS,
                    payload={
                        "violation_ids": [str(v.derived_id) for v in combined_violations],
                        "suspected_entity": f"cross_service:{svc_a}<->{svc_b}",
                        "reasoning": (
                            f"Violations found in both '{svc_a}' and '{svc_b}' — "
                            f"a shared cross-service dependency may be the root cause."
                        ),
                        "services": [svc_a, svc_b],
                        "strategy": self.STRATEGY_ID,
                    },
                    justification=ExtendedJustification(
                        rule_id="cross_service_correlation",
                        supporting_facts={v.derived_id for v in combined_violations},
                        source_strategy=self.STRATEGY_ID,
                    ),
                    status=DerivedStatus.UNKNOWN,
                    confidence=confidence,
                    confidence_sources=[
                        ConfidenceContribution(
                            source=ConfidenceSource.EVIDENCE,
                            weight=confidence,
                            detail=f"Cross-service correlation between {svc_a} and {svc_b}",
                        ),
                    ],
                )
                hypotheses.append(hypothesis)

        log.debug(
            "cross_service.generated",
            count=len(hypotheses),
            services=services_involved,
        )
        return hypotheses
