"""Memory retrieval strategies.

Phase 3 — 6 retrieval strategies per v3.1 spec:
    1. LawBasedRetrieval — retrieve by law category
    2. GraphRegionRetrieval — retrieve by graph region (set of UUIDs)
    3. CausalPatternRetrieval — retrieve by causal fingerprint
    4. EnvironmentFilter — filter results by environment (v3.3 C3)
    5. RepairTypeRetrieval — retrieve repair templates by violation type
    6. PatternMatchRetrieval — retrieve by structural fingerprint
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from src.memory.fingerprint import FingerprintIndex
from src.memory.storage import MemoryStore
from src.memory.types import (
    Episode,
    MemoryResult,
    Pattern,
    Procedure,
    RepairTemplate,
    SemanticRule,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class BaseRetrieval:
    """Base class for retrieval strategies."""

    STRATEGY_ID: str = "base"

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def retrieve(self, query: dict[str, Any], tenant_id: str = "default") -> MemoryResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 1. LawBasedRetrieval
# ---------------------------------------------------------------------------


class LawBasedRetrieval(BaseRetrieval):
    """Retrieve memory entries by law category.

    Given a set of law categories (e.g. {"structural", "security"}),
    returns episodes, rules, and patterns that relate to those categories.
    """

    STRATEGY_ID: str = "law_based"

    def retrieve(self, query: dict[str, Any], tenant_id: str = "default") -> MemoryResult:
        law_categories: set[str] = set(query.get("law_categories", []))
        environment: str | None = query.get("environment")
        limit: int = query.get("limit", 50)

        episodes = self._store.query_episodes(
            tenant_id,
            law_categories=law_categories if law_categories else None,
            environment=environment,
            limit=limit,
        )

        rules = self._store.query_rules(
            tenant_id,
            environment=environment,
            limit=limit,
        )
        # Filter rules by law_categories if specified
        if law_categories:
            rules = [r for r in rules if law_categories.intersection(r.law_categories)]

        log.debug(
            "retrieval.law_based",
            categories=list(law_categories),
            episodes=len(episodes),
            rules=len(rules),
        )
        return MemoryResult(
            episodes=episodes,
            rules=rules,
            total_matches=len(episodes) + len(rules),
        )


# ---------------------------------------------------------------------------
# 2. GraphRegionRetrieval
# ---------------------------------------------------------------------------


class GraphRegionRetrieval(BaseRetrieval):
    """Retrieve memory entries by graph region.

    Given a set of node UUIDs defining a sub-graph region, returns
    episodes and rules whose ``region`` overlaps.
    """

    STRATEGY_ID: str = "graph_region"

    def retrieve(self, query: dict[str, Any], tenant_id: str = "default") -> MemoryResult:
        region: set[UUID] = set(query.get("region", []))
        environment: str | None = query.get("environment")
        limit: int = query.get("limit", 50)

        if not region:
            return MemoryResult()

        rules = self._store.query_rules(
            tenant_id,
            region=region,
            environment=environment,
            limit=limit,
        )

        # Episodes: filter by region overlap
        all_episodes = self._store.query_episodes(
            tenant_id, environment=environment, limit=limit * 2
        )
        episodes = [ep for ep in all_episodes if ep.region.intersection(region)][:limit]

        log.debug(
            "retrieval.graph_region",
            region_size=len(region),
            episodes=len(episodes),
            rules=len(rules),
        )
        return MemoryResult(
            episodes=episodes,
            rules=rules,
            total_matches=len(episodes) + len(rules),
        )


# ---------------------------------------------------------------------------
# 3. CausalPatternRetrieval
# ---------------------------------------------------------------------------


class CausalPatternRetrieval(BaseRetrieval):
    """Retrieve episodes and templates by causal fingerprint.

    Uses the FingerprintIndex for exact and approximate matching.
    """

    STRATEGY_ID: str = "causal_pattern"

    def __init__(self, store: MemoryStore, fingerprint_index: FingerprintIndex) -> None:
        super().__init__(store)
        self._fp_index = fingerprint_index

    def retrieve(self, query: dict[str, Any], tenant_id: str = "default") -> MemoryResult:
        fingerprint: bytes = query.get("fingerprint", b"")
        threshold: float = query.get("threshold", 0.5)

        if not fingerprint:
            return MemoryResult()

        # Exact match
        exact_ids = self._fp_index.query_exact(fingerprint)

        # Approximate match (if graph data provided)
        nodes = query.get("nodes", [])
        edges = query.get("edges", [])
        approx_matches: list[tuple[UUID, float]] = []
        if nodes:
            approx_matches = self._fp_index.query_approximate(nodes, edges, threshold)

        # Gather matching episodes
        all_ids = set(exact_ids) | {uid for uid, _ in approx_matches}
        episodes: list[Episode] = []
        for uid in all_ids:
            ep = self._store.get_episode(uid, tenant_id)
            if ep is not None:
                episodes.append(ep)

        log.debug(
            "retrieval.causal_pattern",
            exact=len(exact_ids),
            approx=len(approx_matches),
            episodes=len(episodes),
        )
        return MemoryResult(
            episodes=episodes,
            total_matches=len(episodes),
        )


# ---------------------------------------------------------------------------
# 4. EnvironmentFilter
# ---------------------------------------------------------------------------


class EnvironmentFilter(BaseRetrieval):
    """Filter memory results by environment (v3.3 C3).

    Wraps another retrieval strategy and post-filters by environment.
    """

    STRATEGY_ID: str = "environment_filter"

    def __init__(self, store: MemoryStore, inner: BaseRetrieval) -> None:
        super().__init__(store)
        self._inner = inner

    def retrieve(self, query: dict[str, Any], tenant_id: str = "default") -> MemoryResult:
        environment: str | None = query.get("environment")
        result = self._inner.retrieve(query, tenant_id)

        if environment is None:
            return result

        result.episodes = [
            ep for ep in result.episodes if ep.environment == environment
        ]
        result.rules = [
            r for r in result.rules if r.environment == environment
        ]
        result.total_matches = len(result.episodes) + len(result.rules)

        log.debug(
            "retrieval.environment_filter",
            environment=environment,
            episodes=len(result.episodes),
            rules=len(result.rules),
        )
        return result


# ---------------------------------------------------------------------------
# 5. RepairTypeRetrieval
# ---------------------------------------------------------------------------


class RepairTypeRetrieval(BaseRetrieval):
    """Retrieve repair templates matching a violation pattern fingerprint."""

    STRATEGY_ID: str = "repair_type"

    def retrieve(self, query: dict[str, Any], tenant_id: str = "default") -> MemoryResult:
        violation_pattern: bytes = query.get("violation_pattern", b"")
        limit: int = query.get("limit", 20)

        if not violation_pattern:
            # Return all templates
            templates = self._store.query_repair_templates(tenant_id, limit=limit)
        else:
            templates = self._store.query_repair_templates(
                tenant_id, violation_pattern=violation_pattern, limit=limit
            )

        # Also look for associated procedures
        procedures: list[Procedure] = []
        if violation_pattern:
            procedures = self._store.query_procedures(
                tenant_id, pattern_fingerprint=violation_pattern, limit=limit
            )

        log.debug(
            "retrieval.repair_type",
            templates=len(templates),
            procedures=len(procedures),
        )
        return MemoryResult(
            repair_templates=templates,
            procedures=procedures,
            total_matches=len(templates) + len(procedures),
        )


# ---------------------------------------------------------------------------
# 6. PatternMatchRetrieval
# ---------------------------------------------------------------------------


class PatternMatchRetrieval(BaseRetrieval):
    """Retrieve patterns by structural signature fingerprint."""

    STRATEGY_ID: str = "pattern_match"

    def retrieve(self, query: dict[str, Any], tenant_id: str = "default") -> MemoryResult:
        signature: bytes = query.get("signature", b"")
        limit: int = query.get("limit", 20)

        if not signature:
            return MemoryResult()

        patterns = self._store.query_patterns_by_signature(signature, tenant_id)[:limit]

        # Also look for procedures applicable to this pattern
        procedures = self._store.query_procedures(
            tenant_id, pattern_fingerprint=signature, limit=limit
        )

        log.debug(
            "retrieval.pattern_match",
            patterns=len(patterns),
            procedures=len(procedures),
        )
        return MemoryResult(
            patterns=patterns,
            procedures=procedures,
            total_matches=len(patterns) + len(procedures),
        )
