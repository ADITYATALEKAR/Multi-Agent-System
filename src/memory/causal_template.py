"""Causal templates — reusable causal patterns (Primitive 5, v3.2).

A CausalTemplate captures the abstract causal structure of a class
of incidents.  It is built by the TemplateAbstractor from multiple
similar Episodes using Approximate Maximum Common Subgraph (MCS).

Components:
    - AbstractNode: a generalised node with a role (e.g. "source", "sink")
    - AbstractEdge: a causal relationship between abstract nodes
    - AbstractGraph: the abstract graph structure
    - CausalTemplate: the full template with matching and instantiation
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

import structlog

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract graph components
# ---------------------------------------------------------------------------


class AbstractNode(BaseModel):
    """A generalized node in a causal template.

    Instead of a concrete service name, this might be "ServiceA" or
    role="upstream-producer".
    """

    node_id: str = Field(default_factory=lambda: str(uuid4())[:8])
    role: str = ""  # e.g. "source", "sink", "relay", "root_cause"
    node_type: str = ""  # e.g. "service", "function", "module"
    constraints: dict[str, Any] = Field(default_factory=dict)  # e.g. {"min_deps": 3}
    label: str = ""


class AbstractEdge(BaseModel):
    """A causal relationship between abstract nodes."""

    source: str  # node_id
    target: str  # node_id
    edge_type: str = "causes"  # "causes", "correlates", "precedes"
    weight: float = 1.0
    constraints: dict[str, Any] = Field(default_factory=dict)


class AbstractGraph(BaseModel):
    """The abstract graph structure of a causal template."""

    nodes: list[AbstractNode] = Field(default_factory=list)
    edges: list[AbstractEdge] = Field(default_factory=list)

    def get_node(self, node_id: str) -> AbstractNode | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def get_edges_from(self, node_id: str) -> list[AbstractEdge]:
        return [e for e in self.edges if e.source == node_id]

    def get_edges_to(self, node_id: str) -> list[AbstractEdge]:
        return [e for e in self.edges if e.target == node_id]

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)


# ---------------------------------------------------------------------------
# CausalTemplate
# ---------------------------------------------------------------------------


class CausalTemplate(BaseModel):
    """A reusable causal pattern extracted from multiple episodes.

    Templates are matched against new incidents to enable fast
    root-cause identification.  Matching produces a confidence score
    based on structural similarity and constraint satisfaction.
    """

    template_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = "default"
    name: str = ""
    description: str = ""
    graph: AbstractGraph = Field(default_factory=AbstractGraph)
    law_categories: set[str] = Field(default_factory=set)
    source_episodes: list[UUID] = Field(default_factory=list)
    fingerprint: bytes = b""
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    match_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_matched: Optional[datetime] = None
    archived: bool = False

    model_config = {"arbitrary_types_allowed": True}

    # -- Matching ----------------------------------------------------------

    def match(self, context: dict[str, Any]) -> float:
        """Return a confidence score [0, 1] for how well *context* matches.

        Matching considers:
        1. Node-type overlap between template and context nodes.
        2. Edge-type overlap between template and context edges.
        3. Constraint satisfaction for template nodes/edges.

        Args:
            context: Dict with keys "nodes" (list of dicts with "type")
                     and "edges" (list of dicts with "type", "source", "target").

        Returns:
            Match confidence between 0.0 and 1.0.
        """
        ctx_nodes = context.get("nodes", [])
        ctx_edges = context.get("edges", [])

        if not self.graph.nodes:
            return 0.0

        # Node type matching
        ctx_types = {n.get("type", "") for n in ctx_nodes}
        template_types = {n.node_type for n in self.graph.nodes if n.node_type}
        if not template_types:
            node_score = 0.5  # no type constraints
        else:
            overlap = len(ctx_types & template_types)
            node_score = overlap / len(template_types) if template_types else 0.0

        # Edge type matching
        ctx_edge_types = {e.get("type", "") for e in ctx_edges}
        template_edge_types = {e.edge_type for e in self.graph.edges}
        if not template_edge_types:
            edge_score = 0.5
        else:
            edge_overlap = len(ctx_edge_types & template_edge_types)
            edge_score = edge_overlap / len(template_edge_types) if template_edge_types else 0.0

        # Structural size similarity
        size_ratio = min(len(ctx_nodes), self.graph.node_count) / max(
            len(ctx_nodes), self.graph.node_count, 1
        )

        # Weighted combination
        score = 0.4 * node_score + 0.3 * edge_score + 0.2 * size_ratio + 0.1 * self.confidence
        return min(max(score, 0.0), 1.0)

    def instantiate(self, bindings: dict[str, Any]) -> dict[str, Any]:
        """Produce a concrete causal explanation by binding variables.

        Args:
            bindings: Mapping from abstract node_id to concrete entity info.

        Returns:
            A fully-bound causal explanation dict.
        """
        bound_nodes = []
        for node in self.graph.nodes:
            binding = bindings.get(node.node_id, {})
            bound_nodes.append({
                "abstract_id": node.node_id,
                "role": node.role,
                "node_type": node.node_type,
                "concrete": binding,
            })

        bound_edges = []
        for edge in self.graph.edges:
            bound_edges.append({
                "source": edge.source,
                "target": edge.target,
                "edge_type": edge.edge_type,
                "weight": edge.weight,
                "source_concrete": bindings.get(edge.source, {}),
                "target_concrete": bindings.get(edge.target, {}),
            })

        return {
            "template_id": str(self.template_id),
            "name": self.name,
            "nodes": bound_nodes,
            "edges": bound_edges,
            "confidence": self.confidence,
        }
