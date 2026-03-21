"""Graph query endpoints for the state graph."""

from __future__ import annotations

import structlog
from fastapi import APIRouter
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["graph"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class GraphNode(BaseModel):
    node_id: str
    label: str = ""
    node_type: str = ""
    properties: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    source: str
    target: str
    relation: str = ""


class SubgraphResponse(BaseModel):
    root_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/graph/subgraph/{node_id}")
def get_subgraph(node_id: str) -> SubgraphResponse:
    """Return the local subgraph surrounding *node_id*.

    In production this queries the state graph backend; currently
    returns a stub root node so the endpoint is exercisable.
    """
    log.info("subgraph_requested", node_id=node_id)
    root_node = GraphNode(
        node_id=node_id,
        label=node_id,
        node_type="unknown",
    )
    return SubgraphResponse(
        root_id=node_id,
        nodes=[root_node],
        edges=[],
    )
