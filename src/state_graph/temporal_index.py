"""Temporal Index: Python API over in-memory sorted index.

v3.2 Primitive 1 + v3.3 Fix 1.
Provides entity_timeline, global_timeline, and osg_timeline queries.
Uses sorted list with bisect for efficient time-range queries.

Performance targets: entity_state_at <5ms hot, <50ms cold on 10M deltas.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, order=True)
class TemporalEntry:
    """A single entry in the temporal index, sorted by timestamp."""

    timestamp: datetime
    entity_id: UUID = field(compare=False)
    sequence_number: int = field(default=0, compare=False)
    delta_id: Optional[UUID] = field(default=None, compare=False)


class TemporalIndex:
    """In-memory temporal index for time-range queries.

    Maintains sorted lists for efficient range lookups.
    Supports three timeline types: entity, global, and OSG.
    """

    def __init__(self) -> None:
        self._entries: list[TemporalEntry] = []
        self._entity_index: dict[UUID, list[TemporalEntry]] = {}
        self._timestamps: list[datetime] = []  # parallel sorted list for bisect

    def insert(
        self,
        timestamp: datetime,
        entity_id: UUID,
        sequence_number: int = 0,
        delta_id: Optional[UUID] = None,
    ) -> None:
        """Insert an entity event into the temporal index.

        Maintains sorted order via bisect insertion.
        """
        entry = TemporalEntry(
            timestamp=timestamp,
            entity_id=entity_id,
            sequence_number=sequence_number,
            delta_id=delta_id,
        )

        # Insert into global sorted list
        idx = bisect.bisect_right(self._timestamps, timestamp)
        self._timestamps.insert(idx, timestamp)
        self._entries.insert(idx, entry)

        # Insert into per-entity index
        if entity_id not in self._entity_index:
            self._entity_index[entity_id] = []
        entity_entries = self._entity_index[entity_id]
        entity_ts = [e.timestamp for e in entity_entries]
        eidx = bisect.bisect_right(entity_ts, timestamp)
        entity_entries.insert(eidx, entry)

    def query_range(self, start: datetime, end: datetime) -> list[UUID]:
        """Query all entity IDs with events in [start, end]."""
        left = bisect.bisect_left(self._timestamps, start)
        right = bisect.bisect_right(self._timestamps, end)
        return [self._entries[i].entity_id for i in range(left, right)]

    def query_range_entries(
        self, start: datetime, end: datetime
    ) -> list[TemporalEntry]:
        """Query all entries in [start, end]."""
        left = bisect.bisect_left(self._timestamps, start)
        right = bisect.bisect_right(self._timestamps, end)
        return self._entries[left:right]

    def entity_timeline(
        self,
        entity_id: UUID,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> list[TemporalEntry]:
        """Get the timeline for a specific entity, optionally within a time range."""
        entries = self._entity_index.get(entity_id, [])
        if start is None and end is None:
            return list(entries)

        result = []
        for entry in entries:
            if start and entry.timestamp < start:
                continue
            if end and entry.timestamp > end:
                break
            result.append(entry)
        return result

    def entity_state_at(self, entity_id: UUID, timestamp: datetime) -> list[TemporalEntry]:
        """Get all events for an entity up to a point in time.

        This is the primary query for reconstructing entity state.
        Target: <5ms hot on 10M deltas.
        """
        entries = self._entity_index.get(entity_id, [])
        if not entries:
            return []

        # Binary search for the rightmost entry <= timestamp
        timestamps = [e.timestamp for e in entries]
        idx = bisect.bisect_right(timestamps, timestamp)
        return entries[:idx]

    def global_timeline(
        self, start: datetime, end: datetime, limit: int = 1000
    ) -> list[TemporalEntry]:
        """Query the global timeline across all entities."""
        left = bisect.bisect_left(self._timestamps, start)
        right = bisect.bisect_right(self._timestamps, end)
        return self._entries[left : min(right, left + limit)]

    def latest_for_entity(self, entity_id: UUID) -> Optional[TemporalEntry]:
        """Get the most recent entry for an entity."""
        entries = self._entity_index.get(entity_id, [])
        return entries[-1] if entries else None

    @property
    def size(self) -> int:
        """Total number of entries in the index."""
        return len(self._entries)

    @property
    def entity_count(self) -> int:
        """Number of distinct entities indexed."""
        return len(self._entity_index)

    def clear(self) -> None:
        """Clear all entries from the index."""
        self._entries.clear()
        self._entity_index.clear()
        self._timestamps.clear()
