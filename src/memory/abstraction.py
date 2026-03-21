"""Template abstraction: Approximate Maximum Common Subgraph (MCS).

Phase 3 — Builds CausalTemplates from clusters of similar episodes.

The TemplateAbstractor uses an approximate MCS algorithm to find the
largest common causal substructure shared by a set of episodes, then
produces a CausalTemplate that generalises the pattern.

Safeguard: MCS similarity threshold 0.6.  Templates with confidence
< 0.3 after 10 matches are archived.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import UUID

import structlog

from src.memory.causal_template import (
    AbstractEdge,
    AbstractGraph,
    AbstractNode,
    CausalTemplate,
)
from src.memory.fingerprint import wl_hash
from src.memory.types import Episode

log = structlog.get_logger(__name__)

_MCS_SIMILARITY_THRESHOLD: float = 0.6
_ARCHIVE_CONFIDENCE_THRESHOLD: float = 0.3
_ARCHIVE_MATCH_THRESHOLD: int = 10
_MIN_EPISODES_FOR_TEMPLATE: int = 3


# ---------------------------------------------------------------------------
# ApproximateMCS
# ---------------------------------------------------------------------------


class ApproximateMCS:
    """Approximate Maximum Common Subgraph finder.

    Uses a greedy label-matching heuristic:
    1. Build node-type frequency maps for each episode's causal graph.
    2. Find common node types across all episodes.
    3. Build edges that are common across episodes.
    4. Return the common subgraph.

    This is O(V+E) per episode, not the NP-hard exact MCS.
    """

    def __init__(self, similarity_threshold: float = _MCS_SIMILARITY_THRESHOLD) -> None:
        self._threshold = similarity_threshold

    def find_common_subgraph(
        self,
        graphs: list[dict[str, Any]],
    ) -> AbstractGraph | None:
        """Find the approximate MCS across a list of graphs.

        Each graph dict should have:
            - "nodes": list of dicts with "type" and optional "role"
            - "edges": list of dicts with "source_type", "target_type", "edge_type"

        Returns:
            An AbstractGraph representing the common structure, or None
            if similarity is below threshold.
        """
        if not graphs:
            return None

        n_graphs = len(graphs)

        # Step 1: Find common node types
        # Count how many graphs contain each node type
        type_presence: dict[str, int] = defaultdict(int)
        type_roles: dict[str, list[str]] = defaultdict(list)

        for g in graphs:
            seen_types: set[str] = set()
            for node in g.get("nodes", []):
                ntype = node.get("type", "unknown")
                if ntype not in seen_types:
                    seen_types.add(ntype)
                    type_presence[ntype] += 1
                    role = node.get("role", "")
                    if role:
                        type_roles[ntype].append(role)

        # Keep types present in >= threshold fraction of graphs
        min_count = max(1, int(n_graphs * self._threshold))
        common_types = {t for t, c in type_presence.items() if c >= min_count}

        if not common_types:
            return None

        # Step 2: Build abstract nodes
        abstract_nodes: list[AbstractNode] = []
        type_to_node_id: dict[str, str] = {}
        for ntype in sorted(common_types):
            roles = type_roles.get(ntype, [])
            # Pick most common role
            role = ""
            if roles:
                role_counts: dict[str, int] = defaultdict(int)
                for r in roles:
                    role_counts[r] += 1
                role = max(role_counts, key=role_counts.get)  # type: ignore[arg-type]

            node = AbstractNode(
                node_type=ntype,
                role=role,
                label=ntype,
            )
            abstract_nodes.append(node)
            type_to_node_id[ntype] = node.node_id

        # Step 3: Find common edges
        # An edge is "common" if the (source_type, target_type, edge_type) triple
        # appears in >= threshold fraction of graphs
        edge_presence: dict[tuple[str, str, str], int] = defaultdict(int)
        edge_weights: dict[tuple[str, str, str], list[float]] = defaultdict(list)

        for g in graphs:
            seen_edges: set[tuple[str, str, str]] = set()
            for edge in g.get("edges", []):
                src_type = edge.get("source_type", "")
                tgt_type = edge.get("target_type", "")
                etype = edge.get("edge_type", "causes")
                key = (src_type, tgt_type, etype)
                if key not in seen_edges and src_type in common_types and tgt_type in common_types:
                    seen_edges.add(key)
                    edge_presence[key] += 1
                    edge_weights[key].append(edge.get("weight", 1.0))

        abstract_edges: list[AbstractEdge] = []
        for (src_type, tgt_type, etype), count in edge_presence.items():
            if count >= min_count:
                src_id = type_to_node_id.get(src_type)
                tgt_id = type_to_node_id.get(tgt_type)
                if src_id and tgt_id:
                    weights = edge_weights[(src_type, tgt_type, etype)]
                    avg_weight = sum(weights) / len(weights)
                    abstract_edges.append(AbstractEdge(
                        source=src_id,
                        target=tgt_id,
                        edge_type=etype,
                        weight=avg_weight,
                    ))

        # Step 4: Check similarity
        total_possible_nodes = len(set().union(
            *(set(n.get("type", "") for n in g.get("nodes", [])) for g in graphs)
        ))
        similarity = len(common_types) / max(total_possible_nodes, 1)

        if similarity < self._threshold:
            log.debug(
                "mcs.below_threshold",
                similarity=round(similarity, 3),
                threshold=self._threshold,
            )
            return None

        return AbstractGraph(nodes=abstract_nodes, edges=abstract_edges)


# ---------------------------------------------------------------------------
# TemplateAbstractor
# ---------------------------------------------------------------------------


class TemplateAbstractor:
    """Build CausalTemplates from clusters of similar episodes.

    Requires at least ``min_episodes`` (default 3) to create a template.
    Uses ApproximateMCS to find the common causal structure.
    """

    def __init__(
        self,
        *,
        min_episodes: int = _MIN_EPISODES_FOR_TEMPLATE,
        similarity_threshold: float = _MCS_SIMILARITY_THRESHOLD,
    ) -> None:
        self._min_episodes = min_episodes
        self._mcs = ApproximateMCS(similarity_threshold)

    def abstract(self, episodes: list[Episode]) -> CausalTemplate | None:
        """Build a CausalTemplate from a cluster of similar episodes.

        Each episode's ``metadata`` should contain a ``"causal_graph"`` key
        with ``{"nodes": [...], "edges": [...]}``.

        Returns:
            A CausalTemplate, or None if insufficient episodes or
            similarity below threshold.
        """
        if len(episodes) < self._min_episodes:
            log.debug(
                "abstractor.insufficient_episodes",
                count=len(episodes),
                min_required=self._min_episodes,
            )
            return None

        # Extract causal graphs from episode metadata
        graphs: list[dict[str, Any]] = []
        for ep in episodes:
            cg = ep.metadata.get("causal_graph")
            if cg and isinstance(cg, dict):
                graphs.append(cg)

        if len(graphs) < self._min_episodes:
            return None

        # Find common subgraph
        abstract_graph = self._mcs.find_common_subgraph(graphs)
        if abstract_graph is None:
            return None

        # Compute fingerprint from abstract graph
        fp_nodes = [{"label": n.node_type} for n in abstract_graph.nodes]
        fp_edges = []
        node_ids = [n.node_id for n in abstract_graph.nodes]
        for edge in abstract_graph.edges:
            src_idx = next((i for i, n in enumerate(abstract_graph.nodes) if n.node_id == edge.source), -1)
            tgt_idx = next((i for i, n in enumerate(abstract_graph.nodes) if n.node_id == edge.target), -1)
            if src_idx >= 0 and tgt_idx >= 0:
                fp_edges.append((src_idx, tgt_idx))

        fingerprint = wl_hash(fp_nodes, fp_edges)

        # Collect metadata
        all_categories: set[str] = set()
        for ep in episodes:
            all_categories.update(ep.law_categories)

        # Confidence based on cluster size and resolution rate
        resolved = sum(1 for ep in episodes if ep.confidence > 0.5)
        base_confidence = resolved / len(episodes) if episodes else 0.0

        template = CausalTemplate(
            name=f"Template from {len(episodes)} episodes",
            description=(
                f"Abstracted from {len(episodes)} episodes. "
                f"Common structure: {abstract_graph.node_count} nodes, "
                f"{abstract_graph.edge_count} edges."
            ),
            graph=abstract_graph,
            law_categories=all_categories,
            source_episodes=[ep.episode_id for ep in episodes],
            fingerprint=fingerprint,
            confidence=min(base_confidence, 1.0),
        )

        log.info(
            "abstractor.template_created",
            template_id=str(template.template_id),
            episodes=len(episodes),
            nodes=abstract_graph.node_count,
            edges=abstract_graph.edge_count,
            confidence=round(template.confidence, 3),
        )
        return template

    @staticmethod
    def should_archive(template: CausalTemplate) -> bool:
        """Check if a template should be archived due to low confidence.

        A template is archived if its confidence drops below 0.3 after
        10+ matches.
        """
        return (
            template.match_count >= _ARCHIVE_MATCH_THRESHOLD
            and template.confidence < _ARCHIVE_CONFIDENCE_THRESHOLD
        )
