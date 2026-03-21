"""Law-local strategy — generates hypotheses from local causal laws."""

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


class LawLocalStrategy(HypothesisStrategy):
    """Derives hypotheses by examining the rule that produced each violation.

    For every violation the strategy inspects its justification's ``rule_id``
    and the entities referenced in its payload to construct a root-cause
    hypothesis local to that rule.
    """

    STRATEGY_ID: str = "law_local"
    PRIORITY: int = 1

    async def generate(
        self,
        violations: list[DerivedFact],
        context: HypothesisContext,
    ) -> list[DerivedFact]:
        hypotheses: list[DerivedFact] = []

        for violation in violations:
            rule_id = violation.justification.rule_id
            suspected_entity = violation.payload.get(
                "subject_id", violation.payload.get("entity_id", str(violation.derived_id))
            )
            reasoning = (
                f"Violation produced by rule '{rule_id}' — the entity "
                f"'{suspected_entity}' directly violates this rule and is the "
                f"most likely local root cause."
            )

            hypothesis = DerivedFact(
                derived_type=DerivedType.HYPOTHESIS,
                payload={
                    "violation_id": str(violation.derived_id),
                    "suspected_entity": str(suspected_entity),
                    "reasoning": reasoning,
                    "strategy": self.STRATEGY_ID,
                },
                justification=ExtendedJustification(
                    rule_id=rule_id,
                    supporting_facts={violation.derived_id},
                    source_strategy=self.STRATEGY_ID,
                ),
                status=DerivedStatus.UNKNOWN,
                confidence=min(violation.confidence * 0.9, 1.0),
                confidence_sources=[
                    ConfidenceContribution(
                        source=ConfidenceSource.EVIDENCE,
                        weight=min(violation.confidence * 0.9, 1.0),
                        detail=f"Derived from rule '{rule_id}' violation",
                    ),
                ],
            )
            hypotheses.append(hypothesis)

        log.debug(
            "law_local.generated",
            count=len(hypotheses),
            violation_count=len(violations),
        )
        return hypotheses
