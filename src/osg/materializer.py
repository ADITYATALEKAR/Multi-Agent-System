"""OSG materializer: converts runtime events to graph structure.

Implements:
- Event ingestion and graph materialization
- Service node management
- Causal chain pinning (v3.3 B4) — pinned events survive window eviction
- Time-windowed event storage with configurable window
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID, uuid4

import structlog

from src.core.runtime_event import EventStatus, EventType, RuntimeEvent

logger = structlog.get_logger()


class ServiceNode:
    """A service node in the Operational State Graph."""

    __slots__ = ("service_id", "name", "status", "last_seen", "event_count", "failure_count")

    def __init__(self, service_id: UUID, name: str = "") -> None:
        self.service_id = service_id
        self.name = name
        self.status: str = "healthy"
        self.last_seen: Optional[datetime] = None
        self.event_count: int = 0
        self.failure_count: int = 0

    def update_from_event(self, event: RuntimeEvent) -> None:
        self.last_seen = event.timestamp
        self.event_count += 1
        if event.status in (EventStatus.FAILURE, EventStatus.TIMEOUT):
            self.failure_count += 1
            if self.failure_count > 3:
                self.status = "degraded"
        elif event.status == EventStatus.SUCCESS and self.failure_count == 0:
            self.status = "healthy"


class ServiceEdge:
    """An edge between two services in the OSG."""

    __slots__ = ("source", "target", "event_types", "call_count", "failure_count", "last_seen")

    def __init__(self, source: UUID, target: UUID) -> None:
        self.source = source
        self.target = target
        self.event_types: set[str] = set()
        self.call_count: int = 0
        self.failure_count: int = 0
        self.last_seen: Optional[datetime] = None

    def update_from_event(self, event: RuntimeEvent) -> None:
        self.event_types.add(event.event_type.value)
        self.call_count += 1
        if event.status in (EventStatus.FAILURE, EventStatus.TIMEOUT):
            self.failure_count += 1
        self.last_seen = event.timestamp


class OSGMaterializer:
    """Materializes runtime events into the Operational State Graph.

    Features:
    - Service node and edge tracking
    - Time-windowed event storage (default 1 hour)
    - Causal chain pinning (v3.3 B4): pinned events exempt from eviction
    - Failure propagation event generation
    """

    def __init__(
        self,
        window_duration: timedelta = timedelta(hours=1),
    ) -> None:
        self._window_duration = window_duration
        self._events: list[RuntimeEvent] = []
        self._nodes: dict[UUID, ServiceNode] = {}
        self._edges: dict[tuple[UUID, UUID], ServiceEdge] = {}
        # v3.3 B4: causal chain pinning
        self._pinned_event_ids: set[UUID] = set()
        # Index: trace_id -> list of events
        self._trace_index: dict[str, list[RuntimeEvent]] = defaultdict(list)
        # Index: service_id -> list of events
        self._service_index: dict[UUID, list[RuntimeEvent]] = defaultdict(list)

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    @property
    def event_count(self) -> int:
        return len(self._events)

    @property
    def pinned_count(self) -> int:
        return len(self._pinned_event_ids)

    def get_node(self, service_id: UUID) -> Optional[ServiceNode]:
        return self._nodes.get(service_id)

    def get_edge(self, source: UUID, target: UUID) -> Optional[ServiceEdge]:
        return self._edges.get((source, target))

    def get_events(self) -> list[RuntimeEvent]:
        return list(self._events)

    def get_events_for_service(self, service_id: UUID) -> list[RuntimeEvent]:
        return list(self._service_index.get(service_id, []))

    def get_events_in_window(
        self, start: datetime, end: datetime
    ) -> list[RuntimeEvent]:
        return [e for e in self._events if start <= e.timestamp <= end]

    def process_event(self, event: RuntimeEvent) -> None:
        """Materialize a single runtime event into the OSG (synchronous API per plan)."""
        self._events.append(event)

        # Update source node
        src_node = self._ensure_node(event.source_service)
        src_node.update_from_event(event)
        self._service_index[event.source_service].append(event)

        # Update target node + edge if present
        if event.target_service is not None:
            tgt_node = self._ensure_node(event.target_service)
            tgt_node.update_from_event(event)
            self._service_index[event.target_service].append(event)

            edge = self._ensure_edge(event.source_service, event.target_service)
            edge.update_from_event(event)

        # Index by trace
        if event.trace_id:
            self._trace_index[event.trace_id].append(event)

        # Auto-pin causal predecessors (v3.3 B4)
        if event.causal_predecessors:
            for pred_id in event.causal_predecessors:
                self.pin_event(pred_id)

        logger.debug(
            "osg_event_materialized",
            event_id=str(event.event_id),
            event_type=event.event_type.value,
        )

    def pin_event(self, event_id: UUID) -> bool:
        """Pin an event so it survives window eviction (v3.3 B4)."""
        self._pinned_event_ids.add(event_id)
        return True

    def unpin_event(self, event_id: UUID) -> bool:
        """Remove pin from an event."""
        self._pinned_event_ids.discard(event_id)
        return True

    def is_pinned(self, event_id: UUID) -> bool:
        return event_id in self._pinned_event_ids

    def evict_window(self, now: Optional[datetime] = None) -> int:
        """Evict events outside the time window. Pinned events are exempt (v3.3 B4).

        Returns:
            Number of events evicted.
        """
        if now is None:
            now = datetime.utcnow()

        cutoff = now - self._window_duration
        before_count = len(self._events)

        # Keep events that are either within window OR pinned
        kept: list[RuntimeEvent] = []
        for e in self._events:
            if e.timestamp >= cutoff or e.event_id in self._pinned_event_ids:
                kept.append(e)

        self._events = kept
        evicted = before_count - len(kept)

        # Rebuild indices after eviction
        if evicted > 0:
            self._rebuild_indices()

        logger.debug("osg_window_eviction", evicted=evicted, remaining=len(self._events))
        return evicted

    def get_trace_events(self, trace_id: str) -> list[RuntimeEvent]:
        return list(self._trace_index.get(trace_id, []))

    def get_failure_events(
        self, start: Optional[datetime] = None, end: Optional[datetime] = None
    ) -> list[RuntimeEvent]:
        """Get all failure/timeout events in a time window."""
        result = []
        for e in self._events:
            if e.status not in (EventStatus.FAILURE, EventStatus.TIMEOUT):
                continue
            if start and e.timestamp < start:
                continue
            if end and e.timestamp > end:
                continue
            result.append(e)
        return result

    def snapshot(self) -> dict:
        """Return a serializable snapshot of the current OSG state."""
        return {
            "nodes": [
                {
                    "service_id": str(n.service_id),
                    "name": n.name,
                    "status": n.status,
                    "event_count": n.event_count,
                    "failure_count": n.failure_count,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "source": str(e.source),
                    "target": str(e.target),
                    "call_count": e.call_count,
                    "failure_count": e.failure_count,
                    "event_types": list(e.event_types),
                }
                for e in self._edges.values()
            ],
        }

    # ── Private helpers ─────────────────────────────────────────

    def _ensure_node(self, service_id: UUID) -> ServiceNode:
        if service_id not in self._nodes:
            self._nodes[service_id] = ServiceNode(service_id)
        return self._nodes[service_id]

    def _ensure_edge(self, source: UUID, target: UUID) -> ServiceEdge:
        key = (source, target)
        if key not in self._edges:
            self._edges[key] = ServiceEdge(source, target)
        return self._edges[key]

    def _rebuild_indices(self) -> None:
        self._trace_index.clear()
        self._service_index.clear()
        for e in self._events:
            if e.trace_id:
                self._trace_index[e.trace_id].append(e)
            self._service_index[e.source_service].append(e)
            if e.target_service:
                self._service_index[e.target_service].append(e)
