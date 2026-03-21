"""Hypothesis aggregator — merges and deduplicates hypotheses."""

from __future__ import annotations

import structlog

from src.core.derived import DerivedFact

log = structlog.get_logger(__name__)

_DEFAULT_TOP_N: int = 10


class StructuralDeduplicator:
    """Deduplicates hypotheses by grouping on ``suspected_entity``.

    When multiple hypotheses share the same suspected entity the duplicator
    merges them: confidences are combined (max), justification supporting
    facts are unioned, and strategy names are concatenated.
    """

    def deduplicate(self, hypotheses: list[DerivedFact]) -> list[DerivedFact]:
        groups: dict[str, list[DerivedFact]] = {}
        for h in hypotheses:
            key = h.payload.get("suspected_entity", str(h.derived_id))
            groups.setdefault(key, []).append(h)

        merged: list[DerivedFact] = []
        for key, group in groups.items():
            if len(group) == 1:
                merged.append(group[0])
                continue

            # Pick the one with highest confidence as base.
            group.sort(key=lambda h: h.confidence, reverse=True)
            best = group[0].model_copy(deep=True)

            # Merge supporting facts and strategies from duplicates.
            strategies: set[str] = {best.payload.get("strategy", "")}
            for other in group[1:]:
                best.justification.supporting_facts |= (
                    other.justification.supporting_facts
                )
                strategies.add(other.payload.get("strategy", ""))
                # Combine confidence via max.
                if other.confidence > best.confidence:
                    best.confidence = other.confidence

            best.payload["merged_strategies"] = sorted(strategies - {""})
            best.payload["merge_count"] = len(group)
            merged.append(best)

        log.debug(
            "deduplicator.done",
            before=len(hypotheses),
            after=len(merged),
        )
        return merged


class HypothesisAggregator:
    """Aggregates hypotheses from multiple strategies into a unified list.

    Pipeline:
    1. Deduplicate by suspected entity.
    2. Sort by confidence descending.
    3. Return top-N results.
    """

    def __init__(self, top_n: int = _DEFAULT_TOP_N) -> None:
        self._top_n = top_n
        self._deduplicator = StructuralDeduplicator()

    def aggregate(self, hypotheses: list[DerivedFact]) -> list[DerivedFact]:
        """Merge, deduplicate, and re-rank hypotheses.

        Args:
            hypotheses: Raw hypotheses collected from all strategies.

        Returns:
            Deduplicated and ranked list of hypotheses (at most top_n).
        """
        if not hypotheses:
            return []

        deduped = self._deduplicator.deduplicate(hypotheses)
        deduped.sort(key=lambda h: h.confidence, reverse=True)
        result = deduped[: self._top_n]

        log.info(
            "aggregator.done",
            input_count=len(hypotheses),
            deduped_count=len(deduped),
            output_count=len(result),
        )
        return result
