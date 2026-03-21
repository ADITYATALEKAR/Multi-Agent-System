"""Temporal ordering of runtime events.

Implements:
- Topological sort respecting causal predecessors
- Timestamp-based tiebreaking
- Lamport-style logical clock for partial ordering
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog

from src.core.runtime_event import RuntimeEvent

logger = structlog.get_logger()


class TemporalOrderer:
    """Orders runtime events by causal and temporal relationships.

    Combines causal predecessor constraints with timestamp ordering.
    Uses topological sort on the causal DAG, with timestamp tiebreaking
    for events with no causal relationship.
    """

    def order(self, events: list[RuntimeEvent]) -> list[RuntimeEvent]:
        """Order events respecting causal constraints and temporal order.

        Algorithm:
        1. Build causal DAG from causal_predecessors
        2. Topological sort (Kahn's algorithm)
        3. Timestamp tiebreaking within each topological level
        """
        if len(events) <= 1:
            return list(events)

        event_map: dict[UUID, RuntimeEvent] = {e.event_id: e for e in events}
        event_ids = set(event_map.keys())

        # Build adjacency list and in-degree (only for events in this set)
        adj: dict[UUID, list[UUID]] = defaultdict(list)
        in_degree: dict[UUID, int] = {eid: 0 for eid in event_ids}

        for e in events:
            for pred_id in e.causal_predecessors:
                if pred_id in event_ids:
                    adj[pred_id].append(e.event_id)
                    in_degree[e.event_id] += 1

        # Kahn's algorithm with timestamp-sorted queue
        queue: list[RuntimeEvent] = sorted(
            [event_map[eid] for eid, deg in in_degree.items() if deg == 0],
            key=lambda e: e.timestamp,
        )
        result: list[RuntimeEvent] = []

        while queue:
            # Take the earliest event
            current = queue.pop(0)
            result.append(current)

            # Process children
            children = adj.get(current.event_id, [])
            newly_ready: list[RuntimeEvent] = []
            for child_id in children:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    newly_ready.append(event_map[child_id])

            # Insert newly ready events in timestamp order
            if newly_ready:
                newly_ready.sort(key=lambda e: e.timestamp)
                # Merge into queue maintaining sort
                merged = sorted(queue + newly_ready, key=lambda e: e.timestamp)
                queue = merged

        # If some events were not reachable (cycles), append by timestamp
        if len(result) < len(events):
            remaining = [e for e in events if e.event_id not in {r.event_id for r in result}]
            remaining.sort(key=lambda e: e.timestamp)
            result.extend(remaining)

        return result

    def compute_logical_clocks(self, events: list[RuntimeEvent]) -> dict[UUID, int]:
        """Compute Lamport-style logical clocks for events.

        Returns:
            Mapping of event_id -> logical clock value.
        """
        ordered = self.order(events)
        event_map = {e.event_id: e for e in ordered}
        clocks: dict[UUID, int] = {}
        event_ids = set(event_map.keys())

        for e in ordered:
            # Clock = max(predecessor clocks) + 1
            pred_clocks = [
                clocks.get(pid, 0)
                for pid in e.causal_predecessors
                if pid in event_ids
            ]
            clocks[e.event_id] = (max(pred_clocks) if pred_clocks else 0) + 1

        return clocks

    def group_by_trace(self, events: list[RuntimeEvent]) -> dict[str, list[RuntimeEvent]]:
        """Group events by trace_id and order each group."""
        groups: dict[str, list[RuntimeEvent]] = defaultdict(list)
        no_trace: list[RuntimeEvent] = []

        for e in events:
            if e.trace_id:
                groups[e.trace_id].append(e)
            else:
                no_trace.append(e)

        result: dict[str, list[RuntimeEvent]] = {}
        for trace_id, group in groups.items():
            result[trace_id] = self.order(group)

        if no_trace:
            result["_no_trace"] = self.order(no_trace)

        return result
