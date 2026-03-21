"""Unit tests for the Rule Compiler (DFE Phase 2)."""

from __future__ import annotations

import json

import pytest

from src.core.derived import DerivedType
from src.dfe.compiler import (
    JoinOrderOptimizer,
    RuleCompiler,
    RuleParser,
    RuleRegistry,
    TypeChecker,
)
from src.dfe.rete import AlphaCondition, RuleIR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_rule_def(rule_id: str = "no-orphan") -> dict:
    """Minimal valid rule definition dict."""
    return {
        "rule_id": rule_id,
        "name": "No Orphan Functions",
        "description": "Functions must be contained in a file or class",
        "category": "structure",
        "weight": 1.0,
        "conditions": [
            {"entity": "node", "type": "function", "bind": "f"},
        ],
        "action": {
            "type": "violation",
            "message": "Function $f has no parent",
            "confidence": 0.9,
        },
    }


def _multi_condition_rule_def() -> dict:
    return {
        "rule_id": "class-function-link",
        "name": "Class-Function Link",
        "conditions": [
            {"entity": "node", "type": "class", "bind": "c"},
            {"entity": "node", "type": "function", "bind": "f"},
        ],
        "action": {
            "type": "violation",
            "message": "Class $c linked to function $f",
            "confidence": 0.8,
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRuleParser:
    def test_parse_simple_rule(self) -> None:
        """Parser extracts rule_id, name, conditions, and action from a dict."""
        parser = RuleParser()
        ast = parser.parse(_simple_rule_def())

        assert ast.rule_id == "no-orphan"
        assert ast.name == "No Orphan Functions"
        assert ast.category == "structure"
        assert len(ast.conditions) == 1
        assert ast.conditions[0].entity_type == "node"
        assert ast.conditions[0].type_filter == "function"
        assert ast.conditions[0].bind_var == "f"
        assert ast.action.derived_type == "violation"
        assert ast.action.confidence == 0.9

    def test_parse_multi_condition(self) -> None:
        """Parser handles multiple conditions with different bind variables."""
        parser = RuleParser()
        ast = parser.parse(_multi_condition_rule_def())

        assert len(ast.conditions) == 2
        assert ast.conditions[0].bind_var == "c"
        assert ast.conditions[1].bind_var == "f"
        assert ast.conditions[0].type_filter == "class"
        assert ast.conditions[1].type_filter == "function"


class TestRuleCompiler:
    def test_compile_produces_rule_ir(self) -> None:
        """Compiler produces a RuleIR with AlphaConditions and action."""
        compiler = RuleCompiler()
        rule_ir = compiler.compile(_simple_rule_def())

        assert isinstance(rule_ir, RuleIR)
        assert rule_ir.rule_id == "no-orphan"
        assert len(rule_ir.conditions) == 1
        assert isinstance(rule_ir.conditions[0], AlphaCondition)
        assert rule_ir.conditions[0].entity_type == "node"
        assert rule_ir.conditions[0].type_filter == "function"
        assert rule_ir.action.derived_type == DerivedType.VIOLATION
        assert rule_ir.action.confidence == 0.9

    def test_compile_text_json(self) -> None:
        """compile_text accepts a JSON string and returns a RuleIR."""
        compiler = RuleCompiler()
        rule_def = _simple_rule_def("json-rule")
        rule_text = json.dumps(rule_def)

        rule_ir = compiler.compile_text(rule_text)
        assert isinstance(rule_ir, RuleIR)
        assert rule_ir.rule_id == "json-rule"
        assert rule_ir.action.derived_type == DerivedType.VIOLATION


class TestTypeChecker:
    def test_type_checker_empty_conditions(self) -> None:
        """TypeChecker warns when a rule has zero conditions."""
        checker = TypeChecker()
        parser = RuleParser()
        rule_def = {
            "rule_id": "empty-rule",
            "conditions": [],
            "action": {"type": "violation"},
        }
        ast = parser.parse(rule_def)
        warnings = checker.check(ast)

        assert any("no conditions defined" in w for w in warnings)


class TestJoinOrderOptimizer:
    def test_join_order_optimizer(self) -> None:
        """Optimizer reorders conditions to place more selective ones first."""
        from src.dfe.compiler import ConditionAST

        optimizer = JoinOrderOptimizer()
        conditions = [
            ConditionAST(entity_type="node", type_filter="", bind_var="a"),
            ConditionAST(
                entity_type="edge",
                type_filter="contains",
                bind_var="b",
                attribute_tests={"weight": 1},
            ),
        ]

        optimized = optimizer.optimize(conditions)

        # Edge with type_filter + attribute_tests should come first (more selective)
        assert optimized[0].bind_var == "b"
        assert optimized[1].bind_var == "a"

    def test_selectivity_estimate(self) -> None:
        """estimate_selectivity decreases with more type filters and attribute tests."""
        optimizer = JoinOrderOptimizer()

        # Rule with no type filter => high selectivity estimate
        rule_no_filter = RuleIR(
            rule_id="no-filter",
            conditions=[
                AlphaCondition(
                    condition_id="c0",
                    entity_type="node",
                    type_filter="",
                ),
            ],
        )

        # Rule with type filter => lower selectivity estimate
        rule_with_filter = RuleIR(
            rule_id="with-filter",
            conditions=[
                AlphaCondition(
                    condition_id="c0",
                    entity_type="node",
                    type_filter="class",
                ),
            ],
        )

        sel_no = optimizer.estimate_selectivity(rule_no_filter)
        sel_with = optimizer.estimate_selectivity(rule_with_filter)
        assert sel_with < sel_no


class TestRuleRegistry:
    def test_rule_registry(self) -> None:
        """Registry stores rules and retrieves them by ID and category."""
        registry = RuleRegistry()
        compiler = RuleCompiler()

        rule_ir_1 = compiler.compile(_simple_rule_def("rule-a"))
        rule_ir_2 = compiler.compile({
            **_simple_rule_def("rule-b"),
            "category": "security",
        })

        registry.register(rule_ir_1)
        registry.register(rule_ir_2)

        assert registry.count == 2
        assert registry.get("rule-a") is not None
        assert registry.get("rule-a").rule_id == "rule-a"
        assert registry.get("nonexistent") is None

        structure_rules = registry.get_by_category("structure")
        assert len(structure_rules) == 1
        assert structure_rules[0].rule_id == "rule-a"

        security_rules = registry.get_by_category("security")
        assert len(security_rules) == 1
        assert security_rules[0].rule_id == "rule-b"

        all_rules = registry.all_rules()
        assert len(all_rules) == 2
