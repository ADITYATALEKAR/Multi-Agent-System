"""Reliability tracking for agents."""

from __future__ import annotations

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class AgentReliability(BaseModel):
    """Per-agent reliability record."""

    agent_id: str
    successes: int = 0
    failures: int = 0
    crashes: int = 0
    reliability: float = 1.0


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

class AgentReliabilityTracker:
    """Tracks agent success/failure/crash rates.

    Each agent starts with a perfect reliability score of 1.0.  The score is
    recomputed after every recorded outcome using:

        base = successes / (successes + failures + crashes)
        penalty = 0.1 * crashes
        reliability = clamp(base - penalty, 0.0, 1.0)

    Crashes carry a harsher penalty (per v3.3 spec) because they signal
    instability rather than mere task failure.
    """

    def __init__(self) -> None:
        self._records: dict[str, AgentReliability] = {}
        log.info("agent_reliability_tracker.init")

    # -- internal helpers ---------------------------------------------------

    def _ensure_record(self, agent_id: str) -> AgentReliability:
        """Return the record for *agent_id*, creating one if absent."""
        if agent_id not in self._records:
            self._records[agent_id] = AgentReliability(agent_id=agent_id)
            log.debug("agent_reliability_tracker.new_record", agent_id=agent_id)
        return self._records[agent_id]

    def _recompute(self, agent_id: str) -> None:
        """Recompute the reliability score for *agent_id*."""
        rec = self._records[agent_id]
        total = rec.successes + rec.failures + rec.crashes
        if total > 0:
            base = rec.successes / total
            penalty = 0.1 * rec.crashes
            rec.reliability = max(0.0, min(1.0, base - penalty))
        else:
            rec.reliability = 1.0

    # -- public API ---------------------------------------------------------

    def record_success(self, agent_id: str) -> None:
        """Record a successful task execution for *agent_id*."""
        rec = self._ensure_record(agent_id)
        rec.successes += 1
        self._recompute(agent_id)
        log.info(
            "agent_reliability_tracker.success",
            agent_id=agent_id,
            reliability=rec.reliability,
        )

    def record_failure(self, agent_id: str) -> None:
        """Record a failed task execution for *agent_id*."""
        rec = self._ensure_record(agent_id)
        rec.failures += 1
        self._recompute(agent_id)
        log.info(
            "agent_reliability_tracker.failure",
            agent_id=agent_id,
            reliability=rec.reliability,
        )

    def record_crash(self, agent_id: str) -> None:
        """Record a crash for *agent_id* (harsher penalty)."""
        rec = self._ensure_record(agent_id)
        rec.crashes += 1
        self._recompute(agent_id)
        log.warning(
            "agent_reliability_tracker.crash",
            agent_id=agent_id,
            reliability=rec.reliability,
        )

    def get_reliability(self, agent_id: str) -> float:
        """Return the reliability score for *agent_id*.

        Returns 1.0 for unknown agents (optimistic default).
        """
        if agent_id not in self._records:
            return 1.0
        return self._records[agent_id].reliability

    def get_record(self, agent_id: str) -> AgentReliability:
        """Return the full reliability record for *agent_id*."""
        return self._ensure_record(agent_id)
