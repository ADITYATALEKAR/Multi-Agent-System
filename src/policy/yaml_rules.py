"""YAML-based policy rule engine.

Evaluates repair actions against declarative YAML rules covering:
- Environment restrictions
- Action type permissions
- Risk thresholds
- Time-of-day windows
- Required approvals
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger()


class PolicyRule(BaseModel):
    """A single policy rule from YAML configuration."""

    rule_id: str
    description: str = ""
    enabled: bool = True
    # Conditions (all must match for rule to apply)
    action_types: list[str] = Field(default_factory=list)  # empty = all
    environments: list[str] = Field(default_factory=list)  # empty = all
    max_risk: float = 1.0
    min_confidence: float = 0.0
    blocked_hours: list[int] = Field(default_factory=list)  # hours UTC when blocked
    require_approval: bool = False
    # Effect
    effect: str = "allow"  # "allow" or "deny"


class RuleEvaluation(BaseModel):
    """Result of evaluating a single rule."""

    rule_id: str
    matched: bool
    effect: str
    reason: str = ""


class YAMLRuleEngine:
    """Evaluates actions against a set of declarative policy rules.

    Rules are evaluated in order. First matching rule determines the outcome.
    If no rule matches, the default effect is "allow".
    """

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self._rules: list[PolicyRule] = rules or []

    def load_rules(self, rules_data: list[dict[str, Any]]) -> None:
        """Load rules from parsed YAML data (list of dicts)."""
        self._rules = [PolicyRule(**r) for r in rules_data]
        logger.info("yaml_rules.loaded", count=len(self._rules))

    def add_rule(self, rule: PolicyRule) -> None:
        """Add a single rule."""
        self._rules.append(rule)

    def evaluate(
        self,
        action_type: str,
        environment: str,
        risk: float,
        confidence: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[str, list[RuleEvaluation]]:
        """Evaluate an action against all rules.

        Returns:
            (effect, evaluations) — "allow" or "deny", plus all rule evaluations.
        """
        evaluations: list[RuleEvaluation] = []
        now = datetime.utcnow()

        for rule in self._rules:
            if not rule.enabled:
                continue

            matched, reason = self._match_rule(rule, action_type, environment, risk, confidence, now)
            evaluations.append(RuleEvaluation(
                rule_id=rule.rule_id,
                matched=matched,
                effect=rule.effect if matched else "skip",
                reason=reason,
            ))

            if matched:
                logger.debug(
                    "yaml_rules.matched",
                    rule_id=rule.rule_id,
                    effect=rule.effect,
                    reason=reason,
                )
                return rule.effect, evaluations

        # Default: allow
        return "allow", evaluations

    def _match_rule(
        self,
        rule: PolicyRule,
        action_type: str,
        environment: str,
        risk: float,
        confidence: float,
        now: datetime,
    ) -> tuple[bool, str]:
        """Check if a rule matches the given action context.

        Returns:
            (matched, reason)
        """
        # Action type filter
        if rule.action_types and action_type not in rule.action_types:
            return False, "action_type_mismatch"

        # Environment filter
        if rule.environments and environment not in rule.environments:
            return False, "environment_mismatch"

        # Risk threshold
        if risk > rule.max_risk:
            return True, f"risk {risk:.2f} exceeds max {rule.max_risk:.2f}"

        # Confidence threshold
        if confidence < rule.min_confidence:
            return True, f"confidence {confidence:.2f} below min {rule.min_confidence:.2f}"

        # Blocked hours
        if rule.blocked_hours and now.hour in rule.blocked_hours:
            return True, f"blocked_hour {now.hour}"

        # If all conditions match and it's an allow rule, match it
        if rule.effect == "allow":
            return True, "conditions_met"

        # For deny rules, match if conditions pass
        if rule.effect == "deny":
            conditions_match = True
            if rule.action_types and action_type not in rule.action_types:
                conditions_match = False
            if rule.environments and environment not in rule.environments:
                conditions_match = False
            return conditions_match, "deny_conditions_met"

        return False, "no_match"

    @property
    def rule_count(self) -> int:
        return len(self._rules)
