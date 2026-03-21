"""Hypothesis aggregator -- runs strategies, deduplicates, and ranks results."""

from __future__ import annotations

from typing import Any
from difflib import SequenceMatcher

import structlog

from src.core.derived import DerivedFact
from src.hypothesis.hypothesis import Hypothesis, HypothesisStatus
from src.hypothesis.strategies import BaseStrategy

log = structlog.get_logger(__name__)

_DEFAULT_TOP_K: int = 10
_SIMILARITY_THRESHOLD: float = 0.75


class HypothesisAggregator:
    """Run multiple strategies, deduplicate, and return top-K hypotheses.

    Pipeline:
        1. Execute each strategy against the provided violations.
        2. Collect all hypotheses into a single pool.
        3. Deduplicate / merge hypotheses with similar descriptions.
        4. Sort by confidence (descending).
        5. Return the top *K* results.

    Args:
        strategies: List of :class:`BaseStrategy` instances to execute.
        top_k: Maximum number of hypotheses to return (default 10).
        similarity_threshold: Description-similarity ratio above which two
            hypotheses are considered duplicates and merged (default 0.75).
    """

    def __init__(
        self,
        strategies: list[BaseStrategy],
        top_k: int = _DEFAULT_TOP_K,
        similarity_threshold: float = _SIMILARITY_THRESHOLD,
    ) -> None:
        self._strategies = list(strategies)
        self._top_k = top_k
        self._similarity_threshold = similarity_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def aggregate(
        self,
        violations: list[DerivedFact],
        graph_context: dict[str, Any] | None = None,
    ) -> list[Hypothesis]:
        """Run all strategies, merge duplicates, and return ranked hypotheses.

        Args:
            violations: DerivedFact objects with ``derived_type == VIOLATION``.
            graph_context: Optional graph / topology context forwarded to each
                strategy.

        Returns:
            At most *top_k* :class:`Hypothesis` objects sorted by confidence
            descending.
        """
        ctx = graph_context if graph_context is not None else {}

        raw: list[Hypothesis] = []
        for strategy in self._strategies:
            try:
                results = strategy.generate(violations, ctx)
                raw.extend(results)
                log.debug(
                    "aggregator.strategy_done",
                    strategy=strategy.STRATEGY_ID,
                    count=len(results),
                )
            except Exception:
                log.exception(
                    "aggregator.strategy_error",
                    strategy=strategy.STRATEGY_ID,
                )

        if not raw:
            log.info("aggregator.done", input_count=0, output_count=0)
            return []

        merged = self._deduplicate(raw)
        merged.sort(key=lambda h: h.confidence, reverse=True)
        result = merged[: self._top_k]

        log.info(
            "aggregator.done",
            input_count=len(raw),
            deduped_count=len(merged),
            output_count=len(result),
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _deduplicate(self, hypotheses: list[Hypothesis]) -> list[Hypothesis]:
        """Merge hypotheses with similar descriptions.

        Two hypotheses are considered duplicates when:
        - They share the same ``strategy_id`` **and** their descriptions have a
          ``SequenceMatcher`` ratio above *similarity_threshold*, **or**
        - Their ``supporting_evidence`` sets overlap by more than 50%.

        When merging, the higher-confidence hypothesis is kept, evidence is
        unioned, and the merged hypothesis status is set to MERGED.
        """
        if len(hypotheses) <= 1:
            return list(hypotheses)

        merged: list[Hypothesis] = []
        consumed: set[int] = set()

        for i, h_a in enumerate(hypotheses):
            if i in consumed:
                continue

            # Collect all hypotheses that should merge into h_a.
            group_evidence: list[list[int]] = []
            for j in range(i + 1, len(hypotheses)):
                if j in consumed:
                    continue
                h_b = hypotheses[j]
                if self._should_merge(h_a, h_b):
                    group_evidence.append(j)

            if not group_evidence:
                merged.append(h_a)
                continue

            # Merge: keep best confidence, union evidence.
            best = h_a.model_copy(deep=True)
            all_evidence_set: set[Any] = set(best.supporting_evidence)
            best_confidence = best.confidence

            for j in group_evidence:
                consumed.add(j)
                other = hypotheses[j]
                all_evidence_set.update(other.supporting_evidence)
                if other.confidence > best_confidence:
                    best_confidence = other.confidence
                    best = other.model_copy(deep=True)

            best.supporting_evidence = list(all_evidence_set)
            best.confidence = best_confidence
            best.status = HypothesisStatus.MERGED
            merged.append(best)

        log.debug(
            "aggregator.deduplicate",
            before=len(hypotheses),
            after=len(merged),
        )
        return merged

    def _should_merge(self, h_a: Hypothesis, h_b: Hypothesis) -> bool:
        """Decide whether two hypotheses are duplicates and should merge."""
        # Same strategy + similar description.
        if h_a.strategy_id == h_b.strategy_id:
            ratio = SequenceMatcher(
                None, h_a.description, h_b.description
            ).ratio()
            if ratio >= self._similarity_threshold:
                return True

        # High evidence overlap regardless of strategy.
        set_a = set(h_a.supporting_evidence)
        set_b = set(h_b.supporting_evidence)
        if set_a and set_b:
            overlap = len(set_a & set_b)
            smaller = min(len(set_a), len(set_b))
            if smaller > 0 and overlap / smaller > 0.5:
                return True

        return False
