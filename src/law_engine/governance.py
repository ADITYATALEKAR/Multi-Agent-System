"""Law governance: 4-state health management (v3.3 Fix 3).

Health states:
    - ``HEALTHY``          — law is operating normally.
    - ``DEGRADED``         — elevated failure rate, still active.
    - ``REVIEW_REQUIRED``  — needs human review before quarantine.
    - ``QUARANTINED``      — suspended, requires human approval to restore.

v3.3 Fix 3: No auto-disable.  Quarantine requires human approval.
Auto-escalation: HEALTHY -> DEGRADED -> REVIEW_REQUIRED.
Manual only: REVIEW_REQUIRED -> QUARANTINED, QUARANTINED -> HEALTHY.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from enum import Enum

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)


# ── Health state enum ────────────────────────────────────────────────────────


class LawHealthState(str, Enum):
    """4-state law health model (v3.3 Fix 3)."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    REVIEW_REQUIRED = "review_required"
    QUARANTINED = "quarantined"


# Legacy string constants for backward compatibility with Phase 2 tests
HEALTH_ACTIVE: str = "active"
HEALTH_QUARANTINED: str = "quarantined"
HEALTH_DEGRADED: str = "degraded"

_DEFAULT_WINDOW_SIZE: int = 20
_DEFAULT_FAILURE_THRESHOLD: float = 0.80
_DEFAULT_DEGRADED_THRESHOLD: float = 0.40
_DEFAULT_REVIEW_THRESHOLD: float = 0.65


# ── Pydantic models ─────────────────────────────────────────────────────────


class QuarantineRecord(BaseModel):
    """Immutable record of a quarantine event."""

    law_id: str
    reason: str
    quarantined_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    approved_by: str = ""


class ReviewRecord(BaseModel):
    """Record of a review request."""

    law_id: str
    reason: str
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    requested_by: str = "system"


class LawHealthEntry(BaseModel):
    """Mutable health bookkeeping for a single law."""

    law_id: str
    state: LawHealthState = LawHealthState.HEALTHY
    quarantine_record: QuarantineRecord | None = None
    review_record: ReviewRecord | None = None
    recent_outcomes: deque[bool] = Field(
        default_factory=lambda: deque(maxlen=_DEFAULT_WINDOW_SIZE)
    )

    model_config = {"arbitrary_types_allowed": True}


# ── Governance class ─────────────────────────────────────────────────────────


class LawGovernance:
    """Manages law health with 4-state model (v3.3 Fix 3).

    State transitions:
        HEALTHY -> DEGRADED       (auto, failure_rate > degraded_threshold)
        DEGRADED -> REVIEW_REQUIRED (auto, failure_rate > review_threshold)
        REVIEW_REQUIRED -> QUARANTINED (manual, approve_quarantine())
        QUARANTINED -> HEALTHY    (manual, restore_from_quarantine())
        DEGRADED -> HEALTHY       (auto, failure_rate drops)
        REVIEW_REQUIRED -> HEALTHY (auto, failure_rate drops)

    No auto-quarantine — quarantine always requires human approval.
    """

    def __init__(
        self,
        *,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        failure_threshold: float = _DEFAULT_FAILURE_THRESHOLD,
        degraded_threshold: float = _DEFAULT_DEGRADED_THRESHOLD,
        review_threshold: float = _DEFAULT_REVIEW_THRESHOLD,
    ) -> None:
        self._entries: dict[str, LawHealthEntry] = {}
        self._window_size = window_size
        self._failure_threshold = failure_threshold
        self._degraded_threshold = degraded_threshold
        self._review_threshold = review_threshold

    # ── Internal helpers ──────────────────────────────────────────────────

    def _ensure_entry(self, law_id: str) -> LawHealthEntry:
        if law_id not in self._entries:
            self._entries[law_id] = LawHealthEntry(
                law_id=law_id,
                recent_outcomes=deque(maxlen=self._window_size),
            )
        return self._entries[law_id]

    def _failure_rate(self, entry: LawHealthEntry) -> float:
        total = len(entry.recent_outcomes)
        if total == 0:
            return 0.0
        return sum(1 for o in entry.recent_outcomes if not o) / total

    # ── Recording ────────────────────────────────────────────────────────

    def record_evaluation(self, law_id: str, success: bool) -> None:
        """Record the outcome of a single law evaluation."""
        entry = self._ensure_entry(law_id)
        entry.recent_outcomes.append(success)
        logger.debug(
            "evaluation_recorded",
            law_id=law_id,
            success=success,
            window_fill=len(entry.recent_outcomes),
        )

    # ── Health check (auto-escalation) ───────────────────────────────────

    def check_health(self, law_id: str) -> str:
        """Evaluate failure rate and auto-escalate health state.

        Auto-transitions:
            HEALTHY -> DEGRADED (failure_rate > degraded_threshold)
            DEGRADED -> REVIEW_REQUIRED (failure_rate > review_threshold)
            DEGRADED -> HEALTHY (failure_rate drops below degraded_threshold)
            REVIEW_REQUIRED -> HEALTHY (failure_rate drops below degraded_threshold)

        Never auto-quarantines — that requires manual approval.

        Returns:
            The (possibly updated) health state string.
        """
        entry = self._ensure_entry(law_id)

        # Quarantined laws stay quarantined until manually restored
        if entry.state == LawHealthState.QUARANTINED:
            return HEALTH_QUARANTINED

        failure_rate = self._failure_rate(entry)

        if failure_rate > self._review_threshold:
            if entry.state != LawHealthState.REVIEW_REQUIRED:
                entry.state = LawHealthState.REVIEW_REQUIRED
                entry.review_record = ReviewRecord(
                    law_id=law_id,
                    reason=(
                        f"Failure rate {failure_rate:.1%} exceeds review threshold "
                        f"{self._review_threshold:.0%}"
                    ),
                )
                logger.warning(
                    "law_review_required",
                    law_id=law_id,
                    failure_rate=round(failure_rate, 4),
                )
            return self._to_legacy_state(entry.state)

        if failure_rate > self._degraded_threshold:
            if entry.state != LawHealthState.DEGRADED:
                entry.state = LawHealthState.DEGRADED
                logger.info(
                    "law_degraded",
                    law_id=law_id,
                    failure_rate=round(failure_rate, 4),
                )
            return self._to_legacy_state(entry.state)

        # Recovery
        if entry.state in (LawHealthState.DEGRADED, LawHealthState.REVIEW_REQUIRED):
            entry.state = LawHealthState.HEALTHY
            entry.review_record = None
            logger.info("law_recovered", law_id=law_id)

        return self._to_legacy_state(entry.state)

    def evaluate_health(self, law_id: str) -> LawHealthState:
        """Return the current 4-state health (calls check_health internally)."""
        self.check_health(law_id)
        return self._ensure_entry(law_id).state

    # ── Manual actions ───────────────────────────────────────────────────

    def request_review(self, law_id: str, reason: str) -> None:
        """Manually request review of a law."""
        entry = self._ensure_entry(law_id)
        if entry.state == LawHealthState.QUARANTINED:
            return
        entry.state = LawHealthState.REVIEW_REQUIRED
        entry.review_record = ReviewRecord(law_id=law_id, reason=reason)
        logger.info("law_review_requested", law_id=law_id, reason=reason)

    def approve_quarantine(self, law_id: str, reviewer: str) -> bool:
        """Approve quarantine of a law (requires REVIEW_REQUIRED state).

        Returns True if quarantine was approved.
        """
        entry = self._ensure_entry(law_id)
        if entry.state != LawHealthState.REVIEW_REQUIRED:
            logger.warning(
                "quarantine_not_in_review",
                law_id=law_id,
                current_state=entry.state.value,
            )
            return False

        entry.state = LawHealthState.QUARANTINED
        entry.quarantine_record = QuarantineRecord(
            law_id=law_id,
            reason=f"Approved by {reviewer}. "
                   + (entry.review_record.reason if entry.review_record else ""),
            approved_by=reviewer,
        )
        entry.review_record = None
        logger.info("law_quarantined", law_id=law_id, reviewer=reviewer)
        return True

    def restore_from_quarantine(self, law_id: str, reviewer: str) -> bool:
        """Restore a quarantined law (requires human approval).

        Returns True if restoration succeeded.
        """
        entry = self._ensure_entry(law_id)
        if entry.state != LawHealthState.QUARANTINED:
            logger.warning(
                "restore_not_quarantined",
                law_id=law_id,
                current_state=entry.state.value,
            )
            return False

        entry.state = LawHealthState.HEALTHY
        entry.quarantine_record = None
        entry.recent_outcomes.clear()
        logger.info("law_restored", law_id=law_id, reviewer=reviewer)
        return True

    # ── Legacy API (backward compat with Phase 2) ────────────────────────

    def quarantine(self, law_id: str, reason: str) -> None:
        """Legacy: directly quarantine (skips review for backward compat)."""
        entry = self._ensure_entry(law_id)
        entry.state = LawHealthState.QUARANTINED
        entry.quarantine_record = QuarantineRecord(law_id=law_id, reason=reason)
        logger.info("law_quarantined", law_id=law_id, reason=reason)

    def restore(self, law_id: str) -> None:
        """Legacy: restore a quarantined law."""
        entry = self._ensure_entry(law_id)
        if entry.state != LawHealthState.QUARANTINED:
            logger.warning(
                "law_restore_not_quarantined",
                law_id=law_id,
                current_state=entry.state.value,
            )
            return
        entry.state = LawHealthState.HEALTHY
        entry.quarantine_record = None
        logger.info("law_restored", law_id=law_id)

    def get_health(self, law_id: str) -> str:
        """Legacy: get health as string. Maps 4-state to legacy 3-state."""
        entry = self._ensure_entry(law_id)
        return self._to_legacy_state(entry.state)

    def get_health_state(self, law_id: str) -> LawHealthState:
        """Get the precise 4-state health."""
        return self._ensure_entry(law_id).state

    def get_quarantined_laws(self) -> list[str]:
        """Return law IDs currently quarantined."""
        return sorted(
            law_id for law_id, entry in self._entries.items()
            if entry.state == LawHealthState.QUARANTINED
        )

    def get_review_required_laws(self) -> list[str]:
        """Return law IDs currently requiring review."""
        return sorted(
            law_id for law_id, entry in self._entries.items()
            if entry.state == LawHealthState.REVIEW_REQUIRED
        )

    # ── State mapping ────────────────────────────────────────────────────

    @staticmethod
    def _to_legacy_state(state: LawHealthState) -> str:
        """Map 4-state enum to legacy 3-state strings for backward compat."""
        if state == LawHealthState.HEALTHY:
            return HEALTH_ACTIVE
        if state == LawHealthState.DEGRADED:
            return HEALTH_DEGRADED
        if state == LawHealthState.QUARANTINED:
            return HEALTH_QUARANTINED
        # REVIEW_REQUIRED maps to degraded in legacy
        return HEALTH_DEGRADED
