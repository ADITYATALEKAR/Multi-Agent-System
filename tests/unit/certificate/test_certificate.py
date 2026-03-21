"""Unit tests for the certificate module: generator and verifier."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.certificate.generator import CertificateGenerator
from src.certificate.verifier import CertificateVerifier
from src.core.certificate import DiagnosisCertificate, OSGSubgraph, SolverResult
from src.core.counterfactual import (
    CounterfactualConclusion,
    CounterfactualScenario,
    Intervention,
    InterventionType,
)
from src.core.derived import (
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_justification() -> ExtendedJustification:
    return ExtendedJustification(rule_id="test-rule")


def _make_violation(confidence: float = 0.7) -> DerivedFact:
    return DerivedFact(
        derived_type=DerivedType.VIOLATION,
        payload={"rule_id": "law-001", "entity_id": str(uuid4())},
        justification=_make_justification(),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
    )


def _make_root_cause(confidence: float = 0.8) -> DerivedFact:
    return DerivedFact(
        derived_type=DerivedType.HYPOTHESIS,
        payload={"entity_id": str(uuid4())},
        justification=_make_justification(),
        status=DerivedStatus.SUPPORTED,
        confidence=confidence,
    )


def _make_counterfactual(
    conclusion: CounterfactualConclusion = CounterfactualConclusion.CAUSES_SYMPTOM,
    boundary_size: int = 10,
    health_delta: float = 0.5,
) -> CounterfactualScenario:
    return CounterfactualScenario(
        base_state_checkpoint=0,
        intervention=Intervention(
            intervention_type=InterventionType.REMOVE_DELTA,
            target_deltas=[uuid4()],
        ),
        conclusion=conclusion,
        boundary_size=boundary_size,
        resulting_health_delta=health_delta,
    )


# ===================================================================
# CertificateGenerator tests
# ===================================================================


class TestCertificateGenerator:
    """Tests for CertificateGenerator."""

    def test_generate_produces_certificate_with_correct_fields(self) -> None:
        gen = CertificateGenerator()
        investigation_id = uuid4()
        violations = [_make_violation(), _make_violation()]
        root_cause = _make_root_cause(confidence=0.85)
        repair_ids = [uuid4()]
        cf = _make_counterfactual()

        cert = gen.generate(
            investigation_id=investigation_id,
            violations=violations,
            root_cause=root_cause,
            repair_plan_ids=repair_ids,
            counterfactuals=[cf],
            law_health_states={"law_a": "healthy"},
            mandatory_ops=["op1", "op2"],
            floor_budget_pct=12.5,
        )

        assert isinstance(cert, DiagnosisCertificate)
        assert cert.incident_id == investigation_id
        assert root_cause.derived_id in cert.root_cause_hypothesis_ids
        assert len(cert.supporting_evidence) == 2
        assert cert.repair_plan_ids == repair_ids
        assert len(cert.counterfactual_results) == 1
        assert cert.law_health_states == {"law_a": "healthy"}
        assert cert.mandatory_ops_executed == ["op1", "op2"]
        assert cert.floor_budget_consumed_pct == 12.5

    def test_confidence_computation(self) -> None:
        """Confidence = 0.4*rc_conf + 0.35*cf_support + 0.25*evidence_coverage."""
        gen = CertificateGenerator()
        root_cause = _make_root_cause(confidence=1.0)
        # 1 CAUSES_SYMPTOM out of 1 -> cf_conf=1.0
        cf = _make_counterfactual(conclusion=CounterfactualConclusion.CAUSES_SYMPTOM)
        # 5+ violations -> evidence_conf=1.0
        violations = [_make_violation() for _ in range(5)]

        cert = gen.generate(
            investigation_id=uuid4(),
            violations=violations,
            root_cause=root_cause,
            counterfactuals=[cf],
        )

        # expected = 0.4*1.0 + 0.35*1.0 + 0.25*1.0 = 1.0
        expected = round(min(1.0, 0.4 * 1.0 + 0.35 * 1.0 + 0.25 * 1.0), 4)
        assert cert.confidence == expected

    def test_confidence_zero_when_empty(self) -> None:
        gen = CertificateGenerator()
        cert = gen.generate(investigation_id=uuid4())
        # No root cause, no counterfactuals, no violations -> 0
        assert cert.confidence == 0.0

    def test_generate_without_optional_params(self) -> None:
        gen = CertificateGenerator()
        cert = gen.generate(investigation_id=uuid4())
        assert isinstance(cert, DiagnosisCertificate)
        assert cert.root_cause_hypothesis_ids == []
        assert cert.counterfactual_results == []
        assert cert.repair_plan_ids == []


# ===================================================================
# CertificateVerifier tests
# ===================================================================


class TestCertificateVerifier:
    """Tests for CertificateVerifier."""

    def test_verify_passes_valid_certificate(self) -> None:
        gen = CertificateGenerator()
        cert = gen.generate(
            investigation_id=uuid4(),
            violations=[_make_violation()],
            root_cause=_make_root_cause(),
        )

        verifier = CertificateVerifier()
        result = verifier.verify(cert)
        assert result.is_valid
        assert result.checks_performed == 5
        assert result.checks_passed >= 3  # at minimum structure + confidence + serialization

    def test_verify_valid_certificate_with_incident_id(self) -> None:
        """A valid certificate with incident_id should not produce structural errors."""
        cert = DiagnosisCertificate(
            incident_id=uuid4(),
            root_cause_hypothesis_ids=[uuid4()],
            supporting_evidence=[uuid4()],
            confidence=0.7,
        )
        verifier = CertificateVerifier()
        result = verifier.verify(cert)
        # No "error" severity issues expected for a well-formed cert
        errors = [i for i in result.issues if i.severity == "error"]
        assert len(errors) == 0

    def test_round_trip_serialization_check_passes(self) -> None:
        gen = CertificateGenerator()
        cert = gen.generate(
            investigation_id=uuid4(),
            violations=[_make_violation()],
            root_cause=_make_root_cause(),
            counterfactuals=[_make_counterfactual()],
        )
        verifier = CertificateVerifier()
        result = verifier.verify(cert)

        serial_issues = [
            i for i in result.issues if i.category == "serialization"
        ]
        assert len(serial_issues) == 0

    def test_detects_empty_certificate_warning(self) -> None:
        """A certificate with no root cause and no evidence should get a warning."""
        cert = DiagnosisCertificate(
            incident_id=uuid4(),
            confidence=0.0,
        )
        verifier = CertificateVerifier()
        result = verifier.verify(cert)

        struct_warnings = [
            i for i in result.issues
            if i.category == "structure" and i.severity == "warning"
        ]
        assert len(struct_warnings) >= 1
        assert "empty" in struct_warnings[0].message.lower()

    def test_detects_high_floor_budget_consumed_pct_warning(self) -> None:
        """floor_budget_consumed_pct > 100 should produce a confidence warning."""
        cert = DiagnosisCertificate(
            incident_id=uuid4(),
            root_cause_hypothesis_ids=[uuid4()],
            confidence=0.5,
            floor_budget_consumed_pct=150.0,
        )
        verifier = CertificateVerifier()
        result = verifier.verify(cert)

        budget_issues = [
            i for i in result.issues
            if "floor budget" in i.message.lower() or "budget" in i.message.lower()
        ]
        assert len(budget_issues) >= 1

    def test_verify_counterfactual_boundary_zero_warning(self) -> None:
        """A counterfactual with boundary_size=0 and a definitive conclusion
        should trigger a warning."""
        cf = CounterfactualScenario(
            base_state_checkpoint=0,
            intervention=Intervention(
                intervention_type=InterventionType.REMOVE_DELTA,
            ),
            conclusion=CounterfactualConclusion.CAUSES_SYMPTOM,
            boundary_size=0,
        )
        cert = DiagnosisCertificate(
            incident_id=uuid4(),
            root_cause_hypothesis_ids=[uuid4()],
            confidence=0.5,
            counterfactual_results=[cf],
        )
        verifier = CertificateVerifier()
        result = verifier.verify(cert)

        cf_warnings = [
            i for i in result.issues if i.category == "counterfactual"
        ]
        assert len(cf_warnings) >= 1
        assert "boundary_size=0" in cf_warnings[0].message
