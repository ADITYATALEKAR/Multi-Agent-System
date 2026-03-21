"""Rule Compiler: parses rule definitions and compiles to RuleIR.

RuleParser, RuleAST, TypeChecker, JoinOrderOptimizer, RuleRegistry.
v3.3 B2: Cardinality explosion warning at compile time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from src.core.derived import DerivedType
from src.dfe.rete import AlphaCondition, RuleAction, RuleIR
from src.state_graph.schema import SchemaRegistry

logger = structlog.get_logger(__name__)


# ── Rule AST ─────────────────────────────────────────────────────────────────


@dataclass
class ConditionAST:
    """Parsed condition from rule text."""

    entity_type: str  # "node" or "edge"
    type_filter: str  # e.g. "class", "function", "contains"
    bind_var: str = ""
    attribute_tests: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionAST:
    """Parsed action from rule text."""

    derived_type: str = "violation"
    message: str = ""
    confidence: float = 1.0
    payload: dict[str, str] = field(default_factory=dict)


@dataclass
class RuleAST:
    """Abstract syntax tree for a rule definition."""

    rule_id: str
    name: str = ""
    description: str = ""
    category: str = "general"
    weight: float = 1.0
    conditions: list[ConditionAST] = field(default_factory=list)
    action: ActionAST = field(default_factory=ActionAST)


# ── Rule Parser ──────────────────────────────────────────────────────────────


class RuleParser:
    """Parses rule definition dicts into RuleAST."""

    def parse(self, rule_def: dict[str, Any]) -> RuleAST:
        """Parse a rule definition dict into an AST.

        Rule format:
        {
            "rule_id": "no-orphan-function",
            "name": "No Orphan Functions",
            "description": "...",
            "category": "structure",
            "weight": 1.0,
            "conditions": [
                {"entity": "node", "type": "function", "bind": "f"},
                {"entity": "node", "type": "file", "bind": "file"},
            ],
            "action": {
                "type": "violation",
                "message": "Function $f has no parent",
                "confidence": 0.9,
            }
        }
        """
        conditions = []
        for cond_def in rule_def.get("conditions", []):
            attr_tests = {}
            for k, v in cond_def.items():
                if k not in ("entity", "type", "bind"):
                    attr_tests[k] = v
            conditions.append(ConditionAST(
                entity_type=cond_def.get("entity", "node"),
                type_filter=cond_def.get("type", ""),
                bind_var=cond_def.get("bind", ""),
                attribute_tests=attr_tests,
            ))

        action_def = rule_def.get("action", {})
        action = ActionAST(
            derived_type=action_def.get("type", "violation"),
            message=action_def.get("message", ""),
            confidence=action_def.get("confidence", 1.0),
            payload=action_def.get("payload", {}),
        )

        return RuleAST(
            rule_id=rule_def["rule_id"],
            name=rule_def.get("name", rule_def["rule_id"]),
            description=rule_def.get("description", ""),
            category=rule_def.get("category", "general"),
            weight=rule_def.get("weight", 1.0),
            conditions=conditions,
            action=action,
        )


# ── Type Checker ─────────────────────────────────────────────────────────────


class TypeChecker:
    """Validates rule conditions against the schema."""

    def __init__(self, schema: SchemaRegistry | None = None) -> None:
        self._schema = schema

    def check(self, ast: RuleAST) -> list[str]:
        """Return list of warning messages. Empty = valid."""
        warnings: list[str] = []
        if not ast.conditions:
            warnings.append(f"Rule {ast.rule_id}: no conditions defined")
        if not ast.rule_id:
            warnings.append("Rule has empty rule_id")

        if self._schema:
            for cond in ast.conditions:
                if cond.entity_type == "node" and cond.type_filter:
                    if not self._schema.validate_node_type(cond.type_filter):
                        warnings.append(
                            f"Rule {ast.rule_id}: unknown node type '{cond.type_filter}'"
                        )
                elif cond.entity_type == "edge" and cond.type_filter:
                    if not self._schema.validate_edge_type(cond.type_filter):
                        warnings.append(
                            f"Rule {ast.rule_id}: unknown edge type '{cond.type_filter}'"
                        )
        return warnings


# ── Join Order Optimizer ─────────────────────────────────────────────────────


class JoinOrderOptimizer:
    """Optimizes the order of conditions to minimize intermediate results.

    Heuristic: place more selective conditions first (specific type filters,
    more attribute tests). v3.3 B2: warns at compile time if estimated
    selectivity > 50K.
    """

    def optimize(self, conditions: list[ConditionAST]) -> list[ConditionAST]:
        """Reorder conditions by estimated selectivity (most selective first)."""

        def selectivity_score(cond: ConditionAST) -> float:
            score = 0.0
            if cond.type_filter:
                score += 10.0
            score += len(cond.attribute_tests) * 5.0
            if cond.entity_type == "edge":
                score += 2.0  # edges are typically more selective
            return -score  # negative for ascending sort (most selective first)

        return sorted(conditions, key=selectivity_score)

    def estimate_selectivity(self, rule_ir: RuleIR) -> float:
        """Estimate the number of partial matches this rule might generate."""
        base = 1000.0
        for cond in rule_ir.conditions:
            if cond.type_filter:
                base *= 0.1
            if cond.attribute_tests:
                base *= 0.5 ** len(cond.attribute_tests)
        return base


# ── Rule Compiler ────────────────────────────────────────────────────────────


_DERIVED_TYPE_MAP: dict[str, DerivedType] = {
    "violation": DerivedType.VIOLATION,
    "hypothesis": DerivedType.HYPOTHESIS,
    "causal_edge": DerivedType.CAUSAL_EDGE,
    "pattern_match": DerivedType.PATTERN_MATCH,
    "health_aggregate": DerivedType.HEALTH_AGGREGATE,
    "blast_radius": DerivedType.BLAST_RADIUS,
}


class RuleCompiler:
    """Compiles rule definitions into RuleIR for the Rete network."""

    def __init__(self, schema: SchemaRegistry | None = None) -> None:
        self._parser = RuleParser()
        self._checker = TypeChecker(schema)
        self._optimizer = JoinOrderOptimizer()

    def compile(self, rule_def: dict[str, Any]) -> RuleIR:
        """Compile a rule definition dict into RuleIR."""
        ast = self._parser.parse(rule_def)

        # Type check
        warnings = self._checker.check(ast)
        for w in warnings:
            logger.warning("rule_compile_warning", warning=w)

        # Optimize join order
        optimized = self._optimizer.optimize(ast.conditions)

        # Build AlphaConditions
        alpha_conditions = []
        for cond in optimized:
            alpha_conditions.append(AlphaCondition(
                condition_id=f"{ast.rule_id}_{cond.bind_var or len(alpha_conditions)}",
                entity_type=cond.entity_type,
                type_filter=cond.type_filter,
                attribute_tests=cond.attribute_tests,
                bind_var=cond.bind_var,
            ))

        # Build join vars
        join_vars: list[tuple[str, str]] = []
        bind_vars = [c.bind_var for c in optimized if c.bind_var]
        for i in range(1, len(bind_vars)):
            join_vars.append((bind_vars[i - 1], bind_vars[i]))

        # Build action
        derived_type = _DERIVED_TYPE_MAP.get(
            ast.action.derived_type, DerivedType.VIOLATION,
        )
        action = RuleAction(
            derived_type=derived_type,
            payload_template=ast.action.payload,
            confidence=ast.action.confidence,
            message_template=ast.action.message,
        )

        rule_ir = RuleIR(
            rule_id=ast.rule_id,
            name=ast.name,
            description=ast.description,
            conditions=alpha_conditions,
            join_vars=join_vars,
            action=action,
            category=ast.category,
            weight=ast.weight,
        )

        # Selectivity estimate + v3.3 B2 warning
        selectivity = self._optimizer.estimate_selectivity(rule_ir)
        rule_ir.selectivity_estimate = selectivity
        if selectivity > 50_000:
            logger.warning(
                "rule_cardinality_warning",
                rule_id=rule_ir.rule_id,
                estimated_matches=selectivity,
                message="Estimated selectivity > 50K — risk of BetaMemory explosion",
            )

        return rule_ir

    def compile_text(self, rule_text: str) -> RuleIR:
        """Compile from rule text (JSON format). Convenience wrapper."""
        import json
        rule_def = json.loads(rule_text)
        return self.compile(rule_def)

    def estimate_selectivity(self, rule_ir: RuleIR) -> float:
        """Estimate selectivity for a compiled rule."""
        return self._optimizer.estimate_selectivity(rule_ir)


# ── Rule Registry ────────────────────────────────────────────────────────────


class RuleRegistry:
    """Central registry for all compiled rules."""

    def __init__(self) -> None:
        self._rules: dict[str, RuleIR] = {}
        self._by_category: dict[str, list[str]] = {}

    def register(self, rule_ir: RuleIR) -> None:
        self._rules[rule_ir.rule_id] = rule_ir
        if rule_ir.category not in self._by_category:
            self._by_category[rule_ir.category] = []
        self._by_category[rule_ir.category].append(rule_ir.rule_id)

    def get(self, rule_id: str) -> RuleIR | None:
        return self._rules.get(rule_id)

    def get_by_category(self, category: str) -> list[RuleIR]:
        rule_ids = self._by_category.get(category, [])
        return [self._rules[rid] for rid in rule_ids if rid in self._rules]

    def all_rules(self) -> list[RuleIR]:
        return list(self._rules.values())

    @property
    def count(self) -> int:
        return len(self._rules)
