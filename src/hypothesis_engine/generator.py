"""Hypothesis generator — produces ranked hypotheses for an incident."""

from __future__ import annotations

import asyncio

import structlog

from src.core.derived import DerivedFact
from src.hypothesis_engine.base import HypothesisContext, HypothesisStrategy
from src.hypothesis_engine.strategies import ALL_STRATEGIES

log = structlog.get_logger(__name__)

_DEFAULT_PER_STRATEGY_TIMEOUT_S: float = 30.0
_EARLY_STOP_CONFIDENCE: float = 0.3


class HypothesisGenerator:
    """Generates hypotheses using multiple pluggable strategies.

    Strategies are executed in priority order (lowest PRIORITY value first).
    Cost-aware logic: if cheaper (lower-priority) strategies already produced
    a hypothesis with confidence > ``_EARLY_STOP_CONFIDENCE``, expensive
    strategies are skipped.
    """

    def __init__(
        self,
        strategies: list[HypothesisStrategy] | None = None,
        per_strategy_timeout_s: float = _DEFAULT_PER_STRATEGY_TIMEOUT_S,
        early_stop_confidence: float = _EARLY_STOP_CONFIDENCE,
    ) -> None:
        if strategies is not None:
            self._strategies = sorted(strategies, key=lambda s: s.PRIORITY)
        else:
            self._strategies = sorted(
                (cls() for cls in ALL_STRATEGIES),
                key=lambda s: s.PRIORITY,
            )
        self._per_strategy_timeout_s = per_strategy_timeout_s
        self._early_stop_confidence = early_stop_confidence

    async def generate(
        self,
        violations: list[DerivedFact],
        context: HypothesisContext,
    ) -> list[DerivedFact]:
        """Generate hypotheses for the given violations.

        Args:
            violations: DerivedFact list with derived_type == VIOLATION.
            context: Shared hypothesis context.

        Returns:
            Combined list of hypotheses from all executed strategies.
        """
        all_hypotheses: list[DerivedFact] = []
        best_confidence: float = 0.0

        for strategy in self._strategies:
            # Cost-aware: skip expensive strategies when cheap ones are sufficient.
            if (
                best_confidence > self._early_stop_confidence
                and strategy.PRIORITY >= 5
            ):
                log.info(
                    "generator.skip_expensive",
                    strategy=strategy.STRATEGY_ID,
                    best_confidence=best_confidence,
                )
                continue

            try:
                results = await asyncio.wait_for(
                    strategy.generate(violations, context),
                    timeout=self._per_strategy_timeout_s,
                )
                all_hypotheses.extend(results)

                for h in results:
                    if h.confidence > best_confidence:
                        best_confidence = h.confidence

                log.debug(
                    "generator.strategy_done",
                    strategy=strategy.STRATEGY_ID,
                    count=len(results),
                    best_confidence=best_confidence,
                )
            except asyncio.TimeoutError:
                log.warning(
                    "generator.strategy_timeout",
                    strategy=strategy.STRATEGY_ID,
                    timeout_s=self._per_strategy_timeout_s,
                )
            except Exception:
                log.exception(
                    "generator.strategy_error",
                    strategy=strategy.STRATEGY_ID,
                )

        log.info("generator.done", total_hypotheses=len(all_hypotheses))
        return all_hypotheses
