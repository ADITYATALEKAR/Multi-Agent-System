"""Causal Bayesian Network -- core CBN data structure and inference.

Provides a directed graph encoding causal relationships between system
entities, with simplified loopy belief-propagation for posterior inference.
"""

from __future__ import annotations

from collections import deque
from typing import Optional
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# ── Internal data models ─────────────────────────────────────────────────────

MAX_NODES = 200


class CBNNode(BaseModel):
    """A single variable node in the Causal Bayesian Network."""

    node_id: UUID
    node_type: str
    prior: float = Field(ge=0.0, le=1.0, default=0.5)


class CBNEdge(BaseModel):
    """A directed causal edge in the CBN."""

    source: UUID
    target: UUID
    weight: float = Field(ge=0.0, default=1.0)


# ── CausalBayesianNetwork ────────────────────────────────────────────────────


class CausalBayesianNetwork:
    """Directed acyclic graph encoding causal relationships and CPDs.

    Supports simplified loopy belief-propagation for approximate posterior
    inference given observed evidence.  Network size is capped at
    :data:`MAX_NODES` to keep inference tractable within a single
    planning cycle.
    """

    def __init__(self) -> None:
        self._nodes: dict[UUID, CBNNode] = {}
        # Adjacency: parent -> set of children
        self._children: dict[UUID, set[UUID]] = {}
        # Reverse adjacency: child -> set of parents
        self._parents: dict[UUID, set[UUID]] = {}
        # Edge metadata keyed by (source, target)
        self._edges: dict[tuple[UUID, UUID], CBNEdge] = {}

    # ── Mutators ──────────────────────────────────────────────────────

    def add_node(
        self, node_id: UUID, node_type: str, prior: float = 0.5
    ) -> None:
        """Add a variable node to the network.

        Args:
            node_id: Unique identifier for the node.
            node_type: Semantic type (e.g. 'service', 'database').
            prior: Prior probability, defaults to 0.5.

        Raises:
            ValueError: If the network already has MAX_NODES nodes.
        """
        if node_id in self._nodes:
            logger.debug("cbn.node_exists", node_id=str(node_id))
            return

        if len(self._nodes) >= MAX_NODES:
            raise ValueError(
                f"CBN node cap reached ({MAX_NODES}). "
                "Reduce scope before adding more nodes."
            )

        node = CBNNode(node_id=node_id, node_type=node_type, prior=prior)
        self._nodes[node_id] = node
        self._children.setdefault(node_id, set())
        self._parents.setdefault(node_id, set())
        logger.debug(
            "cbn.node_added",
            node_id=str(node_id),
            node_type=node_type,
            prior=prior,
        )

    def add_edge(
        self, source: UUID, target: UUID, weight: float = 1.0
    ) -> None:
        """Add a directed causal edge from *source* to *target*.

        Both endpoints must already exist in the network.

        Args:
            source: Parent node id.
            target: Child node id.
            weight: Strength / confidence of the causal link.

        Raises:
            KeyError: If either endpoint is missing from the network.
        """
        if source not in self._nodes:
            raise KeyError(f"Source node {source} not in CBN")
        if target not in self._nodes:
            raise KeyError(f"Target node {target} not in CBN")

        key = (source, target)
        if key in self._edges:
            logger.debug("cbn.edge_exists", source=str(source), target=str(target))
            return

        edge = CBNEdge(source=source, target=target, weight=weight)
        self._edges[key] = edge
        self._children[source].add(target)
        self._parents[target].add(source)
        logger.debug(
            "cbn.edge_added",
            source=str(source),
            target=str(target),
            weight=weight,
        )

    # ── Queries ───────────────────────────────────────────────────────

    def get_node(self, node_id: UUID) -> Optional[dict]:
        """Return node info dict or ``None`` if the node is absent."""
        node = self._nodes.get(node_id)
        if node is None:
            return None
        return {
            "node_id": node.node_id,
            "node_type": node.node_type,
            "prior": node.prior,
        }

    def get_parents(self, node_id: UUID) -> list[UUID]:
        """Return the parent (cause) node ids for *node_id*."""
        return list(self._parents.get(node_id, set()))

    def get_children(self, node_id: UUID) -> list[UUID]:
        """Return the child (effect) node ids for *node_id*."""
        return list(self._children.get(node_id, set()))

    @property
    def node_count(self) -> int:
        """Number of nodes in the CBN."""
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        """Number of directed edges in the CBN."""
        return len(self._edges)

    def all_node_ids(self) -> list[UUID]:
        """Return a list of all node ids in the network."""
        return list(self._nodes.keys())

    # ── Topological order ─────────────────────────────────────────────

    def topological_order(self) -> list[UUID]:
        """Return nodes in topological order (Kahn's algorithm).

        Raises:
            ValueError: If the graph contains a cycle.
        """
        in_degree: dict[UUID, int] = {
            nid: len(self._parents.get(nid, set())) for nid in self._nodes
        }
        queue: deque[UUID] = deque(
            nid for nid, deg in in_degree.items() if deg == 0
        )
        order: list[UUID] = []

        while queue:
            nid = queue.popleft()
            order.append(nid)
            for child in self._children.get(nid, set()):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) != len(self._nodes):
            raise ValueError("CBN contains a cycle; topological sort impossible")

        return order

    # ── Inference (simplified loopy belief propagation) ───────────────

    def infer(
        self,
        evidence: dict[UUID, float],
        max_iterations: int = 20,
        tolerance: float = 1e-6,
    ) -> dict[UUID, float]:
        """Run approximate posterior inference via iterative message passing.

        Evidence nodes are clamped to their observed values.  Non-evidence
        nodes update their belief using:

            belief[X] = prior[X] * product(message[parent -> X])

        where each incoming message is:

            msg[P -> X] = weight(P, X) * belief[P] + (1 - weight(P, X)) * 0.5

        The procedure iterates until convergence or *max_iterations*.

        Args:
            evidence: Mapping of observed node IDs to clamped values
                      in [0, 1].
            max_iterations: Cap on iteration rounds.
            tolerance: Convergence threshold on max belief change.

        Returns:
            Mapping of every node ID to its posterior probability.
        """
        if not self._nodes:
            return {}

        # Initialise beliefs from priors
        belief: dict[UUID, float] = {
            nid: node.prior for nid, node in self._nodes.items()
        }

        # Clamp evidence
        for nid, val in evidence.items():
            if nid in belief:
                belief[nid] = val

        # Iterative message-passing in topological order (when possible)
        try:
            order = self.topological_order()
        except ValueError:
            # Fall back to arbitrary order if cycle detected
            order = list(self._nodes.keys())

        for iteration in range(max_iterations):
            max_delta = 0.0

            for nid in order:
                # Evidence nodes stay clamped
                if nid in evidence:
                    continue

                # Compute incoming messages from parents
                parents = self._parents.get(nid, set())
                if not parents:
                    # Root node -- belief stays at prior
                    continue

                product = self._nodes[nid].prior
                for parent_id in parents:
                    edge = self._edges.get((parent_id, nid))
                    w = edge.weight if edge else 1.0
                    # Message: weighted mix of parent belief and neutral 0.5
                    msg = w * belief[parent_id] + (1.0 - w) * 0.5
                    product *= msg

                # Normalise into [0, 1] -- clamp to avoid degenerate values
                new_belief = max(0.0, min(1.0, product))
                delta = abs(new_belief - belief[nid])
                if delta > max_delta:
                    max_delta = delta
                belief[nid] = new_belief

            if max_delta < tolerance:
                logger.debug(
                    "cbn.infer.converged",
                    iterations=iteration + 1,
                    max_delta=max_delta,
                )
                break
        else:
            logger.debug(
                "cbn.infer.max_iterations",
                iterations=max_iterations,
                max_delta=max_delta,
            )

        # Re-clamp evidence (defensive)
        for nid, val in evidence.items():
            if nid in belief:
                belief[nid] = val

        return belief
