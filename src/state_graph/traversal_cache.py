"""Traversal Cache: Redis-backed cache with delta-driven invalidation (v3.2 Risk Fix A).

Caches graph traversal results (neighbor queries, subgraph extractions, etc.)
and invalidates affected entries when new deltas arrive.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

import structlog

from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)

# Cache key prefix
_PREFIX = "tc:"
# Default TTL in seconds
_DEFAULT_TTL = 300


class TraversalCache:
    """Redis-backed traversal result cache.

    Delta-driven invalidation: when a delta arrives, all cache entries
    whose scope overlaps with the delta's scope are invalidated.

    Args:
        redis: Redis async client instance.
        ttl: Default TTL for cache entries in seconds.
    """

    def __init__(self, redis: Any, ttl: int = _DEFAULT_TTL) -> None:
        self._redis = redis
        self._ttl = ttl
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> Any | None:
        """Retrieve a cached traversal result."""
        raw = await self._redis.get(f"{_PREFIX}{key}")
        if raw is None:
            self._misses += 1
            return None
        self._hits += 1
        return json.loads(raw)

    async def put(
        self, key: str, value: Any, entity_ids: Optional[set[UUID]] = None
    ) -> None:
        """Store a traversal result with optional entity scope tracking.

        Args:
            key: Cache key.
            value: JSON-serializable result.
            entity_ids: Entity IDs this result depends on (for invalidation).
        """
        await self._redis.set(
            f"{_PREFIX}{key}",
            json.dumps(value, default=str),
            ex=self._ttl,
        )

        # Track which entities this cache entry depends on
        if entity_ids:
            for eid in entity_ids:
                await self._redis.sadd(f"{_PREFIX}scope:{eid}", key)
                await self._redis.expire(f"{_PREFIX}scope:{eid}", self._ttl)

    async def invalidate_for_delta(self, delta: GraphDelta) -> int:
        """Invalidate all cache entries affected by a delta.

        Uses the delta's scope to find and delete affected entries.
        Returns the number of entries invalidated.
        """
        invalidated = 0

        for entity_id in delta.scope:
            scope_key = f"{_PREFIX}scope:{entity_id}"
            keys = await self._redis.smembers(scope_key)

            if keys:
                cache_keys = [f"{_PREFIX}{k}" for k in keys]
                if cache_keys:
                    await self._redis.delete(*cache_keys)
                    invalidated += len(cache_keys)
                await self._redis.delete(scope_key)

        if invalidated > 0:
            logger.debug(
                "traversal_cache_invalidated",
                delta_id=str(delta.delta_id),
                entries_invalidated=invalidated,
            )

        return invalidated

    async def invalidate_all(self) -> None:
        """Invalidate all cache entries."""
        # Use SCAN to find and delete all keys with prefix
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match=f"{_PREFIX}*", count=100)
            if keys:
                await self._redis.delete(*keys)
            if cursor == 0:
                break

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0.0 to 1.0). Target: >50% under steady-state."""
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total

    @property
    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses}
