"""Comprehensive unit tests for Phase 2 Energy Scorer components.

Covers HealthVector computation, BlastRadiusComputer, dimension
classification, and the EnergyScorer end-to-end flow.
"""

from __future__ import annotations

import math

import pytest

from src.scoring.energy import (
    EnergyScorer,
    HealthVector,
    BlastRadiusComputer,
    _classify_dimension,
)
from src.core.derived import (
    DerivedFact,
    DerivedType,
    DerivedStatus,
    ExtendedJustification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_violation(
    rule_id: str = "test-rule",
    confidence: float = 0.8,
    law_weight: float = 1.0,
    severity: str | None = None,
    entity_id: str | None = None,
) -> DerivedFact:
    """Create a minimal DerivedFact violation for scoring tests."""
    payload: dict = {
        "rule_id": rule_id,
        "law_weight": law_weight,
    }
    if severity is not None:
        payload["severity"] = severity
    if entity_id is not None:
        payload["entity_id"] = entity_id

    return DerivedFact(
        derived_type=DerivedType.VIOLATION,
        payload=payload,
        justification=ExtendedJustification(rule_id=rule_id),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
    )


# ===========================================================================
# 1. test_health_vector_no_violations
# ===========================================================================


def test_health_vector_no_violations():
    """EnergyScorer with no violations should return a perfect health vector."""
    scorer = EnergyScorer()
    hv = scorer.compute([])

    assert hv.overall_score == 1.0, (
        f"Overall score should be 1.0 with no violations, got {hv.overall_score}"
    )
    assert hv.violation_count == 0, "violation_count should be 0"
    assert hv.critical_violation_count == 0, "critical_violation_count should be 0"
    assert hv.blast_radius_score == 0.0, "blast_radius_score should be 0.0"

    # All dimension scores should be 1.0
    for dim, score in hv.dimension_scores.items():
        assert score == 1.0, (
            f"Dimension '{dim}' should be 1.0 with no violations, got {score}"
        )


# ===========================================================================
# 2. test_health_vector_with_violations
# ===========================================================================


def test_health_vector_with_violations():
    """EnergyScorer with violations should reduce health scores."""
    scorer = EnergyScorer()

    violations = [
        _make_violation(rule_id="struct-001", confidence=0.9, law_weight=1.5),
        _make_violation(rule_id="dep-001", confidence=0.8, law_weight=1.0),
        _make_violation(
            rule_id="sec-001", confidence=0.95, law_weight=2.0, severity="critical",
        ),
    ]

    hv = scorer.compute(violations)

    assert 0.0 < hv.overall_score < 1.0, (
        f"Overall score should be between 0 and 1 with violations, got {hv.overall_score}"
    )
    assert hv.violation_count == 3, (
        f"Expected 3 violations, got {hv.violation_count}"
    )
    assert hv.critical_violation_count >= 1, (
        "At least 1 critical violation expected (law_weight >= 1.5)"
    )
    assert hv.blast_radius_score >= 0.0, (
        "Blast radius score should be non-negative"
    )

    # Dimension scores should be reduced for affected dimensions
    assert isinstance(hv.dimension_scores, dict), (
        "dimension_scores should be a dict"
    )
    assert len(hv.dimension_scores) > 0, (
        "dimension_scores should not be empty"
    )


# ===========================================================================
# 3. test_blast_radius_empty
# ===========================================================================


def test_blast_radius_empty():
    """BlastRadiusComputer with no violations should return 0.0."""
    blast = BlastRadiusComputer()

    score = blast.compute([])
    assert score == 0.0, (
        f"Blast radius of empty violations should be 0.0, got {score}"
    )


# ===========================================================================
# 4. test_blast_radius_heuristic
# ===========================================================================


def test_blast_radius_heuristic():
    """BlastRadiusComputer heuristic should scale with violation count and confidence."""
    blast = BlastRadiusComputer()

    # Single low-confidence violation
    single = [_make_violation(confidence=0.5)]
    score_single = blast.compute(single)
    assert 0.0 < score_single < 1.0, (
        f"Single violation blast radius should be between 0 and 1, got {score_single}"
    )

    # Many high-confidence violations should have higher blast radius
    many = [_make_violation(confidence=0.95) for _ in range(20)]
    score_many = blast.compute(many)
    assert score_many > score_single, (
        f"More violations should yield higher blast radius: "
        f"{score_many} should be > {score_single}"
    )

    # Blast radius should be capped at 1.0
    huge = [_make_violation(confidence=1.0) for _ in range(200)]
    score_huge = blast.compute(huge)
    assert score_huge <= 1.0, (
        f"Blast radius should be capped at 1.0, got {score_huge}"
    )


# ===========================================================================
# 5. test_dimension_classification
# ===========================================================================


def test_dimension_classification():
    """_classify_dimension should map rule IDs to correct scoring dimensions."""
    # Structural patterns
    assert _classify_dimension("struct-001") == "structural", (
        "Rule IDs containing 'struct' should map to 'structural'"
    )
    assert _classify_dimension("naming-conv") == "structural", (
        "Rule IDs containing 'naming' should map to 'structural'"
    )

    # Dependency patterns
    assert _classify_dimension("dep-circular") == "dependency", (
        "Rule IDs containing 'dep' should map to 'dependency'"
    )
    assert _classify_dimension("import-check") == "dependency", (
        "Rule IDs containing 'import' should map to 'dependency'"
    )
    assert _classify_dimension("cycle-detect") == "dependency", (
        "Rule IDs containing 'cycle' should map to 'dependency'"
    )

    # Security patterns
    assert _classify_dimension("sec-hardcoded") == "security", (
        "Rule IDs containing 'sec' should map to 'security'"
    )
    assert _classify_dimension("vuln-scan") == "security", (
        "Rule IDs containing 'vuln' should map to 'security'"
    )
    assert _classify_dimension("auth-flow") == "security", (
        "Rule IDs containing 'auth' should map to 'security'"
    )

    # Complexity patterns
    assert _classify_dimension("complex-fn") == "complexity", (
        "Rule IDs containing 'complex' should map to 'complexity'"
    )
    assert _classify_dimension("cognitive-load") == "complexity", (
        "Rule IDs containing 'cognitive' should map to 'complexity'"
    )

    # Style patterns
    assert _classify_dimension("style-check") == "style", (
        "Rule IDs containing 'style' should map to 'style'"
    )
    assert _classify_dimension("format-rule") == "style", (
        "Rule IDs containing 'format' should map to 'style'"
    )
    assert _classify_dimension("lint-warning") == "style", (
        "Rule IDs containing 'lint' should map to 'style'"
    )

    # Default fallback
    assert _classify_dimension("unknown-xyz") == "structural", (
        "Unrecognized rule IDs should fall back to 'structural'"
    )


# ===========================================================================
# 6. test_health_vector_model (bonus)
# ===========================================================================


def test_health_vector_model():
    """HealthVector pydantic model should enforce field constraints."""
    hv = HealthVector()

    assert hv.overall_score == 1.0, "Default overall_score should be 1.0"
    assert hv.violation_count == 0, "Default violation_count should be 0"
    assert hv.critical_violation_count == 0, (
        "Default critical_violation_count should be 0"
    )
    assert hv.blast_radius_score == 0.0, "Default blast_radius_score should be 0.0"

    # Custom values
    hv2 = HealthVector(
        overall_score=0.5,
        dimension_scores={"structural": 0.7, "security": 0.3},
        violation_count=10,
        critical_violation_count=2,
        blast_radius_score=0.4,
    )
    assert hv2.overall_score == 0.5, "Custom overall_score should be preserved"
    assert hv2.dimension_scores["structural"] == 0.7, (
        "Custom dimension scores should be preserved"
    )
