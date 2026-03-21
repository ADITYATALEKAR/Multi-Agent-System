"""Temporal strategy — generates hypotheses from temporal event correlations."""

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

# Entities with attention scores above this threshold are considered "recently changed".
_RECENCY_THRESHOLD: float = 0.5


class TemporalStrategy(HypothesisStrategy):
    """Derives hypotheses by correlating violations with recent changes.

    Uses attention scores from the context as a proxy for recency: entities
    with high attention scores are considered recently changed and are more
    likely to be the root cause of nearby violations.
    """

    STRATEGY_ID: str = "temporal"
    PRIORITY: int = 4

    def __init__(self, recency_threshold: float = _RECENCY_THRESHOLD) -> None:
        self._recency_threshold = recency_threshold

    async def generate(
        self,
        violations: list[DerivedFact],
        context: HypothesisContext,
    ) -> list[DerivedFact]:
        hypotheses: list[DerivedFact] = []

        if not context.attention_scores:
            log.debug("temporal.skipped", reason="no_attention_scores")
            return hypotheses

        # Identify recently-changed entities.
        recent_entities = {
            uid: score
            for uid, score in context.attention_scores.items()
            if score >= self._recency_threshold
        }

        if not recent_entities:
            log.debug("temporal.skipped", reason="no_recent_entities")
            return hypotheses

        for violation in violations:
            entity_id_str = violation.payload.get(
                "subject_id", violation.payload.get("entity_id")
            )
            if entity_id_str is None:
                continue

            for recent_uid, attention_score in recent_entities.items():
                confidence = min(violation.confidence * attention_score * 0.8, 1.0)
                hypothesis = DerivedFact(
                    derived_type=DerivedType.HYPOTHESIS,
                    payload={
                        "violation_id": str(violation.derived_id),
                        "suspected_entity": str(recent_uid),
                        "reasoning": (
                            f"Entity '{recent_uid}' was recently changed "
                            f"(attention={attention_score:.2f}) and is near the "
                            f"violation — it may be the root cause."
                        ),
                        "attention_score": attention_score,
                        "strategy": self.STRATEGY_ID,
                    },
                    justification=ExtendedJustification(
                        rule_id=violation.justification.rule_id,
                        supporting_facts={violation.derived_id},
                        source_strategy=self.STRATEGY_ID,
                    ),
                    status=DerivedStatus.UNKNOWN,
                    confidence=confidence,
                    confidence_sources=[
                        ConfidenceContribution(
                            source=ConfidenceSource.EVIDENCE,
                            weight=confidence,
                            detail=(
                                f"Temporal proximity — attention "
                                f"score {attention_score:.2f}"
                            ),
                        ),
                    ],
                )
                hypotheses.append(hypothesis)

        log.debug(
            "temporal.generated",
            count=len(hypotheses),
            recent_entity_count=len(recent_entities),
        )
        return hypotheses
