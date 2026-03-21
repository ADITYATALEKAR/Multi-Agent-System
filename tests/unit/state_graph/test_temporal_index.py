"""Unit tests for TemporalIndex."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from src.state_graph.temporal_index import TemporalEntry, TemporalIndex


class TestTemporalEntry:
    def test_ordering(self) -> None:
        t1 = TemporalEntry(timestamp=datetime(2025, 1, 1), entity_id=uuid4())
        t2 = TemporalEntry(timestamp=datetime(2025, 1, 2), entity_id=uuid4())
        assert t1 < t2

    def test_same_timestamp(self) -> None:
        ts = datetime(2025, 1, 1)
        e1 = TemporalEntry(timestamp=ts, entity_id=uuid4())
        e2 = TemporalEntry(timestamp=ts, entity_id=uuid4())
        assert e1 == e2  # compare=False on entity_id

    def test_frozen(self) -> None:
        entry = TemporalEntry(timestamp=datetime.now(), entity_id=uuid4())
        with pytest.raises(AttributeError):
            entry.timestamp = datetime.now()  # type: ignore[misc]


class TestTemporalIndex:
    def setup_method(self) -> None:
        self.idx = TemporalIndex()

    def test_insert_and_query(self) -> None:
        eid = uuid4()
        ts = datetime(2025, 1, 15)
        self.idx.insert(timestamp=ts, entity_id=eid, sequence_number=1)
        results = self.idx.query_range(
            start=datetime(2025, 1, 1),
            end=datetime(2025, 2, 1),
        )
        assert len(results) == 1
        assert results[0] == eid

    def test_query_range_empty(self) -> None:
        eid = uuid4()
        self.idx.insert(
            timestamp=datetime(2025, 6, 1), entity_id=eid, sequence_number=1,
        )
        results = self.idx.query_range(
            start=datetime(2025, 1, 1),
            end=datetime(2025, 2, 1),
        )
        assert len(results) == 0

    def test_multiple_entries_ordered(self) -> None:
        base = datetime(2025, 1, 1)
        ids = []
        for i in range(10):
            eid = uuid4()
            ids.append(eid)
            self.idx.insert(
                timestamp=base + timedelta(days=i),
                entity_id=eid,
                sequence_number=i,
            )
        # Query middle range (bisect_right includes end boundary)
        results = self.idx.query_range(
            start=base + timedelta(days=3),
            end=base + timedelta(days=7),
        )
        assert len(results) == 5  # days 3,4,5,6,7

    def test_entity_timeline(self) -> None:
        eid = uuid4()
        base = datetime(2025, 1, 1)
        for i in range(5):
            self.idx.insert(
                timestamp=base + timedelta(hours=i),
                entity_id=eid,
                sequence_number=i,
            )
        timeline = self.idx.entity_timeline(eid)
        assert len(timeline) == 5

    def test_latest_for_entity(self) -> None:
        eid = uuid4()
        base = datetime(2025, 1, 1)
        for i in range(3):
            self.idx.insert(
                timestamp=base + timedelta(days=i),
                entity_id=eid,
                sequence_number=i,
            )
        latest = self.idx.latest_for_entity(eid)
        assert latest is not None
        assert latest.sequence_number == 2

    def test_latest_for_nonexistent(self) -> None:
        latest = self.idx.latest_for_entity(uuid4())
        assert latest is None

    def test_global_timeline(self) -> None:
        base = datetime(2025, 1, 1)
        for i in range(5):
            self.idx.insert(
                timestamp=base + timedelta(days=i),
                entity_id=uuid4(),
                sequence_number=i,
            )
        timeline = self.idx.global_timeline(
            start=base,
            end=base + timedelta(days=10),
            limit=3,
        )
        assert len(timeline) == 3
