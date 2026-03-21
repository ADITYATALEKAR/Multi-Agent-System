"""Rust-backed in-memory reasoning graph for fast inference.

Phase 1 implementation with dual-mode support:
  1. Rust PyO3 bindings (blueprint_rust) — preferred, high-performance path
  2. Pure Python fallback — dict-based with identical interface

v3.3 specs:
  - A1: Schema version validation on all incoming deltas
  - E1: MessagePack checkpoint every 5 min, recovery <10s for 100K nodes
  - E4: p99 delta application latency <2ms SLO tracked via Prometheus
"""

from __future__ import annotations

import copy
import time
from collections import OrderedDict
from typing import Any
from uuid import UUID

import msgpack
import structlog

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
from src.observability.metrics import (
    blueprint_delta_append_duration_seconds,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Attempt Rust import; fall back to Python implementation
# ---------------------------------------------------------------------------

_RUST_AVAILABLE = False
try:
    import blueprint_rust  # noqa: F401

    _RUST_AVAILABLE = True
    logger.info("reasoning_graph.backend", backend="rust")
except ImportError:
    logger.info("reasoning_graph.backend", backend="python_fallback")


# ---------------------------------------------------------------------------
# UUID serialization helpers for msgpack
# ---------------------------------------------------------------------------


def _uuid_to_str(u: UUID) -> str:
    return str(u)


def _str_to_uuid(s: str) -> UUID:
    return UUID(s)


def _serialize_value(v: Any) -> Any:
    """Recursively convert UUIDs and sets for msgpack compatibility."""
    if isinstance(v, UUID):
        return {"__uuid__": str(v)}
    if isinstance(v, set):
        return {"__set__": [_serialize_value(i) for i in v]}
    if isinstance(v, dict):
        return {k: _serialize_value(val) for k, val in v.items()}
    if isinstance(v, (list, tuple)):
        return [_serialize_value(i) for i in v]
    return v


def _deserialize_value(v: Any) -> Any:
    """Recursively restore UUIDs and sets from msgpack output."""
    if isinstance(v, dict):
        if "__uuid__" in v:
            return UUID(v["__uuid__"])
        if "__set__" in v:
            return set(_deserialize_value(i) for i in v["__set__"])
        return {k: _deserialize_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_deserialize_value(i) for i in v]
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return v


# ---------------------------------------------------------------------------
# Attention-weighted LRU eviction tracker
# ---------------------------------------------------------------------------


class _AttentionLRU:
    """Tracks access recency and attention weight for eviction decisions.

    Each entry has an attention weight (default 1.0). Eviction picks the
    entry with the lowest (attention_weight / recency_rank) score, i.e.
    least-recently-used entries with low attention are evicted first.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._order: OrderedDict[UUID, float] = OrderedDict()  # id -> attention weight

    @property
    def capacity(self) -> int:
        return self._capacity

    def touch(self, uid: UUID, attention: float = 1.0) -> None:
        """Record an access, moving to most-recent position."""
        if uid in self._order:
            self._order.move_to_end(uid)
        self._order[uid] = max(attention, 0.01)

    def remove(self, uid: UUID) -> None:
        self._order.pop(uid, None)

    def evict_one(self) -> UUID | None:
        """Return the UUID to evict (lowest attention, least recent), or None."""
        if not self._order:
            return None
        # Score = attention * position (higher position = more recent).
        # We want to evict the entry with the lowest score.
        best_uid: UUID | None = None
        best_score = float("inf")
        for idx, (uid, attn) in enumerate(self._order.items()):
            score = attn * (idx + 1)
            if score < best_score:
                best_score = score
                best_uid = uid
        if best_uid is not None:
            del self._order[best_uid]
        return best_uid

    def __len__(self) -> int:
        return len(self._order)

    def __contains__(self, uid: UUID) -> bool:
        return uid in self._order


# ---------------------------------------------------------------------------
# ReasoningGraph — Pure-Python fallback implementation
# ---------------------------------------------------------------------------


class ReasoningGraph:
    """In-memory graph optimized for reasoning, backed by a Rust engine
    when available, otherwise using a pure-Python dict-based fallback.

    Args:
        capacity: Maximum number of nodes before LRU eviction kicks in.
            Defaults to 200_000 (well above the 100K checkpoint SLO target).
    """

    def __init__(self, capacity: int = 200_000) -> None:
        self._capacity = capacity

        if _RUST_AVAILABLE:
            self._rust_core = blueprint_rust.ReasoningGraph(capacity)
            self._using_rust = True
        else:
            self._rust_core = None
            self._using_rust = False

        # Pure-Python storage (used when Rust is unavailable)
        self._nodes: dict[UUID, dict] = {}
        self._edges: dict[UUID, dict] = {}
        self._adjacency: dict[UUID, set[UUID]] = {}
        self._type_index: dict[str, set[UUID]] = {}

        # Eviction tracker (used in Python fallback mode)
        self._lru = _AttentionLRU(capacity)

        # Latency tracking
        self._delta_latencies: list[float] = []

        logger.debug(
            "reasoning_graph.init",
            capacity=capacity,
            backend="rust" if self._using_rust else "python",
        )

    # ------------------------------------------------------------------
    # Delta application
    # ------------------------------------------------------------------

    def apply_delta(self, delta: GraphDelta) -> None:
        """Apply a graph delta to the in-memory reasoning graph.

        Validates schema version (v3.3 A1), then dispatches each DeltaOp.
        Tracks p99 latency against the <2ms SLO (v3.3 E4).

        Args:
            delta: The graph delta to apply.

        Raises:
            ValueError: If the delta has an unknown schema version.
        """
        validate_schema_version(delta)

        start = time.perf_counter()

        if self._using_rust:
            self._apply_delta_rust(delta)
        else:
            self._apply_delta_python(delta)

        elapsed = time.perf_counter() - start

        # Record in Prometheus histogram
        blueprint_delta_append_duration_seconds.observe(elapsed)

        # Track locally for p99 reporting
        self._delta_latencies.append(elapsed)
        if len(self._delta_latencies) > 10_000:
            self._delta_latencies = self._delta_latencies[-5_000:]

        if elapsed > 0.002:
            logger.warning(
                "reasoning_graph.delta_slo_breach",
                elapsed_ms=round(elapsed * 1000, 3),
                delta_id=str(delta.delta_id),
                op_count=len(delta.operations),
            )

    def _apply_delta_rust(self, delta: GraphDelta) -> None:
        """Forward delta to the Rust core."""
        # Serialize ops to dicts for the Rust FFI boundary
        ops = [op.model_dump(mode="json") for op in delta.operations]
        self._rust_core.apply_delta(ops)  # type: ignore[union-attr]

    def _apply_delta_python(self, delta: GraphDelta) -> None:
        """Apply delta using pure-Python storage."""
        for op in delta.operations:
            if isinstance(op, AddNode):
                self._add_node(op)
            elif isinstance(op, RemoveNode):
                self._remove_node(op)
            elif isinstance(op, AddEdge):
                self._add_edge(op)
            elif isinstance(op, RemoveEdge):
                self._remove_edge(op)
            elif isinstance(op, UpdateAttribute):
                self._update_attribute(op)
            elif isinstance(op, AttachObservation):
                self._attach_observation(op)
            elif isinstance(op, AddRuntimeEvent):
                self._add_runtime_event(op)
            else:
                logger.warning("reasoning_graph.unknown_op", op_type=type(op).__name__)

    # -- Individual op handlers (Python fallback) --------------------------

    def _add_node(self, op: AddNode) -> None:
        self._maybe_evict()
        node = {
            "node_id": op.node_id,
            "node_type": op.node_type,
            "attributes": dict(op.attributes),
        }
        self._nodes[op.node_id] = node
        self._adjacency.setdefault(op.node_id, set())
        self._type_index.setdefault(op.node_type, set()).add(op.node_id)
        attention = op.attributes.get("attention_weight", 1.0)
        self._lru.touch(op.node_id, attention)

    def _remove_node(self, op: RemoveNode) -> None:
        node = self._nodes.pop(op.node_id, None)
        if node is None:
            return
        # Remove from type index
        ntype = node.get("node_type", "")
        if ntype in self._type_index:
            self._type_index[ntype].discard(op.node_id)
            if not self._type_index[ntype]:
                del self._type_index[ntype]
        # Remove incident edges
        neighbor_ids = list(self._adjacency.pop(op.node_id, set()))
        for nid in neighbor_ids:
            if nid in self._adjacency:
                self._adjacency[nid].discard(op.node_id)
        # Remove edge records where this node is src or tgt
        edge_ids_to_remove = [
            eid
            for eid, e in self._edges.items()
            if e.get("src_id") == op.node_id or e.get("tgt_id") == op.node_id
        ]
        for eid in edge_ids_to_remove:
            del self._edges[eid]
        self._lru.remove(op.node_id)

    def _add_edge(self, op: AddEdge) -> None:
        edge = {
            "edge_id": op.edge_id,
            "src_id": op.src_id,
            "tgt_id": op.tgt_id,
            "edge_type": op.edge_type,
            "attributes": dict(op.attributes),
        }
        self._edges[op.edge_id] = edge
        self._adjacency.setdefault(op.src_id, set()).add(op.tgt_id)
        self._adjacency.setdefault(op.tgt_id, set()).add(op.src_id)
        # Touch both endpoints to keep them warm
        if op.src_id in self._nodes:
            self._lru.touch(op.src_id)
        if op.tgt_id in self._nodes:
            self._lru.touch(op.tgt_id)

    def _remove_edge(self, op: RemoveEdge) -> None:
        edge = self._edges.pop(op.edge_id, None)
        if edge is None:
            return
        src = edge["src_id"]
        tgt = edge["tgt_id"]
        # Only remove adjacency if no other edges connect these nodes
        has_other = any(
            e["src_id"] == src and e["tgt_id"] == tgt
            or e["src_id"] == tgt and e["tgt_id"] == src
            for e in self._edges.values()
        )
        if not has_other:
            if src in self._adjacency:
                self._adjacency[src].discard(tgt)
            if tgt in self._adjacency:
                self._adjacency[tgt].discard(src)

    def _update_attribute(self, op: UpdateAttribute) -> None:
        # Try nodes first, then edges
        if op.entity_id in self._nodes:
            self._nodes[op.entity_id].setdefault("attributes", {})[op.key] = op.new_value
            self._lru.touch(op.entity_id)
        elif op.entity_id in self._edges:
            self._edges[op.entity_id].setdefault("attributes", {})[op.key] = op.new_value
        else:
            logger.warning(
                "reasoning_graph.update_attribute_miss",
                entity_id=str(op.entity_id),
                key=op.key,
            )

    def _attach_observation(self, op: AttachObservation) -> None:
        if op.entity_id in self._nodes:
            attrs = self._nodes[op.entity_id].setdefault("attributes", {})
            observations = attrs.setdefault("_observations", [])
            observations.append(op.observation_data)
            self._lru.touch(op.entity_id)
        elif op.entity_id in self._edges:
            attrs = self._edges[op.entity_id].setdefault("attributes", {})
            observations = attrs.setdefault("_observations", [])
            observations.append(op.observation_data)

    def _add_runtime_event(self, op: AddRuntimeEvent) -> None:
        # Store runtime events as special nodes
        self._maybe_evict()
        node = {
            "node_id": op.event_id,
            "node_type": "__runtime_event__",
            "attributes": {
                "event_type": op.event_type,
                "participants": list(op.participants),
                "temporal_order": op.temporal_order,
            },
        }
        self._nodes[op.event_id] = node
        self._adjacency.setdefault(op.event_id, set())
        self._type_index.setdefault("__runtime_event__", set()).add(op.event_id)
        self._lru.touch(op.event_id, 0.5)  # lower default attention for events
        # Create adjacency to participants
        for pid in op.participants:
            if pid in self._nodes:
                self._adjacency.setdefault(op.event_id, set()).add(pid)
                self._adjacency.setdefault(pid, set()).add(op.event_id)

    # -- Eviction ----------------------------------------------------------

    def _maybe_evict(self) -> None:
        """Evict lowest-attention LRU nodes if at capacity."""
        while len(self._nodes) >= self._capacity:
            uid = self._lru.evict_one()
            if uid is None:
                break
            node = self._nodes.pop(uid, None)
            if node is None:
                continue
            ntype = node.get("node_type", "")
            if ntype in self._type_index:
                self._type_index[ntype].discard(uid)
            # Remove incident edges
            neighbors = list(self._adjacency.pop(uid, set()))
            for nid in neighbors:
                if nid in self._adjacency:
                    self._adjacency[nid].discard(uid)
            edge_ids_to_remove = [
                eid
                for eid, e in self._edges.items()
                if e.get("src_id") == uid or e.get("tgt_id") == uid
            ]
            for eid in edge_ids_to_remove:
                del self._edges[eid]
            logger.debug("reasoning_graph.evicted", node_id=str(uid))

    # ------------------------------------------------------------------
    # Fork (copy-on-write — Phase 0: deep copy)
    # ------------------------------------------------------------------

    def fork(self) -> ReasoningGraph:
        """Create a copy-on-write fork of this reasoning graph.

        Phase 0 implementation: full deep copy. Will be replaced with
        structural sharing / CoW pages in Phase 2+.

        Returns:
            A new ReasoningGraph instance with identical state.
        """
        if self._using_rust:
            new = ReasoningGraph.__new__(ReasoningGraph)
            new._capacity = self._capacity
            new._using_rust = True
            new._rust_core = self._rust_core.fork()  # type: ignore[union-attr]
            new._nodes = {}
            new._edges = {}
            new._adjacency = {}
            new._type_index = {}
            new._lru = _AttentionLRU(self._capacity)
            new._delta_latencies = []
            return new

        new = ReasoningGraph(capacity=self._capacity)
        new._nodes = copy.deepcopy(self._nodes)
        new._edges = copy.deepcopy(self._edges)
        new._adjacency = copy.deepcopy(self._adjacency)
        new._type_index = copy.deepcopy(self._type_index)
        # Rebuild LRU from current state (deep copy of OrderedDict)
        new._lru._order = copy.deepcopy(self._lru._order)
        logger.debug(
            "reasoning_graph.forked",
            node_count=len(new._nodes),
            edge_count=len(new._edges),
        )
        return new

    # ------------------------------------------------------------------
    # Checkpoint / Restore (MessagePack — v3.3 E1)
    # ------------------------------------------------------------------

    def checkpoint(self) -> bytes:
        """Serialize the current graph state to MessagePack bytes.

        v3.3 E1: Checkpoint every 5 min, recovery <10s for 100K nodes.

        Returns:
            Binary MessagePack snapshot of the graph.
        """
        if self._using_rust:
            return self._rust_core.checkpoint()  # type: ignore[union-attr]

        # Build a serializable snapshot
        nodes_ser = {}
        for uid, node in self._nodes.items():
            nodes_ser[str(uid)] = _serialize_value(node)

        edges_ser = {}
        for uid, edge in self._edges.items():
            edges_ser[str(uid)] = _serialize_value(edge)

        adjacency_ser = {
            str(uid): [str(n) for n in neighbors]
            for uid, neighbors in self._adjacency.items()
        }

        type_index_ser = {
            tname: [str(n) for n in nids]
            for tname, nids in self._type_index.items()
        }

        snapshot = {
            "version": 1,
            "capacity": self._capacity,
            "nodes": nodes_ser,
            "edges": edges_ser,
            "adjacency": adjacency_ser,
            "type_index": type_index_ser,
        }

        data = msgpack.packb(snapshot, use_bin_type=True)
        logger.info(
            "reasoning_graph.checkpoint",
            node_count=len(self._nodes),
            edge_count=len(self._edges),
            bytes=len(data),
        )
        return data

    def restore(self, data: bytes) -> None:
        """Restore graph state from a MessagePack checkpoint.

        Args:
            data: Binary snapshot previously produced by checkpoint().

        Raises:
            ValueError: If the snapshot version is unsupported.
        """
        if self._using_rust:
            self._rust_core.restore(data)  # type: ignore[union-attr]
            return

        start = time.perf_counter()
        snapshot = msgpack.unpackb(data, raw=False)

        snap_version = snapshot.get("version", 0)
        if snap_version != 1:
            raise ValueError(
                f"Unsupported checkpoint version={snap_version}, expected=1"
            )

        self._capacity = snapshot.get("capacity", self._capacity)

        # Restore nodes
        self._nodes = {}
        for uid_str, node_data in snapshot.get("nodes", {}).items():
            uid = UUID(uid_str)
            self._nodes[uid] = _deserialize_value(node_data)

        # Restore edges
        self._edges = {}
        for uid_str, edge_data in snapshot.get("edges", {}).items():
            uid = UUID(uid_str)
            self._edges[uid] = _deserialize_value(edge_data)

        # Restore adjacency
        self._adjacency = {}
        for uid_str, neighbor_strs in snapshot.get("adjacency", {}).items():
            uid = UUID(uid_str)
            self._adjacency[uid] = {UUID(n) for n in neighbor_strs}

        # Restore type index
        self._type_index = {}
        for tname, nid_strs in snapshot.get("type_index", {}).items():
            self._type_index[tname] = {UUID(n) for n in nid_strs}

        # Rebuild LRU (all nodes get default attention after restore)
        self._lru = _AttentionLRU(self._capacity)
        for uid in self._nodes:
            self._lru.touch(uid, 1.0)

        elapsed = time.perf_counter() - start
        logger.info(
            "reasoning_graph.restored",
            node_count=len(self._nodes),
            edge_count=len(self._edges),
            elapsed_s=round(elapsed, 3),
        )

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_node(self, node_id: UUID) -> dict | None:
        """Retrieve a single node by its UUID.

        Args:
            node_id: The UUID of the node to retrieve.

        Returns:
            Node dictionary, or None if not found.
        """
        if self._using_rust:
            return self._rust_core.get_node(str(node_id))  # type: ignore[union-attr]

        node = self._nodes.get(node_id)
        if node is not None:
            self._lru.touch(node_id)
        return node

    def get_edge(self, edge_id: UUID) -> dict | None:
        """Retrieve a single edge by its UUID.

        Args:
            edge_id: The UUID of the edge to retrieve.

        Returns:
            Edge dictionary, or None if not found.
        """
        if self._using_rust:
            return self._rust_core.get_edge(str(edge_id))  # type: ignore[union-attr]

        return self._edges.get(edge_id)

    def get_neighbors(self, node_id: UUID) -> list[UUID]:
        """Get all neighbor node IDs for a given node.

        Args:
            node_id: The UUID of the node.

        Returns:
            List of neighbor UUIDs (empty if node not found).
        """
        if self._using_rust:
            raw = self._rust_core.get_neighbors(str(node_id))  # type: ignore[union-attr]
            return [UUID(s) for s in raw]

        if node_id in self._nodes:
            self._lru.touch(node_id)
        return list(self._adjacency.get(node_id, set()))

    def node_count(self) -> int:
        """Return the total number of nodes in the graph."""
        if self._using_rust:
            return self._rust_core.node_count()  # type: ignore[union-attr]
        return len(self._nodes)

    def edge_count(self) -> int:
        """Return the total number of edges in the graph."""
        if self._using_rust:
            return self._rust_core.edge_count()  # type: ignore[union-attr]
        return len(self._edges)

    # ------------------------------------------------------------------
    # Additional query helpers
    # ------------------------------------------------------------------

    def get_nodes_by_type(self, node_type: str) -> list[UUID]:
        """Return all node UUIDs of a given type.

        Args:
            node_type: The node type string to filter by.

        Returns:
            List of matching node UUIDs.
        """
        if self._using_rust:
            raw = self._rust_core.get_nodes_by_type(node_type)  # type: ignore[union-attr]
            return [UUID(s) for s in raw]
        return list(self._type_index.get(node_type, set()))

    def p99_delta_latency_ms(self) -> float:
        """Return the p99 delta application latency in milliseconds.

        Used for SLO monitoring (target: <2ms).
        """
        if not self._delta_latencies:
            return 0.0
        sorted_lat = sorted(self._delta_latencies)
        idx = int(len(sorted_lat) * 0.99)
        idx = min(idx, len(sorted_lat) - 1)
        return sorted_lat[idx] * 1000

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        backend = "rust" if self._using_rust else "python"
        return (
            f"ReasoningGraph(nodes={self.node_count()}, "
            f"edges={self.edge_count()}, backend={backend})"
        )
