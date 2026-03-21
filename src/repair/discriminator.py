"""Repair discriminator: DeltaDebugger + SBFLRanker for fault localization.

DeltaDebugger: Binary-search-based delta debugging to minimize failure-inducing changes.
SBFLRanker: Spectrum-Based Fault Localization using Ochiai/Tarantula metrics.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Callable, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from src.core.derived import DerivedFact, DerivedType
from src.core.fact import GraphDelta

logger = structlog.get_logger()


class SuspiciousnessScore(BaseModel):
    """Fault localization score for a component."""

    entity_id: UUID
    ochiai: float = 0.0
    tarantula: float = 0.0
    combined: float = 0.0


class DeltaDebugger:
    """Binary-search delta debugging to minimize failure-inducing change sets.

    Given a set of deltas that cause a violation, finds the minimal subset
    that still triggers the violation.
    """

    def __init__(self, max_iterations: int = 20) -> None:
        self._max_iterations = max_iterations

    def minimize(
        self,
        deltas: list[GraphDelta],
        test_fn: Callable[[list[GraphDelta]], bool],
    ) -> list[GraphDelta]:
        """Find minimal subset of deltas that still triggers the failure.

        Args:
            deltas: Full set of deltas that cause the failure.
            test_fn: Function that returns True if the given deltas cause failure.

        Returns:
            Minimal failing subset.
        """
        if len(deltas) <= 1:
            return deltas

        # Verify full set fails
        if not test_fn(deltas):
            return deltas  # Can't minimize if full set doesn't fail

        n = 2
        iteration = 0

        while len(deltas) > 1 and iteration < self._max_iterations:
            iteration += 1
            chunk_size = max(1, len(deltas) // n)
            chunks = [deltas[i : i + chunk_size] for i in range(0, len(deltas), chunk_size)]

            found_smaller = False

            # Test each chunk
            for chunk in chunks:
                if test_fn(chunk):
                    deltas = chunk
                    n = 2
                    found_smaller = True
                    break

            if not found_smaller:
                # Test complements
                for i, chunk in enumerate(chunks):
                    complement = [d for j, c in enumerate(chunks) for d in c if j != i]
                    if test_fn(complement):
                        deltas = complement
                        n = max(2, n - 1)
                        found_smaller = True
                        break

            if not found_smaller:
                if n >= len(deltas):
                    break
                n = min(2 * n, len(deltas))

        logger.debug("delta_debug_result", iterations=iteration, result_size=len(deltas))
        return deltas

    def isolate_failing_operations(
        self,
        delta: GraphDelta,
        test_fn: Callable[[list], bool],
    ) -> list:
        """Isolate failing operations within a single delta."""
        ops = list(delta.operations)
        if len(ops) <= 1:
            return ops

        def wrap_test(op_subset: list) -> bool:
            return test_fn(op_subset)

        # Use same ddmin approach on operations
        return self._ddmin_list(ops, wrap_test)

    def _ddmin_list(
        self, items: list, test_fn: Callable[[list], bool], max_iter: int = 20
    ) -> list:
        if len(items) <= 1:
            return items
        if not test_fn(items):
            return items

        n = 2
        iteration = 0
        while len(items) > 1 and iteration < max_iter:
            iteration += 1
            chunk_size = max(1, len(items) // n)
            chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]

            found = False
            for chunk in chunks:
                if test_fn(chunk):
                    items = chunk
                    n = 2
                    found = True
                    break

            if not found:
                for i in range(len(chunks)):
                    complement = [x for j, c in enumerate(chunks) for x in c if j != i]
                    if test_fn(complement):
                        items = complement
                        n = max(2, n - 1)
                        found = True
                        break

            if not found:
                if n >= len(items):
                    break
                n = min(2 * n, len(items))

        return items


class SBFLRanker:
    """Spectrum-Based Fault Localization using Ochiai and Tarantula metrics.

    Records which entities are involved in passing and failing test scenarios,
    then ranks entities by suspiciousness.
    """

    def __init__(self) -> None:
        # entity_id -> counts
        self._entity_pass: dict[UUID, int] = defaultdict(int)
        self._entity_fail: dict[UUID, int] = defaultdict(int)
        self._total_pass: int = 0
        self._total_fail: int = 0

    def record_passing(self, involved_entities: set[UUID]) -> None:
        """Record a passing test scenario and the entities involved."""
        self._total_pass += 1
        for eid in involved_entities:
            self._entity_pass[eid] += 1

    def record_failing(self, involved_entities: set[UUID]) -> None:
        """Record a failing test scenario and the entities involved."""
        self._total_fail += 1
        for eid in involved_entities:
            self._entity_fail[eid] += 1

    def rank(self) -> list[SuspiciousnessScore]:
        """Rank all entities by suspiciousness score.

        Returns scores sorted by combined score descending.
        """
        all_entities = set(self._entity_pass.keys()) | set(self._entity_fail.keys())
        scores: list[SuspiciousnessScore] = []

        for eid in all_entities:
            ef = self._entity_fail.get(eid, 0)  # failed tests involving entity
            ep = self._entity_pass.get(eid, 0)  # passed tests involving entity
            nf = self._total_fail - ef  # failed tests NOT involving entity
            np_ = self._total_pass - ep  # passed tests NOT involving entity

            ochiai = self._ochiai(ef, ep, nf)
            tarantula = self._tarantula(ef, ep, nf, np_)
            combined = 0.6 * ochiai + 0.4 * tarantula

            scores.append(
                SuspiciousnessScore(
                    entity_id=eid,
                    ochiai=ochiai,
                    tarantula=tarantula,
                    combined=combined,
                )
            )

        scores.sort(key=lambda s: s.combined, reverse=True)
        return scores

    def rank_for_violations(
        self, violations: list[DerivedFact], passing_entities: set[UUID]
    ) -> list[SuspiciousnessScore]:
        """Convenience: record violations as failing, passing set as passing, then rank."""
        failing_entities: set[UUID] = set()
        for v in violations:
            entity_id = v.payload.get("entity_id")
            if entity_id:
                if isinstance(entity_id, str):
                    failing_entities.add(UUID(entity_id))
                else:
                    failing_entities.add(entity_id)

        self.record_failing(failing_entities)
        self.record_passing(passing_entities)
        return self.rank()

    @staticmethod
    def _ochiai(ef: int, ep: int, nf: int) -> float:
        """Ochiai suspiciousness: ef / sqrt((ef + nf) * (ef + ep))."""
        total_fail = ef + nf
        total_entity = ef + ep
        denominator = math.sqrt(total_fail * total_entity) if (total_fail * total_entity) > 0 else 0
        return ef / denominator if denominator > 0 else 0.0

    @staticmethod
    def _tarantula(ef: int, ep: int, nf: int, np_: int) -> float:
        """Tarantula suspiciousness: (ef/total_fail) / (ef/total_fail + ep/total_pass)."""
        total_fail = ef + nf
        total_pass = ep + np_
        if total_fail == 0:
            return 0.0
        fail_ratio = ef / total_fail
        pass_ratio = ep / total_pass if total_pass > 0 else 0.0
        denominator = fail_ratio + pass_ratio
        return fail_ratio / denominator if denominator > 0 else 0.0
