"""DeltaMaterializer: syncs GraphDeltas across all three graph tiers.

Two propagation paths:
  - Sync path  -> ReasoningGraph (in-process, <2ms p99 target)
  - Async path -> QueryGraph / Neo4j (background, 500ms staleness bound)

On startup the materializer catches up each tier from its persisted cursor
to the latest sequence number in the DeltaLogStore.

If the QueryGraph (Neo4j) is unreachable, deltas are buffered in-memory
and retried with exponential back-off until the staleness bound is restored.

v3.3 A1: Every delta is schema-version-validated before application.
"""

from __future__ import annotations

import asyncio
import collections
import time
from typing import Any, Optional

import structlog

from src.core.fact import GraphDelta, validate_schema_version
from src.state_graph.delta_log import DeltaLogStore
from src.state_graph.reasoning_graph import ReasoningGraph

logger = structlog.get_logger(__name__)

# Type alias — the QueryGraph class lives in query_graph.py.
# Accept Any so the module remains importable even when Neo4j deps are absent.
QueryGraphMaterializer = Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_QG_STALENESS_BOUND_S: float = 0.5  # 500ms max staleness for QueryGraph
_QG_INITIAL_RETRY_S: float = 0.05   # 50ms initial back-off on Neo4j failure
_QG_MAX_RETRY_S: float = 5.0        # ceiling for exponential back-off
_QG_RETRY_FACTOR: float = 2.0       # multiplier per retry attempt
_CATCH_UP_BATCH: int = 500          # max deltas fetched per catch-up round


class MaterializationError(Exception):
    """Raised when a delta cannot be applied to a tier."""


class DeltaMaterializer:
    """Orchestrates delta application across DeltaLog, ReasoningGraph, and QueryGraph.

    Args:
        delta_log: Append-only delta log (source of truth).
        reasoning_graph: In-process Rust-backed reasoning graph.
        query_graph: Neo4j-backed query graph materializer.
    """

    def __init__(
        self,
        delta_log: DeltaLogStore,
        reasoning_graph: ReasoningGraph,
        query_graph: QueryGraphMaterializer,
    ) -> None:
        self._delta_log = delta_log
        self._reasoning_graph = reasoning_graph
        self._query_graph = query_graph

        # Per-tier cursors: last successfully applied sequence number.
        self._rg_cursor: int = 0
        self._qg_cursor: int = 0

        # Buffer for deltas that failed to apply to QueryGraph (Neo4j down).
        self._qg_pending: collections.deque[GraphDelta] = collections.deque()

        # Background retry state.
        self._qg_retry_task: Optional[asyncio.Task[None]] = None
        self._qg_retry_delay: float = _QG_INITIAL_RETRY_S
        self._shutting_down: bool = False

        # Lock to serialize QueryGraph writes (avoids out-of-order application).
        self._qg_lock = asyncio.Lock()

    # ── Public properties ─────────────────────────────────────────────────

    @property
    def rg_cursor(self) -> int:
        """Last sequence number applied to ReasoningGraph."""
        return self._rg_cursor

    @property
    def qg_cursor(self) -> int:
        """Last sequence number applied to QueryGraph."""
        return self._qg_cursor

    @property
    def qg_pending_count(self) -> int:
        """Number of deltas buffered for QueryGraph retry."""
        return len(self._qg_pending)

    # ── Core materialisation ──────────────────────────────────────────────

    async def materialize(self, delta: GraphDelta) -> None:
        """Apply *delta* to all tiers: sync to ReasoningGraph, async to QueryGraph.

        Schema version is validated once up front (v3.3 A1).
        The sync path blocks the caller; the async path is fire-and-forget
        but respects the 500ms staleness bound.
        """
        validate_schema_version(delta)

        # Fast path — must complete in <2ms p99.
        await self.materialize_sync(delta)

        # Background path — enqueue for QueryGraph.
        # We await the first attempt so that under normal conditions the
        # caller observes success, but failures are retried in background.
        await self.materialize_async(delta)

    async def materialize_sync(self, delta: GraphDelta) -> None:
        """Apply *delta* to ReasoningGraph only (fast, in-process path).

        Target latency: <2ms p99.
        """
        validate_schema_version(delta)
        t0 = time.perf_counter_ns()
        try:
            self._reasoning_graph.apply_delta(delta)
        except NotImplementedError:
            # ReasoningGraph stub — tolerate during early Phase 1.
            logger.debug("reasoning_graph.apply_delta not yet implemented, skipping")
        except Exception:
            logger.exception(
                "rg_apply_failed",
                delta_id=str(delta.delta_id),
                seq=delta.sequence_number,
            )
            raise MaterializationError(
                f"Failed to apply delta {delta.delta_id} to ReasoningGraph"
            )
        finally:
            elapsed_us = (time.perf_counter_ns() - t0) / 1_000
            logger.debug(
                "rg_apply",
                delta_id=str(delta.delta_id),
                seq=delta.sequence_number,
                elapsed_us=round(elapsed_us, 1),
            )

        # Advance cursor only on success.
        if delta.sequence_number > self._rg_cursor:
            self._rg_cursor = delta.sequence_number

    async def materialize_async(self, delta: GraphDelta) -> None:
        """Apply *delta* to QueryGraph (Neo4j).

        On failure the delta is buffered and a background retry loop is
        started automatically.  The retry loop drains the buffer in order,
        applying exponential back-off up to *_QG_MAX_RETRY_S*.
        """
        validate_schema_version(delta)

        async with self._qg_lock:
            # If there is already a backlog, append and let the retry loop
            # handle it so ordering is preserved.
            if self._qg_pending:
                self._qg_pending.append(delta)
                self._ensure_retry_loop()
                return

            # Attempt direct application.
            if await self._try_apply_qg(delta):
                return

            # First failure — buffer and start retry loop.
            self._qg_pending.append(delta)
            self._ensure_retry_loop()

    # ── Catch-up on startup ───────────────────────────────────────────────

    async def catch_up(self) -> int:
        """Replay un-applied deltas from the DeltaLogStore to both tiers.

        Reads from each tier's cursor up to the latest sequence number.
        Returns the total number of deltas replayed (sum of both tiers).
        """
        latest = await self._delta_log.get_latest_sequence()
        if latest == 0:
            logger.info("catch_up_noop", reason="empty_delta_log")
            return 0

        total_replayed = 0

        # -- ReasoningGraph catch-up (sync, sequential) --
        rg_replayed = await self._catch_up_tier(
            tier_name="reasoning_graph",
            cursor=self._rg_cursor,
            latest=latest,
            apply_fn=self.materialize_sync,
        )
        total_replayed += rg_replayed

        # -- QueryGraph catch-up (async, sequential) --
        qg_replayed = await self._catch_up_tier(
            tier_name="query_graph",
            cursor=self._qg_cursor,
            latest=latest,
            apply_fn=self.materialize_async,
        )
        total_replayed += qg_replayed

        logger.info(
            "catch_up_complete",
            rg_replayed=rg_replayed,
            qg_replayed=qg_replayed,
            rg_cursor=self._rg_cursor,
            qg_cursor=self._qg_cursor,
        )
        return total_replayed

    # ── Shutdown ──────────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Gracefully stop background retry tasks and drain pending buffer."""
        self._shutting_down = True
        if self._qg_retry_task is not None and not self._qg_retry_task.done():
            self._qg_retry_task.cancel()
            try:
                await self._qg_retry_task
            except asyncio.CancelledError:
                pass
        pending = len(self._qg_pending)
        if pending:
            logger.warning("shutdown_with_pending_deltas", pending=pending)

    # ── Internal helpers ──────────────────────────────────────────────────

    async def _try_apply_qg(self, delta: GraphDelta) -> bool:
        """Attempt to apply *delta* to QueryGraph.  Returns True on success."""
        try:
            await self._query_graph.apply_delta(delta)
        except NotImplementedError:
            # QueryGraph stub — tolerate during early Phase 1.
            logger.debug("query_graph.apply_delta not yet implemented, skipping")
        except Exception:
            logger.warning(
                "qg_apply_failed",
                delta_id=str(delta.delta_id),
                seq=delta.sequence_number,
            )
            return False
        else:
            if delta.sequence_number > self._qg_cursor:
                self._qg_cursor = delta.sequence_number
            logger.debug(
                "qg_apply",
                delta_id=str(delta.delta_id),
                seq=delta.sequence_number,
            )
        # Treat NotImplementedError as success (cursor advances).
        if delta.sequence_number > self._qg_cursor:
            self._qg_cursor = delta.sequence_number
        return True

    def _ensure_retry_loop(self) -> None:
        """Start the background retry loop if it is not already running."""
        if self._qg_retry_task is None or self._qg_retry_task.done():
            self._qg_retry_task = asyncio.create_task(self._qg_retry_loop())

    async def _qg_retry_loop(self) -> None:
        """Drain the pending buffer with exponential back-off."""
        logger.info("qg_retry_loop_started", pending=len(self._qg_pending))
        while self._qg_pending and not self._shutting_down:
            delta = self._qg_pending[0]
            async with self._qg_lock:
                success = await self._try_apply_qg(delta)
            if success:
                self._qg_pending.popleft()
                self._qg_retry_delay = _QG_INITIAL_RETRY_S  # reset on success
                logger.debug(
                    "qg_retry_success",
                    delta_id=str(delta.delta_id),
                    remaining=len(self._qg_pending),
                )
            else:
                logger.warning(
                    "qg_retry_backoff",
                    delay_s=self._qg_retry_delay,
                    pending=len(self._qg_pending),
                )
                await asyncio.sleep(self._qg_retry_delay)
                self._qg_retry_delay = min(
                    self._qg_retry_delay * _QG_RETRY_FACTOR,
                    _QG_MAX_RETRY_S,
                )
        logger.info(
            "qg_retry_loop_finished",
            remaining=len(self._qg_pending),
            shutting_down=self._shutting_down,
        )

    async def _catch_up_tier(
        self,
        tier_name: str,
        cursor: int,
        latest: int,
        apply_fn: Any,
    ) -> int:
        """Replay deltas for a single tier in batches.  Returns count replayed."""
        if cursor >= latest:
            return 0

        replayed = 0
        current = cursor
        while current < latest:
            batch_end = min(current + _CATCH_UP_BATCH, latest)
            deltas = await self._delta_log.get_range(current + 1, batch_end)
            for delta in deltas:
                try:
                    await apply_fn(delta)
                    replayed += 1
                except MaterializationError:
                    logger.error(
                        "catch_up_apply_error",
                        tier=tier_name,
                        delta_id=str(delta.delta_id),
                        seq=delta.sequence_number,
                    )
                    # Stop catch-up for this tier on hard failure.
                    return replayed
            current = batch_end
            logger.debug(
                "catch_up_batch",
                tier=tier_name,
                batch_end=batch_end,
                replayed=replayed,
            )
        return replayed
