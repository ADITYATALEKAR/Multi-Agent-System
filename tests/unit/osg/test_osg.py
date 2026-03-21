"""Unit tests for the OSG module: materializer, failure propagation, temporal order."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from src.core.runtime_event import EventStatus, EventType, RuntimeEvent
from src.osg.failure_propagation import FailurePropagationInferrer, PropagationChain
from src.osg.materializer import OSGMaterializer
from src.osg.temporal_order import TemporalOrderer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    source: UUID,
    target: UUID | None = None,
    event_type: EventType = EventType.SERVICE_CALL,
    status: EventStatus = EventStatus.SUCCESS,
    timestamp: datetime | None = None,
    trace_id: str | None = None,
    causal_predecessors: set[UUID] | None = None,
    anomaly_score: float = 0.0,
    event_id: UUID | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=event_id or uuid4(),
        event_type=event_type,
        source_service=source,
        target_service=target,
        status=status,
        timestamp=timestamp or datetime.utcnow(),
        trace_id=trace_id,
        causal_predecessors=causal_predecessors or set(),
        anomaly_score=anomaly_score,
    )


# ===================================================================
# OSGMaterializer tests
# ===================================================================


class TestOSGMaterializer:
    """Tests for OSGMaterializer."""

    def test_process_event_creates_source_node(self) -> None:
        osg = OSGMaterializer()
        svc_a = uuid4()
        event = _make_event(source=svc_a)
        osg.process_event(event)

        assert osg.node_count == 1
        node = osg.get_node(svc_a)
        assert node is not None
        assert node.event_count == 1

    def test_process_event_creates_edge_when_target_present(self) -> None:
        osg = OSGMaterializer()
        svc_a, svc_b = uuid4(), uuid4()
        event = _make_event(source=svc_a, target=svc_b)
        osg.process_event(event)

        assert osg.node_count == 2
        assert osg.edge_count == 1
        edge = osg.get_edge(svc_a, svc_b)
        assert edge is not None
        assert edge.call_count == 1

    def test_event_count_tracks_ingested_events(self) -> None:
        osg = OSGMaterializer()
        svc = uuid4()
        for _ in range(5):
            osg.process_event(_make_event(source=svc))
        assert osg.event_count == 5

    def test_node_count_and_edge_count(self) -> None:
        osg = OSGMaterializer()
        a, b, c = uuid4(), uuid4(), uuid4()
        osg.process_event(_make_event(source=a, target=b))
        osg.process_event(_make_event(source=b, target=c))
        osg.process_event(_make_event(source=a, target=c))

        assert osg.node_count == 3
        assert osg.edge_count == 3

    def test_pin_event_and_is_pinned(self) -> None:
        osg = OSGMaterializer()
        eid = uuid4()
        assert not osg.is_pinned(eid)
        osg.pin_event(eid)
        assert osg.is_pinned(eid)

    def test_unpin_event(self) -> None:
        osg = OSGMaterializer()
        eid = uuid4()
        osg.pin_event(eid)
        assert osg.is_pinned(eid)
        osg.unpin_event(eid)
        assert not osg.is_pinned(eid)

    def test_evict_window_respects_pinned_events(self) -> None:
        """v3.3 B4: pinned events must survive eviction even when outside the window."""
        window = timedelta(minutes=30)
        osg = OSGMaterializer(window_duration=window)

        now = datetime(2025, 6, 1, 12, 0, 0)
        old_ts = now - timedelta(hours=2)  # well outside window

        svc = uuid4()
        # Create 3 old events
        old_events = []
        for _ in range(3):
            e = _make_event(source=svc, timestamp=old_ts)
            osg.process_event(e)
            old_events.append(e)

        # Pin the middle event
        pinned_id = old_events[1].event_id
        osg.pin_event(pinned_id)

        assert osg.event_count == 3

        evicted = osg.evict_window(now=now)
        # 2 old unpinned events should be evicted; 1 pinned event survives
        assert evicted == 2
        assert osg.event_count == 1
        remaining = osg.get_events()
        assert len(remaining) == 1
        assert remaining[0].event_id == pinned_id

    def test_get_failure_events_filters_by_status(self) -> None:
        osg = OSGMaterializer()
        svc = uuid4()
        now = datetime(2025, 6, 1, 12, 0, 0)

        ok_event = _make_event(source=svc, status=EventStatus.SUCCESS, timestamp=now)
        fail_event = _make_event(source=svc, status=EventStatus.FAILURE, timestamp=now)
        timeout_event = _make_event(source=svc, status=EventStatus.TIMEOUT, timestamp=now)

        for e in [ok_event, fail_event, timeout_event]:
            osg.process_event(e)

        failures = osg.get_failure_events()
        assert len(failures) == 2
        failure_ids = {e.event_id for e in failures}
        assert fail_event.event_id in failure_ids
        assert timeout_event.event_id in failure_ids
        assert ok_event.event_id not in failure_ids

    def test_get_events_in_window(self) -> None:
        osg = OSGMaterializer()
        svc = uuid4()
        t1 = datetime(2025, 6, 1, 10, 0, 0)
        t2 = datetime(2025, 6, 1, 11, 0, 0)
        t3 = datetime(2025, 6, 1, 12, 0, 0)

        e1 = _make_event(source=svc, timestamp=t1)
        e2 = _make_event(source=svc, timestamp=t2)
        e3 = _make_event(source=svc, timestamp=t3)
        for e in [e1, e2, e3]:
            osg.process_event(e)

        window = osg.get_events_in_window(
            start=datetime(2025, 6, 1, 10, 30, 0),
            end=datetime(2025, 6, 1, 11, 30, 0),
        )
        assert len(window) == 1
        assert window[0].event_id == e2.event_id

    def test_snapshot_returns_dict_with_nodes_and_edges(self) -> None:
        osg = OSGMaterializer()
        a, b = uuid4(), uuid4()
        osg.process_event(_make_event(source=a, target=b))

        snap = osg.snapshot()
        assert isinstance(snap, dict)
        assert "nodes" in snap
        assert "edges" in snap
        assert len(snap["nodes"]) == 2
        assert len(snap["edges"]) == 1

    def test_process_event_auto_pins_causal_predecessors(self) -> None:
        """v3.3 B4: causal predecessors are auto-pinned on ingestion."""
        osg = OSGMaterializer()
        svc_a, svc_b = uuid4(), uuid4()
        e1 = _make_event(source=svc_a)
        osg.process_event(e1)

        e2 = _make_event(source=svc_b, causal_predecessors={e1.event_id})
        osg.process_event(e2)

        assert osg.is_pinned(e1.event_id)


# ===================================================================
# FailurePropagationInferrer tests
# ===================================================================


class TestFailurePropagationInferrer:
    """Tests for FailurePropagationInferrer."""

    def test_infer_propagation_returns_propagation_chain(self) -> None:
        osg = OSGMaterializer()
        svc_a, svc_b = uuid4(), uuid4()
        now = datetime(2025, 6, 1, 12, 0, 0)

        root = _make_event(
            source=svc_a, target=svc_b, status=EventStatus.FAILURE,
            timestamp=now, anomaly_score=0.5,
        )
        osg.process_event(root)

        child = _make_event(
            source=svc_b, status=EventStatus.FAILURE,
            timestamp=now + timedelta(seconds=1),
            causal_predecessors={root.event_id},
            anomaly_score=0.6,
        )
        osg.process_event(child)

        inferrer = FailurePropagationInferrer(osg)
        chain = inferrer.infer_propagation(root.event_id)

        assert isinstance(chain, PropagationChain)
        assert chain.root_event_id == root.event_id
        assert len(chain.chain_events) >= 2
        assert svc_a in chain.affected_services
        assert svc_b in chain.affected_services
        assert chain.max_depth >= 1
        assert chain.confidence > 0.0

    def test_infer_failure_propagation_in_time_window(self) -> None:
        osg = OSGMaterializer()
        svc_a, svc_b, svc_c = uuid4(), uuid4(), uuid4()
        t0 = datetime(2025, 6, 1, 12, 0, 0)

        root = _make_event(
            source=svc_a, target=svc_b, status=EventStatus.FAILURE,
            timestamp=t0, anomaly_score=0.5,
        )
        osg.process_event(root)

        downstream = _make_event(
            source=svc_b, target=svc_c, status=EventStatus.FAILURE,
            timestamp=t0 + timedelta(seconds=2),
            causal_predecessors={root.event_id},
            anomaly_score=0.6,
        )
        osg.process_event(downstream)

        inferrer = FailurePropagationInferrer(osg)
        prop_events = inferrer.infer_failure_propagation(
            window_start=t0 - timedelta(seconds=1),
            window_end=t0 + timedelta(seconds=5),
        )
        assert isinstance(prop_events, list)
        # At least one propagation event synthesized for downstream services
        assert len(prop_events) >= 1
        for pe in prop_events:
            assert pe.event_type == EventType.FAILURE_PROPAGATION

    def test_infer_propagation_nonexistent_root_returns_empty(self) -> None:
        osg = OSGMaterializer()
        svc = uuid4()
        osg.process_event(_make_event(source=svc))

        inferrer = FailurePropagationInferrer(osg)
        chain = inferrer.infer_propagation(uuid4())
        assert chain.chain_events == []
        assert chain.max_depth == 0


# ===================================================================
# TemporalOrderer tests
# ===================================================================


class TestTemporalOrderer:
    """Tests for TemporalOrderer."""

    def test_order_respects_causal_predecessors(self) -> None:
        orderer = TemporalOrderer()
        svc = uuid4()
        # e1 happens AFTER e2 in wall-clock time, but e2 causally depends on e1.
        e1 = _make_event(source=svc, timestamp=datetime(2025, 6, 1, 12, 0, 5))
        e2 = _make_event(
            source=svc,
            timestamp=datetime(2025, 6, 1, 12, 0, 0),
            causal_predecessors={e1.event_id},
        )

        # e1 must come before e2 because e2 depends on e1
        ordered = orderer.order([e2, e1])
        ids = [e.event_id for e in ordered]
        assert ids.index(e1.event_id) < ids.index(e2.event_id)

    def test_compute_logical_clocks(self) -> None:
        orderer = TemporalOrderer()
        svc = uuid4()
        t0 = datetime(2025, 6, 1, 12, 0, 0)

        e1 = _make_event(source=svc, timestamp=t0)
        e2 = _make_event(
            source=svc,
            timestamp=t0 + timedelta(seconds=1),
            causal_predecessors={e1.event_id},
        )
        e3 = _make_event(
            source=svc,
            timestamp=t0 + timedelta(seconds=2),
            causal_predecessors={e2.event_id},
        )

        clocks = orderer.compute_logical_clocks([e3, e1, e2])
        # e1 has no predecessors -> clock 1
        # e2 depends on e1 -> clock 2
        # e3 depends on e2 -> clock 3
        assert clocks[e1.event_id] == 1
        assert clocks[e2.event_id] == 2
        assert clocks[e3.event_id] == 3

    def test_order_single_event_returns_copy(self) -> None:
        orderer = TemporalOrderer()
        e = _make_event(source=uuid4())
        ordered = orderer.order([e])
        assert len(ordered) == 1
        assert ordered[0].event_id == e.event_id

    def test_order_by_timestamp_when_no_causal_link(self) -> None:
        orderer = TemporalOrderer()
        svc = uuid4()
        e1 = _make_event(source=svc, timestamp=datetime(2025, 6, 1, 12, 0, 0))
        e2 = _make_event(source=svc, timestamp=datetime(2025, 6, 1, 11, 0, 0))

        ordered = orderer.order([e1, e2])
        assert ordered[0].event_id == e2.event_id  # earlier timestamp first
        assert ordered[1].event_id == e1.event_id
