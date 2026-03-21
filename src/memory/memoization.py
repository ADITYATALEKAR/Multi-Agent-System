"""Structural memoization cache with lineage tracking.

Phase 3 — Caches expensive computation results keyed by structural
fingerprints.  Supports delta-driven invalidation and lineage tracking
for IIE Pass 9 (cache lineage spot-checks).

v3.3 A2: Two-level memo key (WL-hash + canonical adjacency verification).
v3.3 C3: Environment included in cache key.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.memory.fingerprint import TwoLevelMemoKey

log = structlog.get_logger(__name__)

_DEFAULT_MAX_SIZE: int = 10_000
_DEFAULT_TTL_SECONDS: float = 3600.0  # 1 hour


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


@dataclass
class CacheEntry:
    """A single entry in the memoization cache."""

    entry_id: UUID = field(default_factory=uuid4)
    wl_hash: bytes = b""
    canonical_hash: bytes = b""
    environment: str = ""
    value: Any = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    hit_count: int = 0
    # Lineage: which graph entities contributed to this result
    source_entities: set[UUID] = field(default_factory=set)


# ---------------------------------------------------------------------------
# CacheLineageTracker
# ---------------------------------------------------------------------------


class CacheLineageTracker:
    """Tracks which graph entities each cache entry depends on.

    Used for:
    1. Delta-driven invalidation: when a node/edge changes, invalidate
       all cache entries that depended on it.
    2. IIE Pass 9 spot-checks: verify that cached results are still
       valid by re-computing a sample.
    """

    def __init__(self) -> None:
        # entity_id -> set of cache entry_ids that depend on it
        self._entity_to_entries: dict[UUID, set[UUID]] = {}
        # entry_id -> set of entity_ids it depends on
        self._entry_to_entities: dict[UUID, set[UUID]] = {}

    def register(self, entry_id: UUID, source_entities: set[UUID]) -> None:
        """Register the lineage for a cache entry."""
        self._entry_to_entities[entry_id] = set(source_entities)
        for entity_id in source_entities:
            if entity_id not in self._entity_to_entries:
                self._entity_to_entries[entity_id] = set()
            self._entity_to_entries[entity_id].add(entry_id)

    def get_affected_entries(self, changed_entities: set[UUID]) -> set[UUID]:
        """Return cache entry IDs affected by changes to the given entities."""
        affected: set[UUID] = set()
        for entity_id in changed_entities:
            affected.update(self._entity_to_entries.get(entity_id, set()))
        return affected

    def get_lineage(self, entry_id: UUID) -> set[UUID]:
        """Return the source entities for a cache entry."""
        return self._entry_to_entities.get(entry_id, set())

    def remove(self, entry_id: UUID) -> None:
        """Remove lineage tracking for an evicted cache entry."""
        entities = self._entry_to_entities.pop(entry_id, set())
        for entity_id in entities:
            entry_set = self._entity_to_entries.get(entity_id)
            if entry_set is not None:
                entry_set.discard(entry_id)
                if not entry_set:
                    del self._entity_to_entries[entity_id]

    @property
    def tracked_entries(self) -> int:
        return len(self._entry_to_entities)


# ---------------------------------------------------------------------------
# MemoizationCache
# ---------------------------------------------------------------------------


class MemoizationCache:
    """Structural memoization cache with two-level keys and lineage.

    Keys: (WL-hash, canonical_hash, environment).
    Values: arbitrary computation results.

    LRU eviction when max_size is reached.  TTL-based expiration.
    """

    def __init__(
        self,
        max_size: int = _DEFAULT_MAX_SIZE,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._max_size = max_size
        self._ttl_seconds = ttl_seconds
        self._two_level = TwoLevelMemoKey()
        self._lineage = CacheLineageTracker()
        # wl_hash -> list of CacheEntry (multiple entries per WL-hash due to collisions)
        self._cache: OrderedDict[bytes, list[CacheEntry]] = OrderedDict()
        self._total_entries: int = 0
        # Statistics
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0

    # -- Public API --------------------------------------------------------

    def get(
        self,
        nodes: list[dict[str, Any]],
        edges: list[tuple[int, int]],
        environment: str = "",
    ) -> Any | None:
        """Look up a cached result by graph structure.

        Performs two-level verification (v3.3 A2):
        1. Fast WL-hash lookup
        2. Canonical adjacency verification on hit

        Returns:
            Cached value or None if not found / expired / collision.
        """
        wl, canonical = self._two_level.compute_key(nodes, edges, environment)

        entries = self._cache.get(wl)
        if entries is None:
            self._misses += 1
            return None

        now = datetime.now(timezone.utc)
        for entry in entries:
            # Level 2: canonical verification
            if entry.canonical_hash != canonical:
                continue
            if entry.environment != environment:
                continue
            # TTL check
            age = (now - entry.created_at).total_seconds()
            if age > self._ttl_seconds:
                continue
            # Hit!
            entry.hit_count += 1
            entry.last_accessed = now
            self._hits += 1
            # Move to end (LRU)
            self._cache.move_to_end(wl)
            return entry.value

        self._misses += 1
        return None

    def put(
        self,
        nodes: list[dict[str, Any]],
        edges: list[tuple[int, int]],
        environment: str = "",
        value: Any = None,
        source_entities: set[UUID] | None = None,
    ) -> UUID:
        """Store a computation result keyed by graph structure.

        Args:
            nodes: Graph nodes.
            edges: Graph edges.
            environment: Environment string (v3.3 C3).
            value: The computation result to cache.
            source_entities: Graph entity UUIDs this result depends on (for lineage).

        Returns:
            The entry_id of the cached entry.
        """
        wl, canonical = self._two_level.compute_key(nodes, edges, environment)

        entry = CacheEntry(
            wl_hash=wl,
            canonical_hash=canonical,
            environment=environment,
            value=value,
            source_entities=source_entities or set(),
        )

        # Check for existing entries under same WL-hash
        if wl in self._cache:
            # Replace matching entry or append
            entries = self._cache[wl]
            for i, existing in enumerate(entries):
                if existing.canonical_hash == canonical and existing.environment == environment:
                    self._lineage.remove(existing.entry_id)
                    entries[i] = entry
                    self._lineage.register(entry.entry_id, entry.source_entities)
                    self._cache.move_to_end(wl)
                    return entry.entry_id
            entries.append(entry)
            self._total_entries += 1
        else:
            self._cache[wl] = [entry]
            self._total_entries += 1

        self._lineage.register(entry.entry_id, entry.source_entities)
        self._cache.move_to_end(wl)

        # Evict if over capacity
        while self._total_entries > self._max_size:
            self._evict_oldest()

        return entry.entry_id

    def invalidate(self, changed_entities: set[UUID]) -> int:
        """Invalidate cache entries affected by changed graph entities.

        Returns the number of entries invalidated.
        """
        affected = self._lineage.get_affected_entries(changed_entities)
        count = 0

        for entry_id in affected:
            # Find and remove the entry
            for wl_key, entries in list(self._cache.items()):
                entries[:] = [e for e in entries if e.entry_id != entry_id]
                if not entries:
                    del self._cache[wl_key]
                    count += 1
                    self._total_entries -= 1
                else:
                    # Check if we actually removed something
                    pass
            self._lineage.remove(entry_id)
            count += 1

        if count:
            log.debug("memo_cache.invalidated", count=count)
        return count

    def hit_ratio(self) -> float:
        """Return the cache hit ratio."""
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total

    def clear(self) -> None:
        """Clear the entire cache."""
        self._cache.clear()
        self._lineage = CacheLineageTracker()
        self._total_entries = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def size(self) -> int:
        return self._total_entries

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "size": self._total_entries,
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_ratio": self.hit_ratio(),
            "evictions": self._evictions,
            "lineage_tracked": self._lineage.tracked_entries,
        }

    # -- Internal ----------------------------------------------------------

    def _evict_oldest(self) -> None:
        """Evict the least recently used entry."""
        if not self._cache:
            return
        # Pop from front (oldest)
        wl_key, entries = next(iter(self._cache.items()))
        if entries:
            evicted = entries.pop(0)
            self._lineage.remove(evicted.entry_id)
            self._total_entries -= 1
            self._evictions += 1
        if not entries:
            del self._cache[wl_key]
