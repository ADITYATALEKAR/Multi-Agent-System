"""DeltaEntities: normalized side table (v3.3 Fix 1).

Replaces scope[1] index entirely. Fan-out on append for efficient
entity-centric queries: entity_state_at and entity_diff.

No raw SQL strings — parameterized asyncpg queries only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog

from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)


class DeltaEntityStore:
    """Manages the delta_entities normalized side table.

    This table provides efficient entity-centric queries without
    requiring array containment scans on the delta_log.scope column.

    Args:
        pool: asyncpg connection pool.
        tenant_id: Tenant identifier.
    """

    def __init__(self, pool: Any, tenant_id: str = "default") -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def fan_out(self, delta: GraphDelta, sequence_number: int) -> int:
        """Fan-out a delta to delta_entities for each entity in scope.

        Called atomically during DeltaLogStore.append() within the same transaction.
        Returns the number of entity rows inserted.
        """
        if not delta.scope:
            return 0

        records = [
            (self._tenant_id, entity_id, sequence_number, delta.timestamp, delta.delta_id)
            for entity_id in delta.scope
        ]

        async with self._pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO delta_entities (tenant_id, entity_id, sequence_number, timestamp, delta_id)
                VALUES ($1, $2, $3, $4, $5)
                """,
                records,
            )

        logger.debug(
            "delta_entities_fan_out",
            delta_id=str(delta.delta_id),
            entity_count=len(records),
        )
        return len(records)

    async def entity_state_at(
        self, entity_id: UUID, timestamp: datetime
    ) -> list[UUID]:
        """Get all delta_ids affecting an entity up to a timestamp.

        Returns delta_ids in sequence order for replay.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT delta_id
                FROM delta_entities
                WHERE tenant_id = $1 AND entity_id = $2 AND timestamp <= $3
                ORDER BY sequence_number ASC
                """,
                self._tenant_id,
                entity_id,
                timestamp,
            )
            return [row["delta_id"] for row in rows]

    async def entity_diff(
        self, entity_id: UUID, from_seq: int, to_seq: int
    ) -> list[UUID]:
        """Get delta_ids affecting an entity between two sequence numbers.

        Returns delta_ids for incremental replay.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT delta_id
                FROM delta_entities
                WHERE tenant_id = $1 AND entity_id = $2
                      AND sequence_number > $3 AND sequence_number <= $4
                ORDER BY sequence_number ASC
                """,
                self._tenant_id,
                entity_id,
                from_seq,
                to_seq,
            )
            return [row["delta_id"] for row in rows]

    async def get_entities_for_delta(self, delta_id: UUID) -> list[UUID]:
        """Get all entity_ids associated with a delta."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT entity_id
                FROM delta_entities
                WHERE delta_id = $1
                """,
                delta_id,
            )
            return [row["entity_id"] for row in rows]

    async def get_entity_timeline(
        self, entity_id: UUID, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get the timeline of deltas affecting an entity.

        Returns dicts with delta_id, sequence_number, timestamp.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT delta_id, sequence_number, timestamp
                FROM delta_entities
                WHERE tenant_id = $1 AND entity_id = $2
                ORDER BY sequence_number DESC
                LIMIT $3
                """,
                self._tenant_id,
                entity_id,
                limit,
            )
            return [
                {
                    "delta_id": row["delta_id"],
                    "sequence_number": row["sequence_number"],
                    "timestamp": row["timestamp"],
                }
                for row in rows
            ]

    async def count_entities(self) -> int:
        """Count distinct entities in the store."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                """
                SELECT COUNT(DISTINCT entity_id)
                FROM delta_entities
                WHERE tenant_id = $1
                """,
                self._tenant_id,
            )
