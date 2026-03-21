"""Failure propagation analysis in the OSG.

Implements:
- FailurePropagationInferrer: infers failure propagation chains
- BFS/DFS propagation through service graph
- Anomaly-aware scoring
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

import structlog

from src.core.runtime_event import EventStatus, RuntimeEvent
from src.osg.materializer import OSGMaterializer

logger = structlog.get_logger()


class PropagationChain:
    """A failure propagation chain through services."""

    __slots__ = ("root_event_id", "affected_services", "chain_events", "max_depth", "confidence")

    def __init__(self, root_event_id: UUID) -> None:
        self.root_event_id = root_event_id
        self.affected_services: list[UUID] = []
        self.chain_events: list[UUID] = []
        self.max_depth: int = 0
        self.confidence: float = 0.0


class FailurePropagationInferrer:
    """Infers failure propagation paths in the Operational State Graph.

    Given a time window of events, identifies how failures propagate
    through the service graph using causal predecessors, temporal
    proximity, and anomaly scores.
    """

    def __init__(
        self,
        osg: OSGMaterializer,
        temporal_window_ms: float = 5000.0,
        anomaly_threshold: float = 0.3,
    ) -> None:
        self._osg = osg
        self._temporal_window_ms = temporal_window_ms
        self._anomaly_threshold = anomaly_threshold

    def infer_propagation(self, root_event_id: UUID) -> PropagationChain:
        """Infer the failure propagation chain from a root failure event.

        Uses BFS through causal predecessors and temporally correlated failures.
        """
        chain = PropagationChain(root_event_id)
        events = self._osg.get_events()

        # Build event index
        event_map: dict[UUID, RuntimeEvent] = {e.event_id: e for e in events}
        root = event_map.get(root_event_id)
        if root is None:
            return chain

        # BFS from root
        visited: set[UUID] = set()
        queue: deque[tuple[UUID, int]] = deque([(root_event_id, 0)])
        visited.add(root_event_id)

        # Build reverse causal index: event -> events that list it as predecessor
        causal_children: dict[UUID, list[UUID]] = defaultdict(list)
        for e in events:
            for pred in e.causal_predecessors:
                causal_children[pred].append(e.event_id)

        while queue:
            current_id, depth = queue.popleft()
            current = event_map.get(current_id)
            if current is None:
                continue

            chain.chain_events.append(current_id)
            if current.source_service not in chain.affected_services:
                chain.affected_services.append(current.source_service)
            if current.target_service and current.target_service not in chain.affected_services:
                chain.affected_services.append(current.target_service)
            chain.max_depth = max(chain.max_depth, depth)

            # Follow causal children
            for child_id in causal_children.get(current_id, []):
                if child_id not in visited:
                    visited.add(child_id)
                    queue.append((child_id, depth + 1))

            # Follow temporally correlated failures
            if current.target_service:
                for e in self._osg.get_events_for_service(current.target_service):
                    if (
                        e.event_id not in visited
                        and e.status in (EventStatus.FAILURE, EventStatus.TIMEOUT)
                        and abs((e.timestamp - current.timestamp).total_seconds() * 1000)
                        <= self._temporal_window_ms
                    ):
                        visited.add(e.event_id)
                        queue.append((e.event_id, depth + 1))

        # Compute confidence based on chain properties
        chain.confidence = self._compute_confidence(chain, event_map)
        return chain

    def infer_failure_propagation(
        self, window_start: datetime, window_end: datetime
    ) -> list[RuntimeEvent]:
        """Infer propagation events in a time window (plan API signature).

        Returns synthesized FAILURE_PROPAGATION events for detected chains.
        """
        from src.core.runtime_event import EventType

        failure_events = self._osg.get_failure_events(window_start, window_end)
        if not failure_events:
            return []

        # Find root failures (no causal predecessors or predecessors outside window)
        event_ids_in_window = {e.event_id for e in failure_events}
        roots = [
            e
            for e in failure_events
            if not e.causal_predecessors or not e.causal_predecessors.intersection(event_ids_in_window)
        ]

        propagation_events: list[RuntimeEvent] = []
        seen_chains: set[UUID] = set()

        for root in roots:
            chain = self.infer_propagation(root.event_id)
            if chain.max_depth < 1:
                continue

            for svc_id in chain.affected_services:
                if svc_id in seen_chains or svc_id == root.source_service:
                    continue
                seen_chains.add(svc_id)

                prop_event = RuntimeEvent(
                    event_type=EventType.FAILURE_PROPAGATION,
                    source_service=root.source_service,
                    target_service=svc_id,
                    timestamp=root.timestamp,
                    status=EventStatus.FAILURE,
                    trace_id=root.trace_id,
                    causal_predecessors={root.event_id},
                    anomaly_score=min(1.0, chain.confidence),
                )
                propagation_events.append(prop_event)

        logger.debug(
            "failure_propagation_inferred",
            roots=len(roots),
            propagation_events=len(propagation_events),
        )
        return propagation_events

    def _compute_confidence(
        self, chain: PropagationChain, event_map: dict[UUID, RuntimeEvent]
    ) -> float:
        if not chain.chain_events:
            return 0.0

        # Base confidence from chain length
        depth_score = min(1.0, chain.max_depth / 5.0) * 0.4

        # Anomaly score contribution
        anomaly_scores = []
        for eid in chain.chain_events:
            e = event_map.get(eid)
            if e and e.anomaly_score > self._anomaly_threshold:
                anomaly_scores.append(e.anomaly_score)
        anomaly_avg = sum(anomaly_scores) / len(anomaly_scores) if anomaly_scores else 0.0
        anomaly_score = anomaly_avg * 0.3

        # Causal evidence score
        causal_count = sum(
            1
            for eid in chain.chain_events
            if event_map.get(eid) and event_map[eid].causal_predecessors
        )
        causal_ratio = causal_count / len(chain.chain_events) if chain.chain_events else 0.0
        causal_score = causal_ratio * 0.3

        return min(1.0, depth_score + anomaly_score + causal_score)
