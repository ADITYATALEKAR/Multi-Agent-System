"""Semantic Cache / Normalization Layer.

Caches analyzer results keyed by (file_hash, analyzer_version, toolchain_version).
Backed by PostgreSQL semantic_cache table.
Avoids re-analyzing unchanged files.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


def compute_cache_key(
    file_hash: str, analyzer_version: str, toolchain_version: str
) -> str:
    """Compute a deterministic cache key from file + analyzer + toolchain."""
    raw = f"{file_hash}:{analyzer_version}:{toolchain_version}"
    return hashlib.sha256(raw.encode()).hexdigest()


class SemanticCache:
    """Analyzer result cache backed by PostgreSQL.

    Prevents re-analysis of unchanged files by caching results keyed on
    the file content hash, analyzer version, and toolchain version.

    Args:
        pool: asyncpg connection pool.
        tenant_id: Tenant identifier.
    """

    def __init__(self, pool: Any, tenant_id: str = "default") -> None:
        self._pool = pool
        self._tenant_id = tenant_id
        self._hits = 0
        self._misses = 0

    async def get(
        self,
        file_hash: str,
        analyzer_version: str,
        toolchain_version: str,
    ) -> Any | None:
        """Look up cached analysis results.

        Returns the cached JSONB result, or None on cache miss.
        """
        cache_key = compute_cache_key(file_hash, analyzer_version, toolchain_version)

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT result FROM semantic_cache
                WHERE cache_key = $1 AND tenant_id = $2
                """,
                cache_key,
                self._tenant_id,
            )

        if row is None:
            self._misses += 1
            return None

        self._hits += 1
        return row["result"]

    async def put(
        self,
        file_hash: str,
        analyzer_version: str,
        toolchain_version: str,
        result: Any,
    ) -> None:
        """Store analysis results in the cache."""
        cache_key = compute_cache_key(file_hash, analyzer_version, toolchain_version)

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO semantic_cache
                    (cache_key, file_hash, analyzer_version, toolchain_version,
                     result, created_at, tenant_id)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                ON CONFLICT (cache_key) DO UPDATE
                SET result = EXCLUDED.result,
                    created_at = EXCLUDED.created_at
                """,
                cache_key,
                file_hash,
                analyzer_version,
                toolchain_version,
                json.dumps(result, default=str),
                datetime.utcnow(),
                self._tenant_id,
            )

    async def invalidate(self, file_hashes: set[str]) -> int:
        """Invalidate cache entries for given file hashes.

        Returns the number of entries deleted.
        """
        if not file_hashes:
            return 0

        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM semantic_cache
                WHERE tenant_id = $1 AND file_hash = ANY($2::text[])
                """,
                self._tenant_id,
                list(file_hashes),
            )
            count = int(result.split()[-1])
            if count > 0:
                logger.info(
                    "semantic_cache_invalidated",
                    file_count=len(file_hashes),
                    entries_deleted=count,
                )
            return count

    async def invalidate_all(self) -> int:
        """Invalidate all cache entries for this tenant."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM semantic_cache WHERE tenant_id = $1",
                self._tenant_id,
            )
            return int(result.split()[-1])

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0
