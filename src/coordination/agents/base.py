"""Abstract base class for all coordination agents.

Implements:
- BaseAgent ABC with BDI-style accept_task, capabilities, heartbeat
- Agent lifecycle: idle → working → done
- v3.3 D3: heartbeat with 60s timeout
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, Field

from src.core.coordination import AgentBid, ResourceBudget, WorkItem

if TYPE_CHECKING:
    from uuid import UUID

logger = structlog.get_logger()

HEARTBEAT_TIMEOUT_S = 60  # v3.3 D3


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize naive and aware datetimes to UTC for comparisons."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AgentStatus(BaseModel):
    """Current status of an agent."""

    agent_id: str
    state: str = "idle"  # idle, working, stopped
    current_work_item: UUID | None = None
    last_heartbeat: datetime = Field(default_factory=utc_now)
    tasks_completed: int = 0
    tasks_failed: int = 0


AgentStatus.model_rebuild(_types_namespace={"UUID": __import__("uuid").UUID})


class BaseAgent(ABC):
    """Abstract base agent that all coordination agents must extend.

    Provides:
    - Capability-based work item matching
    - BDI-style bid/execute lifecycle
    - Heartbeat for liveness detection (v3.3 D3)
    - Status tracking
    """

    AGENT_ID: str = "base"
    CAPABILITIES: set[str] = set()

    def __init__(self, agent_id: str | None = None) -> None:
        self._agent_id = agent_id or self.AGENT_ID
        self._status = AgentStatus(agent_id=self._agent_id)
        self._stopped = False

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def get_capabilities(self) -> set[str]:
        return set(self.CAPABILITIES)

    def get_status(self) -> str:
        return self._status.state

    def heartbeat(self) -> None:
        """Signal that the agent is alive (v3.3 D3)."""
        self._status.last_heartbeat = utc_now()

    def is_alive(self) -> bool:
        """Check if agent's heartbeat is within timeout."""
        elapsed = (utc_now() - _as_utc(self._status.last_heartbeat)).total_seconds()
        return elapsed < HEARTBEAT_TIMEOUT_S and not self._stopped

    def can_handle(self, item: WorkItem) -> bool:
        """Check if this agent has the required capabilities for a work item."""
        if not item.required_capabilities:
            return True
        return bool(self.CAPABILITIES & item.required_capabilities)

    def bid(self, item: WorkItem) -> AgentBid | None:
        """Generate a bid for a work item, or None to abstain."""
        if not self.can_handle(item):
            return None
        if self._status.state == "working":
            return None  # Already busy
        if self._stopped:
            return None

        return AgentBid(
            agent_id=self._agent_id,
            work_item_id=item.item_id,
            capability_match=self._compute_capability_match(item),
            estimated_time=self._estimate_time(item),
            current_load=0.0 if self._status.state == "idle" else 0.8,
            agent_reliability=self._compute_reliability(),
            utility_score=0.0,  # Set by BidEvaluator
        )

    def accept_task(self, item: WorkItem, budget: ResourceBudget | None = None) -> Any:
        """Accept and execute a work item (BDI-style)."""
        self._status.state = "working"
        self._status.current_work_item = item.item_id
        self.heartbeat()

        try:
            result = self.execute(item)
            self._status.tasks_completed += 1
            return result
        except Exception:
            self._status.tasks_failed += 1
            raise
        finally:
            self._status.state = "idle"
            self._status.current_work_item = None

    def abort(self) -> None:
        """Abort current work and return to idle."""
        self._status.state = "idle"
        self._status.current_work_item = None

    def stop(self) -> None:
        """Stop the agent."""
        self._stopped = True
        self._status.state = "stopped"

    @abstractmethod
    def execute(self, item: WorkItem) -> Any:
        """Execute the assigned work item. Must be implemented by subclasses."""
        ...

    def _compute_capability_match(self, item: WorkItem) -> float:
        if not item.required_capabilities:
            return 1.0
        matched = len(self.CAPABILITIES & item.required_capabilities)
        return matched / len(item.required_capabilities)

    def _estimate_time(self, item: WorkItem) -> float:
        return 1.0  # Default 1s, overridden by subclasses

    def _compute_reliability(self) -> float:
        total = self._status.tasks_completed + self._status.tasks_failed
        if total == 0:
            return 1.0
        return self._status.tasks_completed / total
