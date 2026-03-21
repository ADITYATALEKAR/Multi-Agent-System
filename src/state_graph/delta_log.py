"""GraphDeltaLog: PostgreSQL append-only delta log.

Implements DeltaLogWriter, DeltaLogReader, and compaction.
v3.1 base + v3.3 Fix 1 (delta_entities fan-out) + v3.3 Fix 5 (HOT/WARM/COLD lifecycle).

No raw SQL strings — all queries use parameterized asyncpg calls.
No mutable global state — state is owned by DeltaLogStore instances.
"""

from __future__ import annotations

import enum
import json
import zlib
from datetime import datetime, timedelta
from typing import Any, Optional
from uuid import UUID

import structlog

from src.core.fact import (
    CURRENT_SCHEMA_VERSION,
    GraphDelta,
    validate_schema_version,
)
from src.observability.metrics import blueprint_delta_append_duration_seconds

logger = structlog.get_logger(__name__)


class DeltaTier(str, enum.Enum):
    """Three-tier DeltaLog lifecycle (v3.3 Fix 5)."""

    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


def _serialize_operations(operations: list[Any]) -> str:
    """Serialize DeltaOp list to JSON for PostgreSQL JSONB storage."""
    return json.dumps([op.model_dump() for op in operations])


def _serialize_scope(scope: set[UUID]) -> list[str]:
    """Convert scope UUIDs to string list for PostgreSQL UUID[] storage."""
    return [str(uid) for uid in scope]


class DeltaLogStore:
    """Append-only log of graph deltas backed by PostgreSQL.

    Handles delta_log writes, delta_entities fan-out (v3.3 Fix 1),
    and three-tier lifecycle management (v3.3 Fix 5).

    Args:
        pool: asyncpg connection pool.
        tenant_id: Tenant identifier for partition routing.
    """

    def __init__(self, pool: Any, tenant_id: str = "default") -> None:
        self._pool = pool
        self._tenant_id = tenant_id

    async def append(self, delta: GraphDelta) -> int:
        """Append a delta to the log atomically.

        Writes to delta_log and fans out to delta_entities in a single transaction.
        Returns the assigned sequence_number (monotonic, gap-free within tenant).
        """
        validate_schema_version(delta)

        with blueprint_delta_append_duration_seconds.time():
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    # Insert into delta_log
                    seq = await conn.fetchval(
                        """
                        INSERT INTO delta_log
                            (delta_id, tenant_id, timestamp, source, operations,
                             scope, causal_predecessor, schema_version)
                        VALUES ($1, $2, $3, $4, $5::jsonb, $6::uuid[], $7, $8)
                        RETURNING sequence_number
                        """,
                        delta.delta_id,
                        self._tenant_id,
                        delta.timestamp,
                        delta.source,
                        _serialize_operations(delta.operations),
                        _serialize_scope(delta.scope),
                        delta.causal_predecessor,
                        delta.schema_version,
                    )

                    # Fan-out to delta_entities (v3.3 Fix 1)
                    if delta.scope:
                        await self._fan_out_entities(conn, delta, seq)

                    logger.info(
                        "delta_appended",
                        delta_id=str(delta.delta_id),
                        sequence_number=seq,
                        scope_size=len(delta.scope),
                        ops_count=len(delta.operations),
                    )

                    return seq

    async def _fan_out_entities(
        self, conn: Any, delta: GraphDelta, sequence_number: int
    ) -> None:
        """Fan-out delta to delta_entities for each entity in scope (v3.3 Fix 1)."""
        records = [
            (self._tenant_id, entity_id, sequence_number, delta.timestamp, delta.delta_id)
            for entity_id in delta.scope
        ]
        await conn.executemany(
            """
            INSERT INTO delta_entities (tenant_id, entity_id, sequence_number, timestamp, delta_id)
            VALUES ($1, $2, $3, $4, $5)
            """,
            records,
        )

    async def get_range(
        self, start_seq: int, end_seq: int
    ) -> list[GraphDelta]:
        """Retrieve deltas within a sequence number range (inclusive)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT delta_id, sequence_number, timestamp, source, operations,
                       scope, causal_predecessor, schema_version
                FROM delta_log
                WHERE tenant_id = $1 AND sequence_number >= $2 AND sequence_number <= $3
                ORDER BY sequence_number ASC
                """,
                self._tenant_id,
                start_seq,
                end_seq,
            )
            return [self._row_to_delta(row) for row in rows]

    async def get_by_scope(self, entity_ids: set[UUID]) -> list[GraphDelta]:
        """Retrieve deltas affecting any of the given entities.

        Uses delta_entities table (v3.3 Fix 1) for efficient lookup.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT dl.delta_id, dl.sequence_number, dl.timestamp,
                       dl.source, dl.operations, dl.scope,
                       dl.causal_predecessor, dl.schema_version
                FROM delta_entities de
                JOIN delta_log dl ON de.delta_id = dl.delta_id AND dl.tenant_id = $1
                WHERE de.tenant_id = $1 AND de.entity_id = ANY($2::uuid[])
                ORDER BY dl.sequence_number ASC
                """,
                self._tenant_id,
                [str(uid) for uid in entity_ids],
            )
            return [self._row_to_delta(row) for row in rows]

    async def get_latest_sequence(self) -> int:
        """Get the latest sequence number for this tenant."""
        async with self._pool.acquire() as conn:
            result = await conn.fetchval(
                """
                SELECT COALESCE(MAX(sequence_number), 0)
                FROM delta_log WHERE tenant_id = $1
                """,
                self._tenant_id,
            )
            return result

    async def entity_state_at(
        self, entity_id: UUID, timestamp: datetime
    ) -> list[GraphDelta]:
        """Get all deltas affecting an entity up to a point in time (v3.3 Fix 1)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT dl.delta_id, dl.sequence_number, dl.timestamp,
                       dl.source, dl.operations, dl.scope,
                       dl.causal_predecessor, dl.schema_version
                FROM delta_entities de
                JOIN delta_log dl ON de.delta_id = dl.delta_id AND dl.tenant_id = $1
                WHERE de.tenant_id = $1 AND de.entity_id = $2 AND de.timestamp <= $3
                ORDER BY de.sequence_number ASC
                """,
                self._tenant_id,
                entity_id,
                timestamp,
            )
            return [self._row_to_delta(row) for row in rows]

    async def entity_diff(
        self, entity_id: UUID, from_seq: int, to_seq: int
    ) -> list[GraphDelta]:
        """Get deltas affecting an entity between two sequence numbers (v3.3 Fix 1)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT dl.delta_id, dl.sequence_number, dl.timestamp,
                       dl.source, dl.operations, dl.scope,
                       dl.causal_predecessor, dl.schema_version
                FROM delta_entities de
                JOIN delta_log dl ON de.delta_id = dl.delta_id AND dl.tenant_id = $1
                WHERE de.tenant_id = $1 AND de.entity_id = $2
                      AND de.sequence_number > $3 AND de.sequence_number <= $4
                ORDER BY de.sequence_number ASC
                """,
                self._tenant_id,
                entity_id,
                from_seq,
                to_seq,
            )
            return [self._row_to_delta(row) for row in rows]

    # ── Lifecycle management (v3.3 Fix 5) ────────────────────────────────

    async def archive_to_warm(self, before: datetime) -> int:
        """Move old HOT deltas to WARM (compressed archive). Returns count archived."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                rows = await conn.fetch(
                    """
                    SELECT delta_id, sequence_number, timestamp, operations, scope
                    FROM delta_log
                    WHERE tenant_id = $1 AND timestamp < $2
                    ORDER BY sequence_number ASC
                    """,
                    self._tenant_id,
                    before,
                )

                if not rows:
                    return 0

                for row in rows:
                    compressed = zlib.compress(
                        json.dumps(row["operations"]).encode(), level=6
                    )
                    await conn.execute(
                        """
                        INSERT INTO delta_log_archive
                            (delta_id, tenant_id, sequence_number, timestamp,
                             operations_compressed, scope)
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (delta_id) DO NOTHING
                        """,
                        row["delta_id"],
                        self._tenant_id,
                        row["sequence_number"],
                        row["timestamp"],
                        compressed,
                        row["scope"],
                    )

                # Delete from HOT
                await conn.execute(
                    """
                    DELETE FROM delta_log
                    WHERE tenant_id = $1 AND timestamp < $2
                    """,
                    self._tenant_id,
                    before,
                )

                logger.info(
                    "deltas_archived_to_warm",
                    tenant_id=self._tenant_id,
                    count=len(rows),
                    cutoff=before.isoformat(),
                )
                return len(rows)

    async def compact(self, retention_days: int = 30) -> int:
        """Run compaction: archive deltas older than retention_days (v3.2 Risk Fix C)."""
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        return await self.archive_to_warm(cutoff)

    # ── Internal helpers ─────────────────────────────────────────────────

    @staticmethod
    def _row_to_delta(row: Any) -> GraphDelta:
        """Convert a database row to a GraphDelta."""
        operations_raw = row["operations"]
        if isinstance(operations_raw, str):
            operations_raw = json.loads(operations_raw)

        # Re-parse operations into DeltaOp objects
        from src.core.fact import (
            AddEdge,
            AddNode,
            AddRuntimeEvent,
            AttachObservation,
            RemoveEdge,
            RemoveNode,
            UpdateAttribute,
        )

        _OP_MAP = {
            "add_node": AddNode,
            "remove_node": RemoveNode,
            "add_edge": AddEdge,
            "remove_edge": RemoveEdge,
            "update_attribute": UpdateAttribute,
            "attach_observation": AttachObservation,
            "add_runtime_event": AddRuntimeEvent,
        }

        ops = []
        for op_data in operations_raw:
            op_type = op_data.get("op", "")
            if op_type in _OP_MAP:
                ops.append(_OP_MAP[op_type](**op_data))

        scope_raw = row["scope"] or []
        scope = {UUID(str(s)) if not isinstance(s, UUID) else s for s in scope_raw}

        return GraphDelta(
            delta_id=row["delta_id"],
            sequence_number=row["sequence_number"],
            timestamp=row["timestamp"],
            source=row["source"],
            operations=ops,
            scope=scope,
            causal_predecessor=row["causal_predecessor"],
            schema_version=row["schema_version"],
        )
