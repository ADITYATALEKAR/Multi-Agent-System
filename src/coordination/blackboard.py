"""Blackboard shared workspace for multi-agent coordination.

Implements:
- WorkItem, Claim, Question stores
- Hard limits: 200 pending claims, 100 pending questions (v3.3 Fix 6)
- Stale item cleanup every 60s
- Claim deduplication
- Capability-filtered open item queries
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

from src.core.coordination import Claim, Question, WorkItem, WorkItemStatus

logger = structlog.get_logger()

# v3.3 Fix 6: Hard limits
MAX_PENDING_CLAIMS = 200
MAX_PENDING_QUESTIONS = 100
STALE_CLEANUP_INTERVAL_S = 60
STALE_WORK_ITEM_AGE_S = 3600  # 1 hour


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BlackboardManager:
    """Central blackboard where agents post, claim, and update work items.

    Enforces:
    - Hard limit on pending claims (200) and questions (100)
    - Stale item cleanup every 60s
    - Claim deduplication (v3.3 Fix 6)
    """

    def __init__(self) -> None:
        self._work_items: dict[UUID, WorkItem] = {}
        self._claims: dict[UUID, Claim] = {}
        self._questions: dict[UUID, Question] = {}
        self._last_cleanup: datetime = utc_now()

    # ── WorkItem operations ──

    def post_work_item(self, item: WorkItem) -> UUID:
        """Post a new work item to the blackboard.

        Returns:
            The work item's UUID.
        """
        self._work_items[item.item_id] = item
        logger.debug("work_item_posted", item_id=str(item.item_id), task_type=item.task_type)
        return item.item_id

    def get_work_item(self, item_id: UUID) -> WorkItem | None:
        """Get a work item by ID."""
        return self._work_items.get(item_id)

    def get_open_items(self, capabilities: set[str] | None = None) -> list[WorkItem]:
        """Return all open (unclaimed) work items, optionally filtered by capabilities.

        Args:
            capabilities: If provided, only return items whose required_capabilities
                         intersect with these capabilities.

        Returns:
            List of open work items sorted by priority descending.
        """
        items = [
            item for item in self._work_items.values()
            if item.status in (WorkItemStatus.OPEN, WorkItemStatus.BIDDING)
        ]

        if capabilities:
            items = [
                item for item in items
                if not item.required_capabilities or (capabilities & item.required_capabilities)
            ]

        return sorted(items, key=lambda i: i.priority, reverse=True)

    def update_item(self, item_id: UUID, updates: dict[str, Any]) -> bool:
        """Update an existing work item.

        Returns:
            True if updated, False if item not found.
        """
        item = self._work_items.get(item_id)
        if not item:
            return False

        updated = item.model_copy(update=updates)
        self._work_items[item_id] = updated
        return True

    def complete_work_item(self, item_id: UUID, result: Any = None) -> None:
        """Mark a work item as complete with an optional result."""
        self.update_item(item_id, {
            "status": WorkItemStatus.COMPLETE,
            "result": result,
        })
        # Clean up claims for this item
        self._claims = {
            cid: c for cid, c in self._claims.items()
            if c.work_item_id != item_id
        }

    def fail_work_item(self, item_id: UUID) -> None:
        """Mark a work item as failed."""
        item = self._work_items.get(item_id)
        if not item:
            return
        if item.attempt_count < item.max_attempts:
            self.update_item(item_id, {
                "status": WorkItemStatus.OPEN,
                "claimed_by": None,
                "attempt_count": item.attempt_count + 1,
            })
        else:
            self.update_item(item_id, {"status": WorkItemStatus.FAILED})

    def abandon_work_item(self, item_id: UUID) -> None:
        """Mark a work item as ABANDONED (v3.3 D2)."""
        self.update_item(item_id, {
            "status": WorkItemStatus.ABANDONED,
            "claimed_by": None,
        })

    # ── Claim operations ──

    def post_claim(self, claim: Claim) -> UUID | None:
        """Post a claim. Returns None if deduped (v3.3 Fix 6).

        Enforces:
        - Deduplication: same agent + same work_item → rejected
        - Hard limit: 200 pending claims
        """
        # Dedup check
        for existing in self._claims.values():
            if existing.agent_id == claim.agent_id and existing.work_item_id == claim.work_item_id:
                logger.debug("claim_deduped", agent=claim.agent_id, item=str(claim.work_item_id))
                return None

        # Hard limit
        if len(self._claims) >= MAX_PENDING_CLAIMS:
            logger.warning("claims_at_limit", count=len(self._claims))
            return None

        self._claims[claim.claim_id] = claim
        return claim.claim_id

    def claim_work_item(self, item_id: UUID, agent_id: str) -> bool:
        """Claim a work item for an agent.

        Returns:
            True if claimed successfully, False if already claimed or not found.
        """
        item = self._work_items.get(item_id)
        if not item:
            return False
        if item.status not in (WorkItemStatus.OPEN, WorkItemStatus.BIDDING):
            return False

        claim = Claim(agent_id=agent_id, work_item_id=item_id)
        claim_id = self.post_claim(claim)
        if claim_id is None:
            return False

        self.update_item(item_id, {
            "status": WorkItemStatus.CLAIMED,
            "claimed_by": agent_id,
            "last_heartbeat": utc_now(),
        })
        return True

    def get_claims_for_item(self, item_id: UUID) -> list[Claim]:
        """Get all claims for a work item."""
        return [c for c in self._claims.values() if c.work_item_id == item_id]

    # ── Question operations ──

    def post_question(self, question: Question) -> UUID | None:
        """Post a question. Returns None if at limit.

        Enforces hard limit: 100 pending questions.
        """
        unresolved = sum(1 for q in self._questions.values() if not q.resolved)
        if unresolved >= MAX_PENDING_QUESTIONS:
            logger.warning("questions_at_limit", count=unresolved)
            return None

        self._questions[question.question_id] = question
        return question.question_id

    def get_question(self, question_id: UUID) -> Question | None:
        return self._questions.get(question_id)

    def get_unresolved_questions(self) -> list[Question]:
        return [q for q in self._questions.values() if not q.resolved]

    def resolve_question(self, question_id: UUID, answer: dict[str, Any]) -> bool:
        q = self._questions.get(question_id)
        if not q:
            return False
        q.answers.append(answer)
        q.resolved = True
        return True

    # ── Cleanup ──

    def cleanup_stale(self) -> int:
        """Remove stale items. Returns count of cleaned items.

        Stale criteria:
        - Work items in CLAIMED/IN_PROGRESS with heartbeat > 60s (agent died)
        - Questions older than 1 hour unresolved
        """
        now = utc_now()
        cleaned = 0

        # Stale work items (heartbeat timeout)
        for item_id, item in list(self._work_items.items()):
            if item.status in (WorkItemStatus.CLAIMED, WorkItemStatus.IN_PROGRESS):
                elapsed = (now - item.last_heartbeat).total_seconds()
                if elapsed > STALE_CLEANUP_INTERVAL_S:
                    self.update_item(item_id, {
                        "status": WorkItemStatus.OPEN,
                        "claimed_by": None,
                    })
                    # Remove associated claims
                    self._claims = {
                        cid: c for cid, c in self._claims.items()
                        if c.work_item_id != item_id
                    }
                    cleaned += 1

        # Stale questions (>1 hour unresolved)
        for qid, q in list(self._questions.items()):
            if not q.resolved:
                # Questions don't have timestamps by default, skip age check
                pass

        # Evict excess claims by age
        if len(self._claims) > MAX_PENDING_CLAIMS:
            sorted_claims = sorted(
                self._claims.items(),
                key=lambda kv: kv[1].claimed_at,
            )
            excess = len(self._claims) - MAX_PENDING_CLAIMS
            for cid, _ in sorted_claims[:excess]:
                del self._claims[cid]
                cleaned += 1

        self._last_cleanup = now
        if cleaned > 0:
            logger.info("blackboard_cleanup", cleaned=cleaned)
        return cleaned

    def should_cleanup(self) -> bool:
        """Check if cleanup is due (every 60s)."""
        elapsed = (utc_now() - self._last_cleanup).total_seconds()
        return elapsed >= STALE_CLEANUP_INTERVAL_S

    # ── Stats ──

    @property
    def work_item_count(self) -> int:
        return len(self._work_items)

    @property
    def pending_claim_count(self) -> int:
        return len(self._claims)

    @property
    def pending_question_count(self) -> int:
        return sum(1 for q in self._questions.values() if not q.resolved)

    def get_items_by_status(self, status: WorkItemStatus) -> list[WorkItem]:
        return [i for i in self._work_items.values() if i.status == status]

    def get_items_by_incident(self, incident_id: UUID) -> list[WorkItem]:
        """Get all work items for an incident (v3.3 A4)."""
        return [i for i in self._work_items.values() if i.incident_id == incident_id]
