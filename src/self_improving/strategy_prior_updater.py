"""Strategy prior updating based on diagnostic outcomes."""

from __future__ import annotations

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_PRIOR: float = 0.5


class StrategyPriorUpdater:
    """Maintains and adjusts per-strategy priors using a simple learning rule.

    A higher prior means the strategy is more likely to produce useful
    hypotheses and should be preferred (or given more budget).
    """

    def __init__(self, learning_rate: float = 0.01) -> None:
        self._lr = learning_rate
        self._priors: dict[str, float] = {}

    # ── Single update ─────────────────────────────────────────────────────

    def update(
        self,
        strategy_id: str,
        was_correct: bool,
        current_prior: float,
    ) -> float:
        """Return the updated prior for *strategy_id*.

        Args:
            strategy_id: Unique strategy identifier (e.g. ``"law_local"``).
            was_correct: Whether the strategy's hypothesis was correct.
            current_prior: The strategy's current prior probability.

        Returns:
            New prior, clamped to [0.01, 1.0].
        """
        if was_correct:
            new_prior = current_prior + self._lr * (1.0 - current_prior)
        else:
            new_prior = current_prior - self._lr * current_prior

        new_prior = max(0.01, min(1.0, new_prior))
        self._priors[strategy_id] = new_prior

        log.debug(
            "strategy_prior.updated",
            strategy_id=strategy_id,
            was_correct=was_correct,
            old_prior=current_prior,
            new_prior=new_prior,
        )
        return new_prior

    # ── Query ─────────────────────────────────────────────────────────────

    def get_priors(self) -> dict[str, float]:
        """Return the current prior map (strategy_id -> prior)."""
        return dict(self._priors)
