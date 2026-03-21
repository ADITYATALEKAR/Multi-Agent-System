"""Unit tests for 4-state LawGovernance model (v3.3 Fix 3).

Covers:
    - HEALTHY -> DEGRADED -> REVIEW_REQUIRED escalation
    - REVIEW_REQUIRED -> QUARANTINED (manual approval)
    - QUARANTINED -> HEALTHY (manual restoration)
    - Recovery: DEGRADED -> HEALTHY when failure rate drops
    - Legacy API backward compatibility
"""

from __future__ import annotations

import pytest

from src.law_engine.governance import (
    HEALTH_ACTIVE,
    HEALTH_DEGRADED,
    HEALTH_QUARANTINED,
    LawGovernance,
    LawHealthState,
)


def test_initial_state_is_healthy():
    """New laws should start in HEALTHY state."""
    gov = LawGovernance()
    assert gov.get_health_state("NEW-001") == LawHealthState.HEALTHY
    assert gov.get_health("NEW-001") == HEALTH_ACTIVE


def test_escalation_to_degraded():
    """Moderate failure rate should escalate to DEGRADED."""
    gov = LawGovernance(window_size=10, degraded_threshold=0.3)

    # 4 failures + 6 successes = 40% failure rate > 30% threshold
    for _ in range(4):
        gov.record_evaluation("LAW-001", success=False)
    for _ in range(6):
        gov.record_evaluation("LAW-001", success=True)

    gov.check_health("LAW-001")
    assert gov.get_health_state("LAW-001") == LawHealthState.DEGRADED
    assert gov.get_health("LAW-001") == HEALTH_DEGRADED


def test_escalation_to_review_required():
    """High failure rate should escalate to REVIEW_REQUIRED."""
    gov = LawGovernance(window_size=10, review_threshold=0.7)

    # 8 failures + 2 successes = 80% failure rate > 70% review threshold
    for _ in range(8):
        gov.record_evaluation("LAW-002", success=False)
    for _ in range(2):
        gov.record_evaluation("LAW-002", success=True)

    gov.check_health("LAW-002")
    assert gov.get_health_state("LAW-002") == LawHealthState.REVIEW_REQUIRED
    assert "LAW-002" in gov.get_review_required_laws()


def test_no_auto_quarantine():
    """Even 100% failure rate should NOT auto-quarantine (v3.3 Fix 3)."""
    gov = LawGovernance(window_size=5)

    for _ in range(5):
        gov.record_evaluation("LAW-003", success=False)

    gov.check_health("LAW-003")
    assert gov.get_health_state("LAW-003") != LawHealthState.QUARANTINED
    assert gov.get_health_state("LAW-003") == LawHealthState.REVIEW_REQUIRED


def test_manual_quarantine_approval():
    """Quarantine requires human approval from REVIEW_REQUIRED state."""
    gov = LawGovernance(window_size=5)

    # Escalate to REVIEW_REQUIRED
    for _ in range(5):
        gov.record_evaluation("LAW-004", success=False)
    gov.check_health("LAW-004")
    assert gov.get_health_state("LAW-004") == LawHealthState.REVIEW_REQUIRED

    # Approve quarantine
    result = gov.approve_quarantine("LAW-004", "reviewer@example.com")
    assert result is True
    assert gov.get_health_state("LAW-004") == LawHealthState.QUARANTINED
    assert "LAW-004" in gov.get_quarantined_laws()


def test_quarantine_approval_requires_review_state():
    """Cannot approve quarantine if law is not in REVIEW_REQUIRED."""
    gov = LawGovernance()

    # Law is HEALTHY — cannot quarantine
    result = gov.approve_quarantine("LAW-005", "reviewer")
    assert result is False
    assert gov.get_health_state("LAW-005") == LawHealthState.HEALTHY


def test_manual_restoration():
    """Restoring from quarantine requires human action."""
    gov = LawGovernance(window_size=5)

    # Get to QUARANTINED
    for _ in range(5):
        gov.record_evaluation("LAW-006", success=False)
    gov.check_health("LAW-006")
    gov.approve_quarantine("LAW-006", "admin")
    assert gov.get_health_state("LAW-006") == LawHealthState.QUARANTINED

    # Restore
    result = gov.restore_from_quarantine("LAW-006", "admin")
    assert result is True
    assert gov.get_health_state("LAW-006") == LawHealthState.HEALTHY


def test_recovery_from_degraded():
    """Failure rate dropping should recover from DEGRADED to HEALTHY."""
    gov = LawGovernance(window_size=10, degraded_threshold=0.4)

    # Push into DEGRADED (5 failures = 50% > 40%)
    for _ in range(5):
        gov.record_evaluation("LAW-007", success=False)
    for _ in range(5):
        gov.record_evaluation("LAW-007", success=True)
    gov.check_health("LAW-007")
    assert gov.get_health_state("LAW-007") == LawHealthState.DEGRADED

    # Add 10 more successes (now window has mostly successes)
    for _ in range(10):
        gov.record_evaluation("LAW-007", success=True)
    gov.check_health("LAW-007")
    assert gov.get_health_state("LAW-007") == LawHealthState.HEALTHY


def test_quarantined_stays_quarantined():
    """Quarantined laws should not auto-recover."""
    gov = LawGovernance()
    gov.quarantine("LAW-008", "manual test")

    # Even with good evaluations, stays quarantined
    for _ in range(20):
        gov.record_evaluation("LAW-008", success=True)

    state = gov.check_health("LAW-008")
    assert state == HEALTH_QUARANTINED


def test_request_review_manually():
    """Manual review request should set REVIEW_REQUIRED."""
    gov = LawGovernance()
    gov.request_review("LAW-009", "suspicious behavior")
    assert gov.get_health_state("LAW-009") == LawHealthState.REVIEW_REQUIRED
    assert "LAW-009" in gov.get_review_required_laws()


def test_evaluate_health_returns_4state():
    """evaluate_health should return the proper LawHealthState enum."""
    gov = LawGovernance()
    state = gov.evaluate_health("FRESH-001")
    assert isinstance(state, LawHealthState)
    assert state == LawHealthState.HEALTHY


def test_legacy_api_backward_compat():
    """Legacy quarantine/restore/get_health should still work."""
    gov = LawGovernance()

    # Legacy quarantine (bypasses review)
    gov.quarantine("LEGACY-001", "testing")
    assert gov.get_health("LEGACY-001") == HEALTH_QUARANTINED

    # Legacy restore
    gov.restore("LEGACY-001")
    assert gov.get_health("LEGACY-001") == HEALTH_ACTIVE
