"""MemoryAgent — unified interface for the memory subsystem.

Phase 3 — Provides the API specified in the blueprint:
    - query(working_memory) -> MemoryResult
    - store_episode(episode) -> UUID
    - get_similar_incidents(violations, region) -> list[Episode]
    - get_known_rules(region, environment) -> list[SemanticRule]
    - get_diagnostic_procedure(violation_pattern) -> Procedure | None
    - get_pattern_match(signature) -> Pattern | None
    - get_template_match(fingerprint, law_categories) -> CausalTemplate | None
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from src.memory.causal_template import CausalTemplate
from src.memory.consolidation import ConsolidationPipeline, ConsolidationResult
from src.memory.fingerprint import FingerprintIndex
from src.memory.memoization import MemoizationCache
from src.memory.retrieval import (
    CausalPatternRetrieval,
    GraphRegionRetrieval,
    LawBasedRetrieval,
    PatternMatchRetrieval,
    RepairTypeRetrieval,
)
from src.memory.storage import InMemoryBackend, MemoryStore
from src.memory.types import (
    Episode,
    MemoryResult,
    Pattern,
    Procedure,
    SemanticRule,
    WorkingMemory,
)

log = structlog.get_logger(__name__)


class MemoryAgent:
    """Agent responsible for memory lifecycle management.

    Wires together storage, retrieval strategies, fingerprint index,
    memoization cache, and consolidation pipeline.
    """

    def __init__(
        self,
        store: MemoryStore | None = None,
        fingerprint_index: FingerprintIndex | None = None,
        memo_cache: MemoizationCache | None = None,
    ) -> None:
        self._store = store or InMemoryBackend()
        self._fp_index = fingerprint_index or FingerprintIndex()
        self._memo = memo_cache or MemoizationCache()

        # Retrieval strategies
        self._law_retrieval = LawBasedRetrieval(self._store)
        self._region_retrieval = GraphRegionRetrieval(self._store)
        self._causal_retrieval = CausalPatternRetrieval(self._store, self._fp_index)
        self._repair_retrieval = RepairTypeRetrieval(self._store)
        self._pattern_retrieval = PatternMatchRetrieval(self._store)

        # Consolidation
        self._consolidation = ConsolidationPipeline(self._store)

        # Template store (in-memory, keyed by fingerprint)
        self._templates: dict[bytes, list[CausalTemplate]] = {}

    # -- Query (unified) ---------------------------------------------------

    def query(self, working_memory: WorkingMemory) -> MemoryResult:
        """Run all relevant retrieval strategies against working memory.

        Combines results from law-based, region-based, and causal-pattern
        retrieval, deduplicates, and returns the merged MemoryResult.
        """
        tenant = working_memory.tenant_id
        result = MemoryResult()

        # Law-based retrieval from context
        law_cats = set(working_memory.context.get("law_categories", []))
        if law_cats:
            lr = self._law_retrieval.retrieve(
                {"law_categories": law_cats}, tenant_id=tenant
            )
            result.episodes.extend(lr.episodes)
            result.rules.extend(lr.rules)

        # Region-based retrieval
        region = set(working_memory.context.get("region", []))
        if region:
            rr = self._region_retrieval.retrieve(
                {"region": region}, tenant_id=tenant
            )
            result.episodes.extend(rr.episodes)
            result.rules.extend(rr.rules)

        # Deduplicate episodes by episode_id
        seen: set[UUID] = set()
        unique_episodes: list[Episode] = []
        for ep in result.episodes:
            if ep.episode_id not in seen:
                seen.add(ep.episode_id)
                unique_episodes.append(ep)
        result.episodes = unique_episodes

        # Deduplicate rules by rule_id
        seen_rules: set[UUID] = set()
        unique_rules: list[SemanticRule] = []
        for r in result.rules:
            if r.rule_id not in seen_rules:
                seen_rules.add(r.rule_id)
                unique_rules.append(r)
        result.rules = unique_rules

        result.total_matches = len(result.episodes) + len(result.rules)
        return result

    # -- Episode management ------------------------------------------------

    def store_episode(self, episode: Episode) -> UUID:
        """Persist an episode and index its fingerprint."""
        eid = self._store.store_episode(episode)

        # Index fingerprint if available
        if episode.fingerprint and episode.metadata.get("causal_graph"):
            cg = episode.metadata["causal_graph"]
            nodes = cg.get("nodes", [])
            edges_raw = cg.get("edges", [])
            # Convert edge dicts to index tuples
            node_types = [n.get("type", "") for n in nodes]
            edges = []
            for e in edges_raw:
                src_type = e.get("source_type", "")
                tgt_type = e.get("target_type", "")
                src_idx = next((i for i, t in enumerate(node_types) if t == src_type), -1)
                tgt_idx = next((i for i, t in enumerate(node_types) if t == tgt_type), -1)
                if src_idx >= 0 and tgt_idx >= 0:
                    edges.append((src_idx, tgt_idx))
            self._fp_index.insert(
                episode.episode_id,
                [{"label": t} for t in node_types],
                edges,
                episode.environment,
            )

        log.debug("memory_agent.store_episode", episode_id=str(eid))
        return eid

    def get_similar_incidents(
        self,
        violations: list[UUID],
        region: set[UUID],
        tenant_id: str = "default",
    ) -> list[Episode]:
        """Find episodes with overlapping violations and region."""
        all_episodes = self._store.query_episodes(tenant_id, limit=200)
        scored: list[tuple[float, Episode]] = []

        for ep in all_episodes:
            # Score by violation overlap + region overlap
            v_overlap = len(set(violations) & set(ep.trigger_violations))
            r_overlap = len(region & ep.region) if ep.region else 0
            score = v_overlap * 2 + r_overlap
            if score > 0:
                scored.append((score, ep))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in scored[:20]]

    # -- Rule retrieval ----------------------------------------------------

    def get_known_rules(
        self,
        region: set[UUID],
        environment: str,
        tenant_id: str = "default",
    ) -> list[SemanticRule]:
        """Get semantic rules applicable to a region and environment."""
        return self._store.query_rules(
            tenant_id, region=region, environment=environment
        )

    # -- Procedure retrieval -----------------------------------------------

    def get_diagnostic_procedure(
        self,
        violation_pattern: bytes,
        tenant_id: str = "default",
    ) -> Procedure | None:
        """Get the best diagnostic procedure for a violation pattern."""
        procedures = self._store.query_procedures(
            tenant_id, pattern_fingerprint=violation_pattern, limit=5
        )
        if not procedures:
            return None
        # Return the one with highest success rate
        return max(procedures, key=lambda p: p.success_rate)

    # -- Pattern retrieval -------------------------------------------------

    def get_pattern_match(
        self,
        signature: bytes,
        tenant_id: str = "default",
    ) -> Pattern | None:
        """Get the best matching pattern for a fingerprint signature."""
        patterns = self._store.query_patterns_by_signature(signature, tenant_id)
        if not patterns:
            return None
        return max(patterns, key=lambda p: p.confidence)

    # -- Template retrieval ------------------------------------------------

    def store_template(self, template: CausalTemplate) -> None:
        """Store a causal template indexed by fingerprint."""
        if template.fingerprint not in self._templates:
            self._templates[template.fingerprint] = []
        self._templates[template.fingerprint].append(template)

    def get_template_match(
        self,
        fingerprint: bytes,
        law_categories: set[str],
    ) -> CausalTemplate | None:
        """Get the best matching causal template.

        Matches by fingerprint first, then filters by law_categories
        overlap, returning the highest-confidence match.
        """
        candidates = self._templates.get(fingerprint, [])
        if not candidates:
            return None

        # Filter by law_categories overlap
        matching = [
            t for t in candidates
            if not t.archived and law_categories.intersection(t.law_categories)
        ]
        if not matching:
            # Fall back to any non-archived template with this fingerprint
            matching = [t for t in candidates if not t.archived]

        if not matching:
            return None
        return max(matching, key=lambda t: t.confidence)

    # -- Consolidation -----------------------------------------------------

    def consolidate(self, tenant_id: str = "default") -> ConsolidationResult:
        """Run the consolidation pipeline."""
        return self._consolidation.consolidate(tenant_id)

    # -- Memoization proxy -------------------------------------------------

    def memo_get(
        self,
        nodes: list[dict[str, Any]],
        edges: list[tuple[int, int]],
        environment: str = "",
    ) -> Any | None:
        """Check the memoization cache."""
        return self._memo.get(nodes, edges, environment)

    def memo_put(
        self,
        nodes: list[dict[str, Any]],
        edges: list[tuple[int, int]],
        environment: str = "",
        value: Any = None,
        source_entities: set[UUID] | None = None,
    ) -> UUID:
        """Store a result in the memoization cache."""
        return self._memo.put(nodes, edges, environment, value, source_entities)

    def memo_invalidate(self, changed_entities: set[UUID]) -> int:
        """Invalidate memo cache entries affected by changed entities."""
        return self._memo.invalidate(changed_entities)

    @property
    def memo_stats(self) -> dict[str, Any]:
        return self._memo.stats
