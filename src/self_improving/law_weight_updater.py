"""Law weight updating based on diagnostic outcomes."""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)


class LawWeightUpdater:
    """Adjusts law weights using a simple online learning rule.

    Correct violations increase the law's weight (reinforcing useful rules);
    false positives decrease it.  Weights are clamped to
    ``[min_weight, max_weight]``.
    """

    def __init__(
        self,
        learning_rate: float = 0.01,
        min_weight: float = 0.1,
        max_weight: float = 2.0,
    ) -> None:
        self._lr = learning_rate
        self._min = min_weight
        self._max = max_weight

    # ── Single update ─────────────────────────────────────────────────────

    def update(
        self,
        law_id: str,
        was_correct: bool,
        current_weight: float,
    ) -> float:
        """Return the updated weight for *law_id*.

        Args:
            law_id: Identifier of the law / rule.
            was_correct: True if the violation was a true positive.
            current_weight: The law's current weight.

        Returns:
            New weight, clamped to [min_weight, max_weight].
        """
        if was_correct:
            new_weight = current_weight + self._lr
        else:
            new_weight = current_weight - self._lr

        new_weight = max(self._min, min(self._max, new_weight))

        log.debug(
            "law_weight.updated",
            law_id=law_id,
            was_correct=was_correct,
            old_weight=current_weight,
            new_weight=new_weight,
        )
        return new_weight

    # ── Batch update ──────────────────────────────────────────────────────

    def batch_update(
        self,
        outcomes: list[tuple[str, bool, float]],
    ) -> dict[str, float]:
        """Apply updates for many laws at once.

        Args:
            outcomes: List of ``(law_id, was_correct, current_weight)`` tuples.

        Returns:
            Mapping of law_id -> new weight.
        """
        results: dict[str, float] = {}
        for law_id, was_correct, current_weight in outcomes:
            results[law_id] = self.update(law_id, was_correct, current_weight)
        return results
