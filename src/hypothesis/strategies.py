"""Hypothesis strategies -- six pluggable generators for root-cause hypotheses.

Functional strategies (Phase 2):
    LawLocalStrategy, GraphBackwardStrategy, CrossServiceStrategy, TemporalStrategy

Phase-5 stubs (return empty lists):
    MemoryAssistedStrategy, LLMAssistedStrategy
"""

from __future__ import annotations

import abc
from collections import defaultdict
from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog

from src.core.derived import DerivedFact
from src.core.fact import GraphDelta
from src.hypothesis.hypothesis import Hypothesis

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Edge types considered dependency relationships for backward traversal
# ---------------------------------------------------------------------------
_DEPENDENCY_EDGE_TYPES: frozenset[str] = frozenset(
    {"depends_on", "imports", "calls", "uses"}
)

# ---------------------------------------------------------------------------
# Temporal window for grouping time-correlated violations
# ---------------------------------------------------------------------------
_DEFAULT_TEMPORAL_WINDOW = timedelta(seconds=60)


# ═══════════════════════════════════════════════════════════════════════════════
# Base strategy
# ═══════════════════════════════════════════════════════════════════════════════


class BaseStrategy(abc.ABC):
    """Abstract base class for all hypothesis-generation strategies.

    Every concrete strategy must set ``STRATEGY_ID`` and implement
    :meth:`generate`.
    """

    STRATEGY_ID: str

    @abc.abstractmethod
    def generate(
        self,
        violations: list[DerivedFact],
        graph_context: dict[str, Any],
    ) -> list[Hypothesis]:
        """Generate hypotheses from *violations* and *graph_context*.

        Args:
            violations: DerivedFact objects with ``derived_type == VIOLATION``.
            graph_context: Arbitrary graph / topology context keyed by string.
                Expected keys vary per strategy (e.g. ``"edges"``,
                ``"nodes"``, ``"services"``).

        Returns:
            A list of :class:`Hypothesis` objects ranked by confidence.
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LawLocalStrategy
# ═══════════════════════════════════════════════════════════════════════════════


class LawLocalStrategy(BaseStrategy):
    """Generate hypotheses by grouping violations that share the same rule.

    When multiple violations originate from the same ``rule_id`` the strategy
    treats them as symptoms of a single underlying issue and produces a
    consolidated hypothesis whose confidence scales with the group size.
    """

    STRATEGY_ID: str = "law_local"

    def generate(
        self,
        violations: list[DerivedFact],
        graph_context: dict[str, Any],
    ) -> list[Hypothesis]:
        if not violations:
            return []

        # Group violations by rule_id.
        rule_groups: dict[str, list[DerivedFact]] = defaultdict(list)
        for v in violations:
            rule_groups[v.justification.rule_id].append(v)

        hypotheses: list[Hypothesis] = []
        for rule_id, group in rule_groups.items():
            # Confidence rises with more violations from the same rule,
            # capped at 0.95.
            base_confidence = max(v.confidence for v in group)
            group_boost = min(len(group) * 0.05, 0.25)
            confidence = min(base_confidence * 0.9 + group_boost, 0.95)

            entities = _collect_entity_ids(group)
            entity_summary = ", ".join(entities[:5])
            if len(entities) > 5:
                entity_summary += f" (+{len(entities) - 5} more)"

            hypotheses.append(
                Hypothesis(
                    description=(
                        f"Rule '{rule_id}' violated by {len(group)} entity(ies) "
                        f"[{entity_summary}] -- the shared rule suggests a common "
                        f"local root cause."
                    ),
                    confidence=confidence,
                    strategy_id=self.STRATEGY_ID,
                    supporting_evidence=[v.derived_id for v in group],
                )
            )

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        log.debug(
            "law_local.generated",
            count=len(hypotheses),
            violation_count=len(violations),
        )
        return hypotheses


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GraphBackwardStrategy
# ═══════════════════════════════════════════════════════════════════════════════


class GraphBackwardStrategy(BaseStrategy):
    """Trace dependency edges backward from each violation to find root causes.

    The strategy inspects ``graph_context["edges"]`` for dependency edges
    (depends_on, imports, calls, uses) and walks one hop backward from the
    entity referenced by each violation to identify potential upstream causes.
    """

    STRATEGY_ID: str = "graph_backward"

    def generate(
        self,
        violations: list[DerivedFact],
        graph_context: dict[str, Any],
    ) -> list[Hypothesis]:
        edges: list[dict[str, Any]] = graph_context.get("edges", [])
        if not edges:
            log.debug("graph_backward.skipped", reason="no_edges_in_context")
            return []

        # Index: tgt_id -> list of (src_id, edge_type).
        incoming: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for edge in edges:
            tgt = str(edge.get("tgt_id", ""))
            src = str(edge.get("src_id", ""))
            etype = edge.get("edge_type", "")
            if etype in _DEPENDENCY_EDGE_TYPES and tgt and src:
                incoming[tgt].append((src, etype))

        hypotheses: list[Hypothesis] = []
        for violation in violations:
            entity_id = _extract_entity_id(violation)
            if entity_id is None:
                continue

            upstream_entries = incoming.get(entity_id, [])
            for src_id, edge_type in upstream_entries:
                confidence = min(violation.confidence * 0.75, 0.90)
                hypotheses.append(
                    Hypothesis(
                        description=(
                            f"Entity '{src_id}' is upstream of the violating "
                            f"entity '{entity_id}' via a '{edge_type}' edge "
                            f"and may be the root cause."
                        ),
                        confidence=confidence,
                        strategy_id=self.STRATEGY_ID,
                        supporting_evidence=[violation.derived_id],
                    )
                )

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        log.debug(
            "graph_backward.generated",
            count=len(hypotheses),
            violation_count=len(violations),
        )
        return hypotheses


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CrossServiceStrategy
# ═══════════════════════════════════════════════════════════════════════════════


class CrossServiceStrategy(BaseStrategy):
    """Look for correlated violations across different services.

    Groups violations by their ``service`` payload field and produces
    cross-service hypotheses when two or more services exhibit violations
    simultaneously.
    """

    STRATEGY_ID: str = "cross_service"

    def generate(
        self,
        violations: list[DerivedFact],
        graph_context: dict[str, Any],
    ) -> list[Hypothesis]:
        service_map: dict[str, list[DerivedFact]] = defaultdict(list)
        for v in violations:
            svc = v.payload.get("service") or v.payload.get("service_name")
            if svc:
                service_map[svc].append(v)

        if len(service_map) < 2:
            log.debug(
                "cross_service.skipped",
                reason="fewer_than_two_services",
                service_count=len(service_map),
            )
            return []

        hypotheses: list[Hypothesis] = []
        services_sorted = sorted(service_map.keys())

        for i, svc_a in enumerate(services_sorted):
            for svc_b in services_sorted[i + 1 :]:
                combined = service_map[svc_a] + service_map[svc_b]
                avg_conf = sum(v.confidence for v in combined) / len(combined)
                confidence = min(avg_conf * 0.70, 0.90)

                hypotheses.append(
                    Hypothesis(
                        description=(
                            f"Violations correlated across services "
                            f"'{svc_a}' and '{svc_b}' -- a shared upstream "
                            f"dependency may be the root cause."
                        ),
                        confidence=confidence,
                        strategy_id=self.STRATEGY_ID,
                        supporting_evidence=[v.derived_id for v in combined],
                    )
                )

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        log.debug(
            "cross_service.generated",
            count=len(hypotheses),
            services=services_sorted,
        )
        return hypotheses


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TemporalStrategy
# ═══════════════════════════════════════════════════════════════════════════════


class TemporalStrategy(BaseStrategy):
    """Correlate violations that occurred within a narrow time window.

    Violations whose timestamps fall within ``temporal_window`` of each other
    are grouped and treated as likely sharing a common cause.
    """

    STRATEGY_ID: str = "temporal"

    def __init__(self, temporal_window: timedelta = _DEFAULT_TEMPORAL_WINDOW) -> None:
        self._window = temporal_window

    def generate(
        self,
        violations: list[DerivedFact],
        graph_context: dict[str, Any],
    ) -> list[Hypothesis]:
        if len(violations) < 2:
            log.debug("temporal.skipped", reason="fewer_than_two_violations")
            return []

        # Sort violations by timestamp.
        sorted_violations = sorted(violations, key=lambda v: v.timestamp)

        # Sliding-window grouping: collect clusters of temporally close violations.
        clusters: list[list[DerivedFact]] = []
        current_cluster: list[DerivedFact] = [sorted_violations[0]]

        for v in sorted_violations[1:]:
            if v.timestamp - current_cluster[-1].timestamp <= self._window:
                current_cluster.append(v)
            else:
                if len(current_cluster) >= 2:
                    clusters.append(current_cluster)
                current_cluster = [v]

        if len(current_cluster) >= 2:
            clusters.append(current_cluster)

        hypotheses: list[Hypothesis] = []
        for cluster in clusters:
            avg_conf = sum(v.confidence for v in cluster) / len(cluster)
            # Larger clusters get a slight boost (capped).
            size_boost = min(len(cluster) * 0.03, 0.15)
            confidence = min(avg_conf * 0.80 + size_boost, 0.90)

            ts_min = cluster[0].timestamp.isoformat()
            ts_max = cluster[-1].timestamp.isoformat()
            entities = _collect_entity_ids(cluster)
            entity_summary = ", ".join(entities[:5])
            if len(entities) > 5:
                entity_summary += f" (+{len(entities) - 5} more)"

            hypotheses.append(
                Hypothesis(
                    description=(
                        f"{len(cluster)} violations occurred within "
                        f"{self._window.total_seconds():.0f}s "
                        f"({ts_min} -- {ts_max}), affecting [{entity_summary}] "
                        f"-- likely a shared temporal cause."
                    ),
                    confidence=confidence,
                    strategy_id=self.STRATEGY_ID,
                    supporting_evidence=[v.derived_id for v in cluster],
                )
            )

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        log.debug(
            "temporal.generated",
            count=len(hypotheses),
            cluster_count=len(clusters),
        )
        return hypotheses


# ═══════════════════════════════════════════════════════════════════════════════
# 5. MemoryAssistedStrategy  (Phase 5 stub)
# ═══════════════════════════════════════════════════════════════════════════════


class MemoryAssistedStrategy(BaseStrategy):
    """Derive hypotheses by recalling similar past incidents from episodic memory.

    Phase 5 stub -- returns an empty list.  The full episodic-memory lookup
    will be implemented in Phase 5.
    """

    STRATEGY_ID: str = "memory_assisted"

    def generate(
        self,
        violations: list[DerivedFact],
        graph_context: dict[str, Any],
    ) -> list[Hypothesis]:
        log.debug("memory_assisted.stub", msg="Phase 5 -- returning empty list")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# 6. LLMAssistedStrategy  (Phase 5 stub)
# ═══════════════════════════════════════════════════════════════════════════════


class LLMAssistedStrategy(BaseStrategy):
    """Derive hypotheses by prompting an LLM with structured violation context.

    Phase 5 stub -- returns an empty list.  The full LLM-based reasoning
    pipeline will be implemented in Phase 5.
    """

    STRATEGY_ID: str = "llm_assisted"

    def generate(
        self,
        violations: list[DerivedFact],
        graph_context: dict[str, Any],
    ) -> list[Hypothesis]:
        log.debug("llm_assisted.stub", msg="Phase 5 -- returning empty list")
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_entity_id(violation: DerivedFact) -> str | None:
    """Extract the primary entity identifier from a violation's payload."""
    raw = violation.payload.get("subject_id") or violation.payload.get("entity_id")
    return str(raw) if raw is not None else None


def _collect_entity_ids(violations: list[DerivedFact]) -> list[str]:
    """Collect unique entity identifiers from a list of violations."""
    seen: set[str] = set()
    result: list[str] = []
    for v in violations:
        eid = _extract_entity_id(v)
        if eid and eid not in seen:
            seen.add(eid)
            result.append(eid)
    return result
