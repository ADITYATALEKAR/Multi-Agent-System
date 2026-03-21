"""Comprehensive unit tests for Phase 2 Law Engine components.

Covers LawDefinition model, LawCategory enum, LawLibrary, LawEvaluator,
and LawGovernance (quarantine, restore, auto-quarantine, evaluation recording).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.law_engine.law import LawDefinition, LawCategory, EvalMode
from src.law_engine.library import LawLibrary
from src.law_engine.evaluator import LawEvaluator
from src.law_engine.governance import (
    LawGovernance,
    HEALTH_ACTIVE,
    HEALTH_QUARANTINED,
    HEALTH_DEGRADED,
)
from src.dfe.rete import ReteNetwork
from src.dfe.compiler import RuleCompiler
from src.core.fact import AddNode, GraphDelta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_law(
    law_id: str = "TEST-001",
    name: str = "Test Law",
    description: str = "A test law.",
    category: LawCategory = LawCategory.STRUCTURAL,
    eval_mode: EvalMode = EvalMode.RETE,
    weight: float = 1.0,
    enabled: bool = True,
    conditions: list[dict] | None = None,
    action: dict | None = None,
) -> LawDefinition:
    return LawDefinition(
        law_id=law_id,
        name=name,
        description=description,
        category=category,
        eval_mode=eval_mode,
        weight=weight,
        enabled=enabled,
        conditions=conditions or [{"entity": "node", "type": "cycle", "bind": "c"}],
        action=action or {
            "type": "violation",
            "message": "Test violation: $c",
            "confidence": 0.9,
        },
    )


def _make_delta(node_type: str = "cycle", source: str = "test") -> GraphDelta:
    """Create a minimal GraphDelta containing one AddNode operation."""
    node_id = uuid4()
    return GraphDelta(
        sequence_number=0,
        source=source,
        operations=[
            AddNode(node_id=node_id, node_type=node_type, attributes={}),
        ],
    )


# ===========================================================================
# 1. test_law_definition_model
# ===========================================================================


def test_law_definition_model():
    """LawDefinition should accept all required fields and expose defaults."""
    law = _make_law()

    assert law.law_id == "TEST-001", "law_id should be preserved"
    assert law.name == "Test Law", "name should be preserved"
    assert law.category == LawCategory.STRUCTURAL, "category should be STRUCTURAL"
    assert law.eval_mode == EvalMode.RETE, "default eval_mode should be RETE"
    assert law.weight == 1.0, "default weight should be 1.0"
    assert law.enabled is True, "laws should be enabled by default"
    assert law.health_state == "HEALTHY", "default health_state should be HEALTHY"
    assert isinstance(law.conditions, list), "conditions should be a list"
    assert isinstance(law.action, dict), "action should be a dict"
    assert law.version == "1.0", "default version should be 1.0"
    assert isinstance(law.tags, list), "tags should default to an empty list"


# ===========================================================================
# 2. test_law_category_enum
# ===========================================================================


def test_law_category_enum():
    """LawCategory should contain exactly 7 values covering all domains."""
    expected = {
        "structural",
        "dependency",
        "naming",
        "complexity",
        "security",
        "performance",
        "consistency",
    }

    actual = {member.value for member in LawCategory}

    assert actual == expected, (
        f"LawCategory values mismatch. "
        f"Missing: {expected - actual}, Extra: {actual - expected}"
    )
    assert len(LawCategory) == 7, "LawCategory should have exactly 7 members"


# ===========================================================================
# 3. test_law_library_loads_100_plus_laws
# ===========================================================================


def test_law_library_loads_100_plus_laws():
    """LawLibrary should load at least 100 built-in laws on construction."""
    library = LawLibrary()

    assert library.count >= 100, (
        f"Expected at least 100 built-in laws, got {library.count}"
    )

    all_laws = library.all_laws()
    assert len(all_laws) == library.count, (
        "all_laws() length should match count property"
    )

    # Every law must have a unique law_id
    law_ids = [law.law_id for law in all_laws]
    assert len(law_ids) == len(set(law_ids)), "All law IDs must be unique"


# ===========================================================================
# 4. test_law_library_get_by_category
# ===========================================================================


def test_law_library_get_by_category():
    """get_by_category should return laws only for the requested category."""
    library = LawLibrary()

    for category in LawCategory:
        laws = library.get_by_category(category)
        assert len(laws) > 0, (
            f"Expected at least one law for category '{category.value}'"
        )
        for law in laws:
            assert law.category == category, (
                f"Law '{law.law_id}' has category '{law.category.value}' "
                f"but was returned for category '{category.value}'"
            )


# ===========================================================================
# 5. test_law_library_register_custom_law
# ===========================================================================


def test_law_library_register_custom_law():
    """Registering a custom law should make it retrievable."""
    library = LawLibrary()
    original_count = library.count

    custom = _make_law(
        law_id="CUSTOM-001",
        name="Custom Test Law",
        category=LawCategory.SECURITY,
    )
    library.register(custom)

    assert library.count == original_count + 1, (
        "Count should increase by 1 after registering a new law"
    )

    retrieved = library.get("CUSTOM-001")
    assert retrieved is not None, "Custom law should be retrievable by ID"
    assert retrieved.law_id == "CUSTOM-001", "Retrieved law should have the correct ID"
    assert retrieved.category == LawCategory.SECURITY, (
        "Retrieved law should have the correct category"
    )

    # Re-registering the same ID should overwrite
    updated = _make_law(
        law_id="CUSTOM-001",
        name="Updated Custom Law",
        category=LawCategory.NAMING,
    )
    library.register(updated)
    assert library.count == original_count + 1, (
        "Count should not increase when overwriting an existing law"
    )
    assert library.get("CUSTOM-001").name == "Updated Custom Law", (
        "Overwritten law should reflect the updated name"
    )


# ===========================================================================
# 6. test_law_evaluator_register_laws
# ===========================================================================


def test_law_evaluator_register_laws():
    """LawEvaluator.register_laws should compile and register laws in the Rete network."""
    rete = ReteNetwork()
    compiler = RuleCompiler()
    library = LawLibrary()

    evaluator = LawEvaluator(rete, compiler, library)
    evaluator.register_laws()

    assert rete.rule_count > 0, (
        "Rete network should have registered rules after register_laws()"
    )

    # All enabled, non-solver laws should be registered
    expected_count = sum(
        1
        for law in library.enabled_laws()
        if law.eval_mode != EvalMode.SOLVER
    )
    assert rete.rule_count == expected_count, (
        f"Expected {expected_count} rules registered, got {rete.rule_count}"
    )


# ===========================================================================
# 7. test_law_evaluator_evaluate_delta
# ===========================================================================


def test_law_evaluator_evaluate_delta():
    """Evaluating a delta with a matching node should produce violations."""
    rete = ReteNetwork()
    compiler = RuleCompiler()
    library = LawLibrary()

    evaluator = LawEvaluator(rete, compiler, library)

    # Register only a single specific law to get predictable results
    test_law = _make_law(
        law_id="EVAL-TEST-001",
        conditions=[{"entity": "node", "type": "test_node_xyz", "bind": "t"}],
        action={
            "type": "violation",
            "message": "Test violation for $t",
            "confidence": 0.85,
        },
    )
    evaluator.register_laws([test_law])

    # Delta with a matching node type
    delta = _make_delta(node_type="test_node_xyz")
    derived = evaluator.evaluate_delta(delta)

    assert len(derived) == 1, (
        f"Expected exactly 1 violation from matching delta, got {len(derived)}"
    )
    assert derived[0].payload["rule_id"] == "EVAL-TEST-001", (
        "Violation should reference the correct rule_id"
    )

    # Delta with a non-matching node type should produce no violations
    non_matching_delta = _make_delta(node_type="no_match_type")
    derived2 = evaluator.evaluate_delta(non_matching_delta)
    assert len(derived2) == 0, (
        "Non-matching delta should produce no violations"
    )


# ===========================================================================
# 8. test_law_governance_quarantine_restore
# ===========================================================================


def test_law_governance_quarantine_restore():
    """Quarantining and restoring a law should toggle health state correctly."""
    gov = LawGovernance()

    # Fresh law should be active
    assert gov.get_health("LAW-A") == HEALTH_ACTIVE, (
        "Newly tracked law should be in active state"
    )

    # Quarantine
    gov.quarantine("LAW-A", "Too many false positives")
    assert gov.get_health("LAW-A") == HEALTH_QUARANTINED, (
        "Law should be quarantined after quarantine() call"
    )
    assert "LAW-A" in gov.get_quarantined_laws(), (
        "Quarantined law should appear in get_quarantined_laws()"
    )

    # Restore
    gov.restore("LAW-A")
    assert gov.get_health("LAW-A") == HEALTH_ACTIVE, (
        "Law should be active again after restore() call"
    )
    assert "LAW-A" not in gov.get_quarantined_laws(), (
        "Restored law should not appear in get_quarantined_laws()"
    )

    # Restoring an already-active law should be a no-op
    gov.restore("LAW-A")
    assert gov.get_health("LAW-A") == HEALTH_ACTIVE, (
        "Restoring an active law should keep it active"
    )


# ===========================================================================
# 9. test_law_governance_auto_quarantine
# ===========================================================================


def test_law_governance_auto_quarantine():
    """check_health should auto-escalate to REVIEW_REQUIRED (v3.3 Fix 3).

    v3.3 Fix 3: No auto-quarantine.  High failure rate escalates to
    REVIEW_REQUIRED, requiring human approval for quarantine.
    """
    window_size = 5
    failure_threshold = 0.80

    gov = LawGovernance(
        window_size=window_size,
        failure_threshold=failure_threshold,
    )

    law_id = "AUTO-Q-001"

    # Record all failures (5 out of 5 = 100% failure rate)
    for _ in range(window_size):
        gov.record_evaluation(law_id, success=False)

    state = gov.check_health(law_id)
    # v3.3 Fix 3: auto-escalates to REVIEW_REQUIRED, not QUARANTINED
    assert state == HEALTH_DEGRADED, (
        f"Law with 100% failure rate should be in review (maps to degraded), got '{state}'"
    )
    assert law_id in gov.get_review_required_laws(), (
        "Auto-escalated law should appear in get_review_required_laws()"
    )
    # Manual quarantine approval
    assert gov.approve_quarantine(law_id, "test-reviewer")
    assert law_id in gov.get_quarantined_laws(), (
        "Approved-quarantined law should appear in get_quarantined_laws()"
    )


# ===========================================================================
# 10. test_law_governance_record_evaluation
# ===========================================================================


def test_law_governance_record_evaluation():
    """Recording evaluations should affect health state transitions correctly."""
    window_size = 10
    gov = LawGovernance(
        window_size=window_size,
        failure_threshold=0.80,
    )

    law_id = "EVAL-REC-001"

    # Record 10 successes -- should stay active
    for _ in range(window_size):
        gov.record_evaluation(law_id, success=True)

    state = gov.check_health(law_id)
    assert state == HEALTH_ACTIVE, (
        f"Law with 100% success rate should stay active, got '{state}'"
    )

    # Now record failures to trigger degraded state
    # Half threshold = 0.40; need > 40% failures over the window
    # With window_size=10, recording 5 failures out of 10 = 50% failure rate
    # The deque has maxlen=10 so old successes slide out as new entries arrive
    gov2 = LawGovernance(window_size=10, failure_threshold=0.80)
    law_id2 = "EVAL-REC-002"

    # Fill window: 5 successes, then 5 failures
    for _ in range(5):
        gov2.record_evaluation(law_id2, success=True)
    for _ in range(5):
        gov2.record_evaluation(law_id2, success=False)

    state2 = gov2.check_health(law_id2)
    assert state2 == HEALTH_DEGRADED, (
        f"Law with 50% failure rate (above half-threshold of 40%) "
        f"should be degraded, got '{state2}'"
    )


# ===========================================================================
# 11. test_law_evaluator_get_violations (bonus)
# ===========================================================================


def test_law_evaluator_get_violations():
    """get_violations should return all accumulated violations."""
    rete = ReteNetwork()
    compiler = RuleCompiler()
    library = LawLibrary()

    evaluator = LawEvaluator(rete, compiler, library)

    test_law = _make_law(
        law_id="VIOL-TEST",
        conditions=[{"entity": "node", "type": "sentinel_type", "bind": "s"}],
        action={
            "type": "violation",
            "message": "Sentinel matched: $s",
            "confidence": 0.75,
        },
    )
    evaluator.register_laws([test_law])

    # No violations initially
    assert len(evaluator.get_violations()) == 0, (
        "Should have no violations before any delta evaluation"
    )

    # Produce a violation
    delta = _make_delta(node_type="sentinel_type")
    evaluator.evaluate_delta(delta)

    violations = evaluator.get_violations()
    assert len(violations) == 1, "Should have exactly 1 violation after one matching delta"

    # Clear violations
    evaluator.clear_violations()
    assert len(evaluator.get_violations()) == 0, (
        "Violations should be empty after clear_violations()"
    )


# ===========================================================================
# 12. test_law_definition_eval_mode_enum (bonus)
# ===========================================================================


def test_law_definition_eval_mode_enum():
    """EvalMode should contain rete, query, and solver."""
    expected = {"rete", "query", "solver"}
    actual = {member.value for member in EvalMode}

    assert actual == expected, (
        f"EvalMode values mismatch. Expected {expected}, got {actual}"
    )
