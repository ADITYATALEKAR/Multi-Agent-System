from __future__ import annotations

"""Unit tests for the Phase 6 policy layer: YAML rules, OPA integration, and PolicyEngine."""

import pytest

from src.policy.yaml_rules import YAMLRuleEngine, PolicyRule
from src.policy.opa import OPAIntegration, OPAInput
from src.policy.engine import PolicyEngine, PolicyDecision, PolicyDecisionType


# ---------------------------------------------------------------------------
# YAMLRuleEngine tests
# ---------------------------------------------------------------------------


def test_yaml_rule_engine_allow_default():
    """No rules loaded -> default effect is 'allow'."""
    engine = YAMLRuleEngine()
    effect, evals = engine.evaluate(
        action_type="update_attribute",
        environment="staging",
        risk=0.5,
        confidence=0.8,
    )
    assert effect == "allow"
    assert evals == []


def test_yaml_rule_engine_deny_high_risk():
    """A deny rule with max_risk=0.7 blocks actions with risk > 0.7."""
    rule = PolicyRule(
        rule_id="deny-high-risk",
        effect="deny",
        max_risk=0.7,
    )
    engine = YAMLRuleEngine(rules=[rule])
    effect, evals = engine.evaluate(
        action_type="update_attribute",
        environment="staging",
        risk=0.9,
        confidence=0.8,
    )
    assert effect == "deny"
    assert len(evals) == 1
    assert evals[0].matched is True
    assert evals[0].effect == "deny"


def test_yaml_rule_engine_load_rules():
    """load_rules accepts a list of dicts and populates internal rules."""
    engine = YAMLRuleEngine()
    rules_data = [
        {"rule_id": "r1", "description": "first rule", "effect": "allow"},
        {"rule_id": "r2", "description": "second rule", "effect": "deny", "max_risk": 0.5},
    ]
    engine.load_rules(rules_data)
    assert engine.rule_count == 2


# ---------------------------------------------------------------------------
# OPAIntegration tests
# ---------------------------------------------------------------------------


def test_opa_local_eval_passes():
    """All checks pass -> allowed=True, reason mentions 'all checks passed'."""
    opa = OPAIntegration()
    inp = OPAInput(
        action_type="update_attribute",
        environment="staging",
        risk=0.3,
        confidence=0.8,
    )
    result = opa.evaluate(inp)
    assert result.allowed is True
    assert "all checks passed" in result.reason
    assert result.violations == []


def test_opa_local_eval_high_risk_denied():
    """Risk > 0.8 (default threshold) -> denied."""
    opa = OPAIntegration()
    inp = OPAInput(
        action_type="update_attribute",
        environment="staging",
        risk=0.95,
        confidence=0.8,
    )
    result = opa.evaluate(inp)
    assert result.allowed is False
    assert any("risk" in v for v in result.violations)


def test_opa_local_eval_low_confidence_denied():
    """Confidence < 0.3 (default floor) -> denied."""
    opa = OPAIntegration()
    inp = OPAInput(
        action_type="update_attribute",
        environment="staging",
        risk=0.1,
        confidence=0.1,
    )
    result = opa.evaluate(inp)
    assert result.allowed is False
    assert any("confidence" in v for v in result.violations)


def test_opa_production_requires_approval():
    """Production environment -> require_approval=True even when allowed."""
    opa = OPAIntegration()
    inp = OPAInput(
        action_type="update_attribute",
        environment="production",
        risk=0.1,
        confidence=0.9,
    )
    result = opa.evaluate(inp)
    assert result.require_approval is True


# ---------------------------------------------------------------------------
# PolicyEngine tests
# ---------------------------------------------------------------------------


def test_policy_engine_approve():
    """Both YAML (no rules) and OPA pass -> APPROVE."""
    engine = PolicyEngine()
    decision = engine.evaluate(
        action_type="update_attribute",
        environment="staging",
        risk=0.2,
        confidence=0.9,
    )
    assert decision.decision == PolicyDecisionType.APPROVE
    assert decision.simulated is False
    assert decision.opa_allowed is True
    assert decision.yaml_effect == "allow"


def test_policy_engine_deny():
    """YAML deny rule triggers -> DENY."""
    yaml_engine = YAMLRuleEngine(rules=[
        PolicyRule(rule_id="block-risky", effect="deny", max_risk=0.5),
    ])
    engine = PolicyEngine(yaml_engine=yaml_engine)
    decision = engine.evaluate(
        action_type="update_attribute",
        environment="staging",
        risk=0.8,
        confidence=0.9,
    )
    assert decision.decision == PolicyDecisionType.DENY
    assert decision.yaml_effect == "deny"
    assert len(decision.violations) > 0


def test_policy_engine_simulate():
    """simulate() returns simulated=True with no side-effect differences."""
    engine = PolicyEngine()
    decision = engine.simulate(
        action_type="update_attribute",
        environment="staging",
        risk=0.2,
        confidence=0.9,
    )
    assert decision.simulated is True
    assert decision.decision == PolicyDecisionType.APPROVE


def test_policy_engine_require_approval():
    """Production environment -> REQUIRE_APPROVAL when both layers pass."""
    engine = PolicyEngine()
    decision = engine.evaluate(
        action_type="update_attribute",
        environment="production",
        risk=0.2,
        confidence=0.9,
    )
    assert decision.decision == PolicyDecisionType.REQUIRE_APPROVAL
    assert decision.require_human_approval is True
