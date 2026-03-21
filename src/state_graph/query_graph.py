"""Neo4j query graph for persistent graph queries.

Phase 1 implementation: QueryGraphMaterializer, Neo4jGraphStore, GraphQueryEngine.

Consumes GraphDeltas and materializes them into Neo4j via parameterized Cypher.
Tenant isolation via node property ``tenant_id`` (v3.3 E2 Option A).
Staleness SLO: async materialization within 500 ms.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

import structlog
from neo4j import AsyncDriver, AsyncManagedTransaction

from src.core.fact import (
    AddEdge,
    AddNode,
    AddRuntimeEvent,
    AttachObservation,
    GraphDelta,
    RemoveEdge,
    RemoveNode,
    UpdateAttribute,
    validate_schema_version,
)
from src.state_graph.schema import EdgeType, NodeType

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Staleness SLO constant (seconds)
# ---------------------------------------------------------------------------
_STALENESS_SLO_SECONDS: float = 0.5


# ===========================================================================
# Neo4jGraphStore — low-level Neo4j operations
# ===========================================================================


class Neo4jGraphStore:
    """Low-level Neo4j operations: node/edge CRUD via parameterized Cypher.

    Every public method accepts an ``AsyncManagedTransaction`` so callers
    can compose multiple operations inside a single transaction when needed.
    """

    # -- Node operations ----------------------------------------------------

    @staticmethod
    async def merge_node(
        tx: AsyncManagedTransaction,
        node_id: UUID,
        node_type: str,
        tenant_id: str,
        attributes: dict[str, Any],
    ) -> None:
        """MERGE a node by its ``node_id``, setting type, tenant, and attrs."""
        props = {
            "node_id": str(node_id),
            "node_type": node_type,
            "tenant_id": tenant_id,
            **{k: _serialise_value(v) for k, v in attributes.items()},
        }
        cypher = (
            "MERGE (n:Entity {node_id: $node_id}) "
            "SET n += $props, n:Entity"
        )
        await tx.run(cypher, {"node_id": str(node_id), "props": props})

    @staticmethod
    async def remove_node(
        tx: AsyncManagedTransaction,
        node_id: UUID,
        tenant_id: str,
    ) -> None:
        """DETACH DELETE a node scoped to its tenant."""
        cypher = (
            "MATCH (n:Entity {node_id: $node_id, tenant_id: $tenant_id}) "
            "DETACH DELETE n"
        )
        await tx.run(cypher, {"node_id": str(node_id), "tenant_id": tenant_id})

    @staticmethod
    async def merge_edge(
        tx: AsyncManagedTransaction,
        edge_id: UUID,
        src_id: UUID,
        tgt_id: UUID,
        edge_type: str,
        tenant_id: str,
        attributes: dict[str, Any],
    ) -> None:
        """MERGE an edge between two nodes identified by ``node_id``."""
        props = {
            "edge_id": str(edge_id),
            "edge_type": edge_type,
            "tenant_id": tenant_id,
            **{k: _serialise_value(v) for k, v in attributes.items()},
        }
        cypher = (
            "MATCH (a:Entity {node_id: $src_id}), (b:Entity {node_id: $tgt_id}) "
            "MERGE (a)-[r:RELATES {edge_id: $edge_id}]->(b) "
            "SET r += $props"
        )
        await tx.run(
            cypher,
            {
                "src_id": str(src_id),
                "tgt_id": str(tgt_id),
                "edge_id": str(edge_id),
                "props": props,
            },
        )

    @staticmethod
    async def remove_edge(
        tx: AsyncManagedTransaction,
        edge_id: UUID,
        tenant_id: str,
    ) -> None:
        """Delete an edge by ``edge_id`` scoped to tenant."""
        cypher = (
            "MATCH ()-[r:RELATES {edge_id: $edge_id, tenant_id: $tenant_id}]->() "
            "DELETE r"
        )
        await tx.run(cypher, {"edge_id": str(edge_id), "tenant_id": tenant_id})

    @staticmethod
    async def update_attribute(
        tx: AsyncManagedTransaction,
        entity_id: UUID,
        key: str,
        new_value: Any,
        tenant_id: str,
    ) -> None:
        """Update a single attribute on a node *or* edge."""
        # Try node first, then edge.
        node_cypher = (
            "MATCH (n:Entity {node_id: $entity_id, tenant_id: $tenant_id}) "
            "SET n[$key] = $value"
        )
        # Neo4j does not support dynamic property keys via $key in SET.
        # Use apoc-free workaround with a known key parameter.
        node_cypher = (
            "MATCH (n:Entity {node_id: $entity_id, tenant_id: $tenant_id}) "
            f"SET n.`{_safe_property_name(key)}` = $value"
        )
        await tx.run(
            node_cypher,
            {
                "entity_id": str(entity_id),
                "tenant_id": tenant_id,
                "value": _serialise_value(new_value),
            },
        )
        edge_cypher = (
            "MATCH ()-[r:RELATES {edge_id: $entity_id, tenant_id: $tenant_id}]->() "
            f"SET r.`{_safe_property_name(key)}` = $value"
        )
        await tx.run(
            edge_cypher,
            {
                "entity_id": str(entity_id),
                "tenant_id": tenant_id,
                "value": _serialise_value(new_value),
            },
        )

    @staticmethod
    async def attach_observation(
        tx: AsyncManagedTransaction,
        entity_id: UUID,
        observation_data: dict[str, Any],
        tenant_id: str,
    ) -> None:
        """Store observation data as properties prefixed with ``obs_``."""
        set_clauses = ", ".join(
            f"n.`obs_{_safe_property_name(k)}` = $obs_{_safe_property_name(k)}"
            for k in observation_data
        )
        if not set_clauses:
            return
        cypher = (
            "MATCH (n:Entity {node_id: $entity_id, tenant_id: $tenant_id}) "
            f"SET {set_clauses}"
        )
        params: dict[str, Any] = {
            "entity_id": str(entity_id),
            "tenant_id": tenant_id,
        }
        for k, v in observation_data.items():
            params[f"obs_{_safe_property_name(k)}"] = _serialise_value(v)
        await tx.run(cypher, params)

    @staticmethod
    async def add_runtime_event(
        tx: AsyncManagedTransaction,
        event_id: UUID,
        event_type: str,
        participants: list[UUID],
        temporal_order: int,
        tenant_id: str,
    ) -> None:
        """Create a runtime-event node and link it to participants."""
        props = {
            "node_id": str(event_id),
            "node_type": "runtime_event",
            "event_type": event_type,
            "temporal_order": temporal_order,
            "tenant_id": tenant_id,
        }
        cypher = (
            "MERGE (e:Entity {node_id: $node_id}) "
            "SET e += $props, e:RuntimeEvent"
        )
        await tx.run(cypher, {"node_id": str(event_id), "props": props})

        for pid in participants:
            link_cypher = (
                "MATCH (e:Entity {node_id: $event_id}), "
                "(p:Entity {node_id: $participant_id}) "
                "MERGE (e)-[:PARTICIPATES]->(p)"
            )
            await tx.run(
                link_cypher,
                {"event_id": str(event_id), "participant_id": str(pid)},
            )


# ===========================================================================
# QueryGraphMaterializer — consumes GraphDeltas, applies to Neo4j
# ===========================================================================


class QueryGraphMaterializer:
    """Consume ``GraphDelta`` objects and apply them to Neo4j.

    * Validates ``schema_version`` before processing (v3.3 A1).
    * Executes all ops inside a single write transaction for atomicity.
    * Logs staleness vs the 500 ms SLO.
    """

    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver
        self._store = Neo4jGraphStore()

    async def apply_delta(self, delta: GraphDelta) -> None:
        """Validate and apply *delta* to Neo4j.

        Raises:
            ValueError: If the delta has an unsupported schema version.
        """
        validate_schema_version(delta)

        t0 = time.monotonic()
        tenant = delta.tenant_id

        async with self._driver.session() as session:
            async with session.begin_transaction() as tx:
                for op in delta.operations:
                    await self._apply_op(tx, op, tenant)
                await tx.commit()

        elapsed = time.monotonic() - t0
        log.info(
            "delta_applied",
            delta_id=str(delta.delta_id),
            ops=len(delta.operations),
            elapsed_ms=round(elapsed * 1000, 1),
            within_slo=elapsed < _STALENESS_SLO_SECONDS,
        )
        if elapsed >= _STALENESS_SLO_SECONDS:
            log.warning(
                "staleness_slo_exceeded",
                delta_id=str(delta.delta_id),
                elapsed_ms=round(elapsed * 1000, 1),
                slo_ms=round(_STALENESS_SLO_SECONDS * 1000),
            )

    # -- internal dispatch --------------------------------------------------

    async def _apply_op(
        self,
        tx: AsyncManagedTransaction,
        op: Any,
        tenant_id: str,
    ) -> None:
        if isinstance(op, AddNode):
            await self._store.merge_node(
                tx, op.node_id, op.node_type, tenant_id, op.attributes
            )
        elif isinstance(op, RemoveNode):
            await self._store.remove_node(tx, op.node_id, tenant_id)
        elif isinstance(op, AddEdge):
            await self._store.merge_edge(
                tx, op.edge_id, op.src_id, op.tgt_id,
                op.edge_type, tenant_id, op.attributes,
            )
        elif isinstance(op, RemoveEdge):
            await self._store.remove_edge(tx, op.edge_id, tenant_id)
        elif isinstance(op, UpdateAttribute):
            await self._store.update_attribute(
                tx, op.entity_id, op.key, op.new_value, tenant_id,
            )
        elif isinstance(op, AttachObservation):
            await self._store.attach_observation(
                tx, op.entity_id, op.observation_data, tenant_id,
            )
        elif isinstance(op, AddRuntimeEvent):
            await self._store.add_runtime_event(
                tx, op.event_id, op.event_type,
                op.participants, op.temporal_order, tenant_id,
            )
        else:
            log.warning("unknown_delta_op", op_type=type(op).__name__)


# ===========================================================================
# GraphQueryEngine — high-level read interface
# ===========================================================================


class GraphQueryEngine:
    """High-level query interface over the Neo4j-materialised graph.

    All reads go through the async driver's read transactions.
    """

    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver

    async def query(
        self, cypher: str, params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute an arbitrary read Cypher query.

        Args:
            cypher: Parameterised Cypher string.
            params: Query parameters (may be ``None``).

        Returns:
            List of record dicts.
        """
        async with self._driver.session() as session:
            result = await session.run(cypher, parameters=params or {})
            records = await result.data()
            return records

    async def get_node(self, node_id: UUID) -> dict[str, Any] | None:
        """Return a single node's properties, or ``None``."""
        cypher = "MATCH (n:Entity {node_id: $node_id}) RETURN properties(n) AS props"
        rows = await self.query(cypher, {"node_id": str(node_id)})
        if rows:
            return rows[0]["props"]
        return None

    async def get_neighbors(
        self, node_id: UUID, depth: int = 1,
    ) -> list[dict[str, Any]]:
        """Return neighbours up to *depth* hops away.

        Args:
            node_id: Starting node UUID.
            depth: Maximum traversal depth (default 1).

        Returns:
            List of neighbour property dicts (deduplicated).
        """
        if depth < 1:
            return []
        cypher = (
            "MATCH (start:Entity {node_id: $node_id})"
            f"-[*1..{int(depth)}]-(neighbor:Entity) "
            "WHERE neighbor.node_id <> $node_id "
            "RETURN DISTINCT properties(neighbor) AS props"
        )
        rows = await self.query(cypher, {"node_id": str(node_id)})
        return [r["props"] for r in rows]

    async def get_subgraph(
        self, node_ids: set[UUID],
    ) -> dict[str, Any]:
        """Return the induced subgraph for a set of node UUIDs.

        Returns:
            ``{"nodes": [...], "edges": [...]}`` where each node/edge is a
            property dict.
        """
        id_strings = [str(nid) for nid in node_ids]

        nodes_cypher = (
            "MATCH (n:Entity) WHERE n.node_id IN $ids "
            "RETURN properties(n) AS props"
        )
        node_rows = await self.query(nodes_cypher, {"ids": id_strings})

        edges_cypher = (
            "MATCH (a:Entity)-[r]->(b:Entity) "
            "WHERE a.node_id IN $ids AND b.node_id IN $ids "
            "RETURN properties(r) AS props, "
            "a.node_id AS src, b.node_id AS tgt, type(r) AS rel_type"
        )
        edge_rows = await self.query(edges_cypher, {"ids": id_strings})

        return {
            "nodes": [r["props"] for r in node_rows],
            "edges": [
                {
                    "src": r["src"],
                    "tgt": r["tgt"],
                    "rel_type": r["rel_type"],
                    **r["props"],
                }
                for r in edge_rows
            ],
        }


# ===========================================================================
# QueryGraph — unified facade (backwards-compatible with the original stub)
# ===========================================================================


class QueryGraph:
    """Neo4j-backed query graph supporting Cypher queries and delta application.

    Composes ``QueryGraphMaterializer`` (writes) and ``GraphQueryEngine``
    (reads) behind a single async interface.
    """

    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver
        self._materializer = QueryGraphMaterializer(driver)
        self._engine = GraphQueryEngine(driver)

    async def apply_delta(self, delta: GraphDelta) -> None:
        """Apply a graph delta to the Neo4j query graph.

        Args:
            delta: The graph delta to apply.
        """
        await self._materializer.apply_delta(delta)

    async def query(
        self, cypher: str, params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a Cypher query against the graph.

        Args:
            cypher: The Cypher query string (must be parameterised).
            params: Optional parameter dictionary for the query.

        Returns:
            List of result dictionaries.
        """
        return await self._engine.query(cypher, params)

    async def get_node(self, node_id: UUID) -> dict[str, Any] | None:
        """Return a single node's properties, or ``None``."""
        return await self._engine.get_node(node_id)

    async def get_neighbors(
        self, node_id: UUID, depth: int = 1,
    ) -> list[dict[str, Any]]:
        """Get neighbouring nodes up to a given traversal depth.

        Args:
            node_id: The UUID of the starting node.
            depth: Maximum traversal depth (default 1).

        Returns:
            List of neighbour node dictionaries.
        """
        return await self._engine.get_neighbors(node_id, depth)

    async def get_subgraph(
        self, node_ids: set[UUID],
    ) -> dict[str, Any]:
        """Return the induced subgraph for the given node UUIDs.

        Returns:
            Dict with ``"nodes"`` and ``"edges"`` lists.
        """
        return await self._engine.get_subgraph(node_ids)


# ===========================================================================
# Helpers
# ===========================================================================


def _safe_property_name(name: str) -> str:
    """Sanitise a property name to prevent Cypher injection.

    Only alphanumerics and underscores are kept.
    """
    return "".join(ch for ch in name if ch.isalnum() or ch == "_")


def _serialise_value(value: Any) -> Any:
    """Convert Python values to Neo4j-compatible types."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, dict):
        # Neo4j cannot store nested maps as properties; serialise to string.
        import json
        return json.dumps(value, default=str)
    if isinstance(value, (list, tuple)):
        return [_serialise_value(v) for v in value]
    return value
