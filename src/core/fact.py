"""Core fact primitives: Fact, DeltaOp, GraphDelta.

These are the foundational data structures for the Split Graph world model.
GraphDelta.schema_version is present from day 0 (v3.3 A1).
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────


class FactType(str, enum.Enum):
    NODE_FACT = "node_fact"
    EDGE_FACT = "edge_fact"
    ATTRIBUTE_FACT = "attribute_fact"
    OBSERVATION_FACT = "observation_fact"
    RUNTIME_FACT = "runtime_fact"


class GraphTier(str, enum.Enum):
    DELTA_LOG = "delta_log"
    QUERY_GRAPH = "query_graph"
    REASONING_GRAPH = "reasoning_graph"
    OSG = "osg"


# ── Fact ─────────────────────────────────────────────────────────────────────


class Fact(BaseModel):
    """An atomic, immutable observation about the target system."""

    fact_id: UUID = Field(default_factory=uuid4)
    fact_type: FactType
    subject_id: UUID
    predicate: str
    object_value: Any
    source_analyzer: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    environment: str = "default"
    graph_tier: GraphTier = GraphTier.DELTA_LOG
    fingerprint: bytes = b""


# ── DeltaOp variants ────────────────────────────────────────────────────────


class AddNode(BaseModel):
    """Add a node to the graph."""

    op: str = "add_node"
    node_id: UUID = Field(default_factory=uuid4)
    node_type: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class RemoveNode(BaseModel):
    """Remove a node from the graph."""

    op: str = "remove_node"
    node_id: UUID


class AddEdge(BaseModel):
    """Add an edge to the graph."""

    op: str = "add_edge"
    edge_id: UUID = Field(default_factory=uuid4)
    src_id: UUID
    tgt_id: UUID
    edge_type: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class RemoveEdge(BaseModel):
    """Remove an edge from the graph."""

    op: str = "remove_edge"
    edge_id: UUID


class UpdateAttribute(BaseModel):
    """Update an attribute on a node or edge."""

    op: str = "update_attribute"
    entity_id: UUID
    key: str
    old_value: Any
    new_value: Any


class AttachObservation(BaseModel):
    """Attach observation data to an entity."""

    op: str = "attach_observation"
    entity_id: UUID
    observation_data: dict[str, Any] = Field(default_factory=dict)


class AddRuntimeEvent(BaseModel):
    """Add a runtime event to the OSG."""

    op: str = "add_runtime_event"
    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    participants: list[UUID] = Field(default_factory=list)
    temporal_order: int = 0


DeltaOp = (
    AddNode
    | RemoveNode
    | AddEdge
    | RemoveEdge
    | UpdateAttribute
    | AttachObservation
    | AddRuntimeEvent
)


# ── GraphDelta ───────────────────────────────────────────────────────────────

# Current schema version — increment on breaking DeltaOp format changes.
CURRENT_SCHEMA_VERSION: int = 1


class GraphDelta(BaseModel):
    """An atomic batch of operations applied to the Split Graph.

    schema_version (v3.3 A1): Present from day 0. All delta consumers MUST
    check schema_version and reject unknown versions with a clear error
    rather than silent misparse.
    """

    delta_id: UUID = Field(default_factory=uuid4)
    sequence_number: int = Field(ge=0)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str
    operations: list[DeltaOp]
    scope: set[UUID] = Field(default_factory=set)
    tenant_id: str = "default"
    causal_predecessor: Optional[UUID] = None
    schema_version: int = CURRENT_SCHEMA_VERSION


def validate_schema_version(delta: GraphDelta) -> None:
    """Reject deltas with unknown schema versions.

    v3.3 A1: All delta consumers MUST call this before processing.
    """
    if delta.schema_version != CURRENT_SCHEMA_VERSION:
        raise ValueError(
            f"Unknown GraphDelta schema_version={delta.schema_version}, "
            f"expected={CURRENT_SCHEMA_VERSION}. "
            f"Refusing to process to avoid silent misparse."
        )
