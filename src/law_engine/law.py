"""LawDefinition model for the Law Engine.

Defines the declarative LawDefinition structure used by the Law Engine for
rule registration, evaluation, and governance. Each law specifies conditions
(matching graph nodes/edges) and an action (violation output).

LawCategory covers 7 domains: structural, dependency, naming, complexity,
security, performance, consistency.

EvalMode selects the evaluation backend: Rete network, graph query, or
Z3 solver (Phase 3 stub).
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field


class LawCategory(str, enum.Enum):
    STRUCTURAL = "structural"
    DEPENDENCY = "dependency"
    NAMING = "naming"
    COMPLEXITY = "complexity"
    SECURITY = "security"
    PERFORMANCE = "performance"
    CONSISTENCY = "consistency"


class EvalMode(str, enum.Enum):
    RETE = "rete"
    QUERY = "query"
    SOLVER = "solver"


class LawDefinition(BaseModel):
    """A declarative law governing codebase behaviour.

    Attributes:
        law_id: Unique identifier (e.g. ``"STR-001"``).
        name: Human-readable name.
        description: What the law enforces.
        category: One of the 7 LawCategory values.
        eval_mode: Evaluation backend selector.
        conditions: Condition dicts consumed by RuleCompiler.
        action: Action dict consumed by RuleCompiler.
        weight: Relative importance (higher = more critical).
        health_state: Always ``"HEALTHY"`` in Phase 2 (v3.3 Fix 3 deferred).
        enabled: Whether the law is active.
        tags: Free-form tags for filtering.
        version: Semantic version of the law definition.
    """

    law_id: str
    name: str
    description: str
    category: LawCategory
    eval_mode: EvalMode = EvalMode.RETE
    conditions: list[dict] = Field(default_factory=list)
    action: dict = Field(default_factory=dict)
    weight: float = 1.0
    health_state: str = "HEALTHY"
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    version: str = "1.0"
