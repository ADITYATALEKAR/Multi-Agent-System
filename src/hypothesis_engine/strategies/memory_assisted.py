"""Memory-assisted strategy — generates hypotheses using episodic memory.

Phase 2 stub: returns an empty list.  Real implementation arrives in Phase 3.
"""

from __future__ import annotations

import structlog

from src.core.derived import DerivedFact
from src.hypothesis_engine.base import HypothesisContext, HypothesisStrategy

log = structlog.get_logger(__name__)


class MemoryAssistedStrategy(HypothesisStrategy):
    """Derives hypotheses by recalling similar past incidents from memory.

    Phase 2 stub — returns an empty list.  The full episodic-memory lookup
    will be implemented in Phase 3.
    """

    STRATEGY_ID: str = "memory_assisted"
    PRIORITY: int = 5

    async def generate(
        self,
        violations: list[DerivedFact],
        context: HypothesisContext,
    ) -> list[DerivedFact]:
        log.debug("memory_assisted.stub", msg="Phase 3 — returning empty list")
        return []
