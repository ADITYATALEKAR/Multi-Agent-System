"""CBN builder -- constructs a CausalBayesianNetwork from graph context.

Converts graph snapshots and runtime events into a wired
:class:`CausalBayesianNetwork` ready for inference.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.causal.cbn import CausalBayesianNetwork
from src.core.derived import DerivedFact
from src.core.runtime_event import RuntimeEvent

logger = structlog.get_logger(__name__)

# Default priors by node type
_TYPE_PRIORS: dict[str, float] = {
    "service": 0.5,
    "database": 0.3,
    "queue": 0.4,
}
_DEFAULT_PRIOR: float = 0.5


class CBNBuilder:
    """Builds a Causal Bayesian Network from the current state graph.

    Two entry-points:

    * :meth:`build_from_graph` -- constructs a CBN from a graph-context
      dict (nodes + edges) and optional OSG runtime events.
    * :meth:`build_from_violations` -- constructs a CBN from a list of
      violation :class:`DerivedFact` objects, linking violations that
      share entities.
    """

    # ── From graph context ────────────────────────────────────────────

    def build_from_graph(
        self,
        graph_context: dict,
        osg_events: list[RuntimeEvent] | None = None,
        scope: set[UUID] | None = None,
    ) -> CausalBayesianNetwork:
        """Construct a CBN from the provided graph context.

        Args:
            graph_context: Dict with ``"nodes"`` (list of dicts with
                ``"id"``, ``"type"``) and ``"edges"`` (list of dicts with
                ``"source"``, ``"target"``, optional ``"weight"``).
            osg_events: Optional OSG runtime events.  Causal-predecessor
                links in events produce additional edges.
            scope: If given, only include nodes whose id is in *scope*.

        Returns:
            Fully wired :class:`CausalBayesianNetwork`.
        """
        cbn = CausalBayesianNetwork()

        raw_nodes: list[dict] = graph_context.get("nodes", [])
        raw_edges: list[dict] = graph_context.get("edges", [])

        # ── Add nodes ─────────────────────────────────────────────────
        for n in raw_nodes:
            node_id = _ensure_uuid(n["id"])
            if scope is not None and node_id not in scope:
                continue
            node_type: str = n.get("type", "unknown")
            prior = _TYPE_PRIORS.get(node_type, _DEFAULT_PRIOR)
            cbn.add_node(node_id, node_type, prior)

        # ── Add edges from graph context ──────────────────────────────
        for e in raw_edges:
            src = _ensure_uuid(e["source"])
            tgt = _ensure_uuid(e["target"])
            weight = float(e.get("weight", 1.0))
            # Skip edges that reference nodes outside the scope
            if cbn.get_node(src) is None or cbn.get_node(tgt) is None:
                continue
            cbn.add_edge(src, tgt, weight)

        # ── Add edges from OSG causal predecessors ────────────────────
        if osg_events:
            self._add_osg_edges(cbn, osg_events)

        logger.info(
            "cbn_builder.built_from_graph",
            nodes=cbn.node_count,
            edges=cbn.edge_count,
        )
        return cbn

    # ── From violations ───────────────────────────────────────────────

    def build_from_violations(
        self,
        violations: list[DerivedFact],
        attention_scores: dict[UUID, float] | None = None,
    ) -> CausalBayesianNetwork:
        """Build a CBN from violation derived-facts.

        Each violation becomes a node.  Violations that share at least one
        entity id (extracted from ``payload["entity_ids"]``) are connected
        by an edge, reflecting potential causal coupling.

        Args:
            violations: List of violation :class:`DerivedFact` objects.
            attention_scores: Optional mapping of derived-fact ids to
                attention weights that influence node priors.

        Returns:
            A :class:`CausalBayesianNetwork` linking related violations.
        """
        cbn = CausalBayesianNetwork()
        attention_scores = attention_scores or {}

        # Map violation id -> set of entity ids
        entity_map: dict[UUID, set[UUID]] = {}

        for v in violations:
            vid = v.derived_id
            # Determine prior: base from confidence, modulated by attention
            base_prior = max(0.05, min(0.95, v.confidence))
            if vid in attention_scores:
                base_prior = max(
                    0.05,
                    min(0.95, base_prior * (0.5 + attention_scores[vid])),
                )

            node_type = v.derived_type.value  # e.g. "violation"
            cbn.add_node(vid, node_type, prior=base_prior)

            # Extract entity ids from payload
            raw_entities = v.payload.get("entity_ids", [])
            entity_ids: set[UUID] = set()
            for eid in raw_entities:
                entity_ids.add(_ensure_uuid(eid))
            entity_map[vid] = entity_ids

        # ── Link violations sharing entities ──────────────────────────
        vids = list(entity_map.keys())
        for i, vid_a in enumerate(vids):
            for vid_b in vids[i + 1 :]:
                shared = entity_map[vid_a] & entity_map[vid_b]
                if shared:
                    # Weight proportional to how many entities are shared
                    weight = min(1.0, len(shared) * 0.3 + 0.2)
                    cbn.add_edge(vid_a, vid_b, weight)

        logger.info(
            "cbn_builder.built_from_violations",
            violations=len(violations),
            nodes=cbn.node_count,
            edges=cbn.edge_count,
        )
        return cbn

    # ── Private helpers ───────────────────────────────────────────────

    @staticmethod
    def _add_osg_edges(
        cbn: CausalBayesianNetwork, events: list[RuntimeEvent]
    ) -> None:
        """Add edges derived from causal-predecessor links in OSG events."""
        for evt in events:
            target_id = evt.source_service
            # Ensure the event's source service exists as a node
            if cbn.get_node(target_id) is None:
                continue
            for pred_id in evt.causal_predecessors:
                if cbn.get_node(pred_id) is not None:
                    cbn.add_edge(pred_id, target_id, weight=0.8)


# ── Module-level helpers ──────────────────────────────────────────────────────


def _ensure_uuid(value: UUID | str) -> UUID:
    """Coerce *value* to :class:`UUID`, accepting str or UUID."""
    if isinstance(value, UUID):
        return value
    return UUID(str(value))
