"""Top-level orchestrator driving the multi-agent coordination cycle.

Implements:
- Full coordination loop: post → bid → assign → execute
- Triage mode with hysteresis (v3.3 Fix 6): entry 90%, exit 60%, min 2min dwell
- Heartbeat polling every 15s, 60s timeout (v3.3 D3)
- Escalation timeout 30min → ABANDONED (v3.3 D2)
- Budget management per task
- Fast-path vs bidding routing
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from src.coordination.arbitration import ConflictArbitrator, StalemateBreaker
from src.coordination.bidding import BiddingProtocol
from src.coordination.blackboard import BlackboardManager
from src.coordination.bus import MessageBus
from src.coordination.execution_policy import ExecutionPolicy
from src.coordination.reliability import AgentReliabilityTracker
from src.core.coordination import (
    WorkItem,
    WorkItemStatus,
)

if TYPE_CHECKING:
    from src.coordination.agents.base import BaseAgent

logger = structlog.get_logger()


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize naive and aware datetimes to UTC for comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# v3.3 Fix 6: Triage thresholds
TRIAGE_ENTRY_PCT = 0.90
TRIAGE_EXIT_PCT = 0.60
TRIAGE_MIN_DWELL_S = 120  # 2 minutes minimum before exit

# v3.3 D2: Escalation timeout
ESCALATION_TIMEOUT_S = 1800  # 30 minutes

# v3.3 D3: Heartbeat poll interval
HEARTBEAT_POLL_INTERVAL_S = 15

# Capacity limits
MAX_CONCURRENT_ITEMS = 100


class TriageState(BaseModel):
    """Tracks triage mode state with hysteresis."""

    active: bool = False
    entered_at: datetime | None = None
    last_check: datetime = Field(default_factory=utc_now)


class TaskContext(BaseModel):
    """Context for a submitted task through the pipeline."""

    task_id: UUID = Field(default_factory=uuid4)
    incident_id: UUID = Field(default_factory=uuid4)
    tenant_id: str = "default"
    submitted_at: datetime = Field(default_factory=utc_now)
    work_item_ids: list[UUID] = Field(default_factory=list)
    completed: bool = False


class Orchestrator:
    """Runs the main coordination loop and handles incident processing.

    Responsibilities:
    - Route work items via fast-path or bidding
    - Monitor agent heartbeats (15s poll, 60s timeout)
    - Manage triage mode (90% entry, 60% exit, 2min dwell)
    - Handle escalation timeouts (30min → ABANDONED)
    - Enforce execution floors
    """

    def __init__(
        self,
        blackboard: BlackboardManager | None = None,
        bidding: BiddingProtocol | None = None,
        reliability: AgentReliabilityTracker | None = None,
        arbitrator: ConflictArbitrator | None = None,
        stalemate_breaker: StalemateBreaker | None = None,
        execution_policy: ExecutionPolicy | None = None,
        bus: MessageBus | None = None,
    ) -> None:
        self._blackboard = blackboard or BlackboardManager()
        self._bidding = bidding or BiddingProtocol()
        self._reliability = reliability or AgentReliabilityTracker()
        self._arbitrator = arbitrator or ConflictArbitrator()
        self._stalemate_breaker = stalemate_breaker or StalemateBreaker()
        self._execution_policy = execution_policy or ExecutionPolicy()
        self._bus = bus or MessageBus()

        self._agents: dict[str, BaseAgent] = {}
        self._tasks: dict[UUID, TaskContext] = {}
        self._triage = TriageState()
        self._last_heartbeat_check: datetime = utc_now()
        self._cycle_count: int = 0

    # ── Agent registration ──

    def register_agent(self, agent: BaseAgent) -> None:
        """Register a specialist agent."""
        self._agents[agent.agent_id] = agent
        logger.debug("agent_registered", agent_id=agent.agent_id)

    def unregister_agent(self, agent_id: str) -> None:
        """Unregister an agent."""
        self._agents.pop(agent_id, None)

    def get_agent(self, agent_id: str) -> BaseAgent | None:
        return self._agents.get(agent_id)

    # ── Task submission ──

    def submit_task(self, work_items: list[WorkItem], tenant_id: str = "default") -> UUID:
        """Submit a set of work items as a task.

        Returns:
            Task ID for tracking.
        """
        incident_id = work_items[0].incident_id if work_items else uuid4()
        ctx = TaskContext(
            incident_id=incident_id,
            tenant_id=tenant_id,
        )

        for item in work_items:
            item_id = self._blackboard.post_work_item(item)
            ctx.work_item_ids.append(item_id)

        self._tasks[ctx.task_id] = ctx
        logger.info("task_submitted", task_id=str(ctx.task_id), items=len(work_items))
        return ctx.task_id

    # ── Main coordination cycle ──

    def run_cycle(self) -> int:
        """Execute one full coordination cycle.

        Steps:
        1. Cleanup stale items
        2. Check heartbeats
        3. Check escalation timeouts
        4. Check triage mode
        5. Route open items (fast-path or bidding)
        6. Assign winners

        Returns:
            Number of items processed.
        """
        self._cycle_count += 1
        processed = 0

        # 1. Stale cleanup
        if self._blackboard.should_cleanup():
            cleaned = self._blackboard.cleanup_stale()
            if cleaned > 0:
                logger.info("cycle_cleanup", cleaned=cleaned)

        # 2. Heartbeat check
        self._check_heartbeats()

        # 3. Escalation timeout check
        self._check_escalation_timeouts()

        # 4. Triage mode evaluation
        self._evaluate_triage()

        # 5. Route open items
        open_items = self._blackboard.get_open_items()

        # In triage mode, only process high-priority items
        if self._triage.active:
            open_items = [i for i in open_items if i.priority >= 0.5]

        for item in open_items:
            # Try fast-path first
            fast_agent = self._bidding.should_fast_path(item)
            if fast_agent and fast_agent in self._agents:
                agent = self._agents[fast_agent]
                if agent.can_handle(item) and agent.get_status() == "idle":
                    self._assign_item(item, fast_agent)
                    processed += 1
                    continue

            # Fall back to bidding
            processed += self._run_bidding(item)

        logger.debug(
            "cycle_complete",
            cycle=self._cycle_count,
            processed=processed,
            triage=self._triage.active,
        )
        return processed

    def _run_bidding(self, item: WorkItem) -> int:
        """Run bidding protocol for a single item.

        Returns:
            1 if assigned, 0 otherwise.
        """
        # Update status to BIDDING
        self._blackboard.update_item(item.item_id, {"status": WorkItemStatus.BIDDING})

        # Collect bids from capable agents
        for agent in self._agents.values():
            bid = agent.bid(item)
            if bid:
                # Inject reliability score
                bid = bid.model_copy(
                    update={
                        "agent_reliability": self._reliability.get_reliability(agent.agent_id),
                    }
                )
                if not self._bidding.submit_bid(bid):
                    break  # At slot capacity

        # Evaluate bids
        winner = self._bidding.evaluate_bids(item.item_id)
        if not winner:
            # Revert to OPEN
            self._blackboard.update_item(item.item_id, {"status": WorkItemStatus.OPEN})
            return 0

        # Check for stalemate
        # (handled internally by evaluate_bids via utility scoring)

        self._assign_item(item, winner.agent_id)
        self._bidding.clear_bids(item.item_id)
        return 1

    def _assign_item(self, item: WorkItem, agent_id: str) -> None:
        """Assign a work item to an agent and execute."""
        claimed = self._blackboard.claim_work_item(item.item_id, agent_id)
        if not claimed:
            return

        self._blackboard.update_item(
            item.item_id,
            {
                "status": WorkItemStatus.IN_PROGRESS,
                "last_heartbeat": utc_now(),
            },
        )

        agent = self._agents.get(agent_id)
        if not agent:
            self._blackboard.fail_work_item(item.item_id)
            return

        try:
            result = agent.accept_task(item, item.budget)
            self._blackboard.complete_work_item(item.item_id, result)
            self._reliability.record_success(agent_id)

            self._bus.publish(
                subject="work_item.completed",
                sender="orchestrator",
                payload={"item_id": str(item.item_id), "agent_id": agent_id},
            )
        except Exception as exc:
            logger.error("agent_execution_failed", agent=agent_id, error=str(exc))
            self._blackboard.fail_work_item(item.item_id)
            self._reliability.record_failure(agent_id)

    # ── Heartbeat monitoring (v3.3 D3) ──

    def _check_heartbeats(self) -> None:
        """Check all agent heartbeats. Release items from dead agents."""
        now = utc_now()
        elapsed = (now - self._last_heartbeat_check).total_seconds()
        if elapsed < HEARTBEAT_POLL_INTERVAL_S:
            return

        self._last_heartbeat_check = now

        for agent_id, agent in list(self._agents.items()):
            if not agent.is_alive():
                logger.warning("agent_heartbeat_timeout", agent_id=agent_id)
                self._reliability.record_crash(agent_id)

                # Release any items claimed by this agent
                for item in self._blackboard.get_items_by_status(WorkItemStatus.IN_PROGRESS):
                    if item.claimed_by == agent_id:
                        self._blackboard.update_item(
                            item.item_id,
                            {
                                "status": WorkItemStatus.OPEN,
                                "claimed_by": None,
                            },
                        )

    # ── Escalation timeout (v3.3 D2) ──

    def _check_escalation_timeouts(self) -> None:
        """Check for items stuck in escalation beyond 30 minutes."""
        now = utc_now()
        cutoff = now - timedelta(seconds=ESCALATION_TIMEOUT_S)

        for item in self._blackboard.get_items_by_status(WorkItemStatus.CLAIMED):
            if _as_utc(item.last_heartbeat) < cutoff:
                logger.warning(
                    "escalation_timeout",
                    item_id=str(item.item_id),
                    agent=item.claimed_by,
                )
                self._blackboard.abandon_work_item(item.item_id)

    # ── Triage mode (v3.3 Fix 6) ──

    def _evaluate_triage(self) -> None:
        """Evaluate triage mode entry/exit with hysteresis."""
        capacity = self._get_capacity_pct()
        now = utc_now()

        if not self._triage.active:
            if capacity >= TRIAGE_ENTRY_PCT:
                self._triage.active = True
                self._triage.entered_at = now
                logger.warning("triage_mode_entered", capacity=f"{capacity:.0%}")
                self._bus.publish(
                    subject="triage.entered",
                    sender="orchestrator",
                    payload={"capacity": capacity},
                )
        else:
            # Check minimum dwell time
            if self._triage.entered_at:
                dwell = (now - _as_utc(self._triage.entered_at)).total_seconds()
                if dwell < TRIAGE_MIN_DWELL_S:
                    return  # Too early to exit

            if capacity <= TRIAGE_EXIT_PCT:
                self._triage.active = False
                self._triage.entered_at = None
                logger.info("triage_mode_exited", capacity=f"{capacity:.0%}")
                self._bus.publish(
                    subject="triage.exited",
                    sender="orchestrator",
                    payload={"capacity": capacity},
                )

    def _get_capacity_pct(self) -> float:
        """Calculate current capacity utilization."""
        in_progress = len(self._blackboard.get_items_by_status(WorkItemStatus.IN_PROGRESS))
        claimed = len(self._blackboard.get_items_by_status(WorkItemStatus.CLAIMED))
        active = in_progress + claimed
        if MAX_CONCURRENT_ITEMS == 0:
            return 0.0
        return active / MAX_CONCURRENT_ITEMS

    # ── Termination check ──

    def check_termination(self, task_id: UUID) -> bool:
        """Check if all work items for a task are done."""
        ctx = self._tasks.get(task_id)
        if not ctx:
            return True

        for item_id in ctx.work_item_ids:
            item = self._blackboard.get_work_item(item_id)
            if item and item.status not in (
                WorkItemStatus.COMPLETE,
                WorkItemStatus.FAILED,
                WorkItemStatus.CANCELLED,
                WorkItemStatus.ABANDONED,
            ):
                return False

        ctx.completed = True
        return True

    # ── Triage queries ──

    def is_triage_mode(self) -> bool:
        return self._triage.active

    def enter_triage_mode(self) -> None:
        """Force entry into triage mode."""
        self._triage.active = True
        self._triage.entered_at = utc_now()

    def exit_triage_mode(self) -> None:
        """Force exit from triage mode."""
        self._triage.active = False
        self._triage.entered_at = None

    # ── Stats ──

    @property
    def agent_count(self) -> int:
        return len(self._agents)

    @property
    def cycle_count(self) -> int:
        return self._cycle_count

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    def get_task(self, task_id: UUID) -> TaskContext | None:
        return self._tasks.get(task_id)

    def get_task_items(self, task_id: UUID) -> list[WorkItem]:
        """Return the current work items associated with a task."""
        ctx = self._tasks.get(task_id)
        if ctx is None:
            return []
        items: list[WorkItem] = []
        for item_id in ctx.work_item_ids:
            item = self._blackboard.get_work_item(item_id)
            if item is not None:
                items.append(item)
        return items
