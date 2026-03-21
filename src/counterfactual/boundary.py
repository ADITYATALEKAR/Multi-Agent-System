"""Adaptive Simulation Boundary — v3.3 Fix 2.

Determines the subgraph boundary for counterfactual replay by expanding
outward from the hypothesis entity in up to 3 rounds, guided by attention
scores and a hard node-count cap.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

import structlog

from src.core.derived import DerivedFact

log = structlog.get_logger(__name__)


class AdaptiveSimulationBoundary:
    """Computes an adaptive boundary around a hypothesis entity.

    The boundary starts with 1-hop neighbors whose attention score exceeds
    a threshold and progressively widens (up to 3 rounds) if the boundary
    is too small.  A hard cap of *max_nodes* is always enforced.
    """

    # Minimum boundary size before another expansion round is triggered.
    _MIN_BOUNDARY: int = 5

    def __init__(
        self,
        max_nodes: int = 750,
        expansion_rounds: int = 3,
        initial_threshold: float = 0.3,
    ) -> None:
        self._max_nodes = max_nodes
        self._expansion_rounds = expansion_rounds
        self._initial_threshold = initial_threshold
        self._expansion_triggers: list[str] = []

    # ── public API ───────────────────────────────────────────────────────

    def compute(
        self,
        hypothesis: DerivedFact,
        graph_context: dict,
        budget_ms: int = 5000,
    ) -> tuple[set[UUID], int]:
        """Return *(boundary_node_ids, expansion_count)*.

        Parameters
        ----------
        hypothesis:
            The derived fact (hypothesis) whose causal neighbourhood we
            are bounding.
        graph_context:
            Must contain ``"nodes"`` (list of dicts with ``"id"`` and
            optionally ``"attention_score"``) and ``"edges"`` (list of
            dicts with ``"source"`` and ``"target"``).
        budget_ms:
            Time budget hint (currently advisory).
        """
        self._expansion_triggers = []

        entity_id = hypothesis.payload.get("entity_id")
        if entity_id is None:
            log.warning("hypothesis.payload missing entity_id — empty boundary")
            return set(), 0

        entity_uuid = UUID(str(entity_id))

        # Build fast look-ups: node-id → attention, adjacency list
        nodes_raw: list[dict] = graph_context.get("nodes", [])
        edges_raw: list[dict] = graph_context.get("edges", [])

        attention: dict[UUID, float] = {}
        all_node_ids: set[UUID] = set()
        for n in nodes_raw:
            nid = UUID(str(n["id"]))
            all_node_ids.add(nid)
            attention[nid] = float(n.get("attention_score", 0.0))

        adjacency: dict[UUID, set[UUID]] = defaultdict(set)
        for e in edges_raw:
            src = UUID(str(e["source"]))
            tgt = UUID(str(e["target"]))
            adjacency[src].add(tgt)
            adjacency[tgt].add(src)

        boundary: set[UUID] = set()
        # Always include the seed entity itself.
        if entity_uuid in all_node_ids:
            boundary.add(entity_uuid)

        expansion_count = 0
        threshold = self._initial_threshold

        # ── Round 1: 1-hop neighbours with attention > threshold ─────
        expansion_count += 1
        frontier = adjacency.get(entity_uuid, set())
        for nid in frontier:
            if attention.get(nid, 0.0) > threshold:
                boundary.add(nid)
        log.debug(
            "boundary.round1",
            size=len(boundary),
            threshold=threshold,
        )

        # ── Round 2: 2-hop (if boundary still too small) ────────────
        if (
            len(boundary) < self._MIN_BOUNDARY
            and expansion_count < self._expansion_rounds
        ):
            expansion_count += 1
            trigger = (
                f"round2: boundary size {len(boundary)} < {self._MIN_BOUNDARY}, "
                f"expanding to 2-hop with threshold {threshold * 0.7:.3f}"
            )
            self._expansion_triggers.append(trigger)
            log.debug("boundary.expand_round2", trigger=trigger)
            threshold_r2 = threshold * 0.7
            hop2_frontier: set[UUID] = set()
            for nid in frontier:
                hop2_frontier.update(adjacency.get(nid, set()))
            for nid in hop2_frontier:
                if attention.get(nid, 0.0) > threshold_r2:
                    boundary.add(nid)
            frontier = hop2_frontier

        # ── Round 3: 3-hop (if boundary *still* too small) ──────────
        if (
            len(boundary) < self._MIN_BOUNDARY
            and expansion_count < self._expansion_rounds
        ):
            expansion_count += 1
            trigger = (
                f"round3: boundary size {len(boundary)} < {self._MIN_BOUNDARY}, "
                f"expanding to 3-hop with threshold {threshold * 0.5:.3f}"
            )
            self._expansion_triggers.append(trigger)
            log.debug("boundary.expand_round3", trigger=trigger)
            threshold_r3 = threshold * 0.5
            hop3_frontier: set[UUID] = set()
            for nid in frontier:
                hop3_frontier.update(adjacency.get(nid, set()))
            for nid in hop3_frontier:
                if attention.get(nid, 0.0) > threshold_r3:
                    boundary.add(nid)

        # ── Hard cap ────────────────────────────────────────────────
        if len(boundary) > self._max_nodes:
            # Keep the highest-attention nodes up to the cap, always
            # preserving the seed entity.
            scored = sorted(
                boundary - {entity_uuid},
                key=lambda nid: attention.get(nid, 0.0),
                reverse=True,
            )
            boundary = {entity_uuid} | set(scored[: self._max_nodes - 1])
            trigger = f"hard_cap: trimmed to {self._max_nodes} nodes"
            self._expansion_triggers.append(trigger)
            log.info("boundary.hard_cap", max_nodes=self._max_nodes)

        log.info(
            "boundary.computed",
            boundary_size=len(boundary),
            expansion_count=expansion_count,
        )
        return boundary, expansion_count

    def get_expansion_triggers(self) -> list[str]:
        """Return the log of why each expansion round was triggered."""
        return list(self._expansion_triggers)
