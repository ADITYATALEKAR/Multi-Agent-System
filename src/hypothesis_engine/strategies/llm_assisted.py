"""LLM-assisted strategy — generates hypotheses with large language model aid.

Phase 2 stub: returns an empty list.  The interface is defined here so that
downstream consumers can depend on it, but the real LLM call is deferred.
"""

from __future__ import annotations

import structlog

from src.core.derived import DerivedFact
from src.hypothesis_engine.base import HypothesisContext, HypothesisStrategy

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT_S: float = 30.0


class LLMAssistedStrategy(HypothesisStrategy):
    """Derives hypotheses by prompting an LLM with structured context.

    Phase 2 stub — returns an empty list.  The full LLM-based reasoning
    pipeline will be implemented in a later phase.
    """

    STRATEGY_ID: str = "llm_assisted"
    PRIORITY: int = 6

    def __init__(self, timeout_s: float = _DEFAULT_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    async def generate(
        self,
        violations: list[DerivedFact],
        context: HypothesisContext,
    ) -> list[DerivedFact]:
        log.debug(
            "llm_assisted.stub",
            msg="Phase 2 stub — returning empty list",
            timeout_s=self._timeout_s,
        )
        return []
