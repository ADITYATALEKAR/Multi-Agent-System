"""Repair plan generation with 5 strategies.

Strategies:
1. TemplateStrategy: match known RepairTemplates from memory
2. InverseStrategy: generate inverse operations for each violation delta
3. DependencyStrategy: fix dependency violations by adding/removing edges
4. ConfigStrategy: generate configuration parameter changes
5. CompositeStrategy: combine multiple atomic repairs

Per plan safeguards: per-strategy cap = 20, total cap = 100.
"""

from __future__ import annotations

import enum
from typing import Any, Optional
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, Field

from src.core.derived import DerivedFact, DerivedType
from src.core.fact import (
    AddEdge,
    AddNode,
    DeltaOp,
    GraphDelta,
    RemoveEdge,
    RemoveNode,
    UpdateAttribute,
)

logger = structlog.get_logger()


class RepairActionType(str, enum.Enum):
    ADD_NODE = "add_node"
    REMOVE_NODE = "remove_node"
    ADD_EDGE = "add_edge"
    REMOVE_EDGE = "remove_edge"
    UPDATE_ATTRIBUTE = "update_attribute"
    RECONFIGURE = "reconfigure"


class RepairAction(BaseModel):
    """A single atomic repair action."""

    action_id: UUID = Field(default_factory=uuid4)
    action_type: RepairActionType
    target_entity: UUID
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    risk: float = Field(ge=0.0, le=1.0, default=0.5)


class RepairTrajectory(BaseModel):
    """A complete repair plan: ordered sequence of actions."""

    trajectory_id: UUID = Field(default_factory=uuid4)
    violation_ids: list[UUID] = Field(default_factory=list)
    actions: list[RepairAction] = Field(default_factory=list)
    strategy: str = ""
    score: float = 0.0
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    risk: float = Field(ge=0.0, le=1.0, default=1.0)
    estimated_impact: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphDeltaGen:
    """Generates GraphDelta from a RepairTrajectory."""

    def to_delta(self, trajectory: RepairTrajectory, source: str = "repair_planner") -> GraphDelta:
        ops: list[DeltaOp] = []
        for action in trajectory.actions:
            op = self._action_to_op(action)
            if op is not None:
                ops.append(op)

        return GraphDelta(
            sequence_number=0,
            source=source,
            operations=ops,
        )

    def _action_to_op(self, action: RepairAction) -> Optional[DeltaOp]:
        if action.action_type == RepairActionType.ADD_NODE:
            return AddNode(
                node_id=action.target_entity,
                node_type=action.parameters.get("node_type", "unknown"),
                attributes=action.parameters.get("attributes", {}),
            )
        elif action.action_type == RepairActionType.REMOVE_NODE:
            return RemoveNode(node_id=action.target_entity)
        elif action.action_type == RepairActionType.ADD_EDGE:
            return AddEdge(
                src_id=action.target_entity,
                tgt_id=action.parameters.get("target_id", action.target_entity),
                edge_type=action.parameters.get("edge_type", "depends_on"),
            )
        elif action.action_type == RepairActionType.REMOVE_EDGE:
            return RemoveEdge(edge_id=action.target_entity)
        elif action.action_type == RepairActionType.UPDATE_ATTRIBUTE:
            return UpdateAttribute(
                entity_id=action.target_entity,
                key=action.parameters.get("key", ""),
                old_value=action.parameters.get("old_value"),
                new_value=action.parameters.get("new_value"),
            )
        return None


# ── Strategy implementations ────────────────────────────────────────────


class _BaseStrategy:
    """Base class for repair generation strategies."""

    STRATEGY_NAME: str = "base"
    MAX_CANDIDATES: int = 20

    def generate(
        self, violations: list[DerivedFact], context: dict[str, Any]
    ) -> list[RepairTrajectory]:
        raise NotImplementedError


class TemplateStrategy(_BaseStrategy):
    """Strategy 1: Match known RepairTemplates from memory."""

    STRATEGY_NAME = "template"

    def generate(
        self, violations: list[DerivedFact], context: dict[str, Any]
    ) -> list[RepairTrajectory]:
        templates = context.get("repair_templates", [])
        candidates: list[RepairTrajectory] = []

        for template in templates[:self.MAX_CANDIDATES]:
            # Match template to violations
            matched_violations = []
            for v in violations:
                rule_id = v.payload.get("rule_id", "")
                pattern = getattr(template, "violation_pattern", "")
                if pattern and pattern in rule_id:
                    matched_violations.append(v.derived_id)

            if matched_violations:
                actions = []
                for step in getattr(template, "repair_steps", []):
                    actions.append(
                        RepairAction(
                            action_type=RepairActionType(step.get("action_type", "reconfigure")),
                            target_entity=uuid4(),
                            parameters=step.get("parameters", {}),
                            description=step.get("description", "template-based repair"),
                            confidence=0.7,
                            risk=0.3,
                        )
                    )
                if actions:
                    candidates.append(
                        RepairTrajectory(
                            violation_ids=matched_violations,
                            actions=actions,
                            strategy=self.STRATEGY_NAME,
                            confidence=0.7,
                            risk=0.3,
                        )
                    )

        return candidates[:self.MAX_CANDIDATES]


class InverseStrategy(_BaseStrategy):
    """Strategy 2: Generate inverse operations for violation-causing deltas."""

    STRATEGY_NAME = "inverse"

    def generate(
        self, violations: list[DerivedFact], context: dict[str, Any]
    ) -> list[RepairTrajectory]:
        candidates: list[RepairTrajectory] = []

        for v in violations:
            trigger_ops = v.payload.get("trigger_operations", [])
            entity_id = v.payload.get("entity_id")

            if not entity_id:
                continue

            if isinstance(entity_id, str):
                entity_id = UUID(entity_id)

            # Generate inverse action
            actions: list[RepairAction] = []
            rule_id = v.payload.get("rule_id", "")

            if "add_" in str(trigger_ops):
                actions.append(
                    RepairAction(
                        action_type=RepairActionType.REMOVE_NODE,
                        target_entity=entity_id,
                        description=f"Remove entity that caused violation {rule_id}",
                        confidence=0.5,
                        risk=0.6,
                    )
                )
            elif "remove_" in str(trigger_ops):
                actions.append(
                    RepairAction(
                        action_type=RepairActionType.ADD_NODE,
                        target_entity=entity_id,
                        parameters={"node_type": v.payload.get("node_type", "unknown")},
                        description=f"Re-add removed entity for {rule_id}",
                        confidence=0.5,
                        risk=0.5,
                    )
                )
            else:
                # Default: attribute update
                actions.append(
                    RepairAction(
                        action_type=RepairActionType.UPDATE_ATTRIBUTE,
                        target_entity=entity_id,
                        parameters={
                            "key": "status",
                            "old_value": "broken",
                            "new_value": "fixed",
                        },
                        description=f"Fix attributes for {rule_id}",
                        confidence=0.4,
                        risk=0.4,
                    )
                )

            if actions:
                candidates.append(
                    RepairTrajectory(
                        violation_ids=[v.derived_id],
                        actions=actions,
                        strategy=self.STRATEGY_NAME,
                        confidence=0.5,
                        risk=0.5,
                    )
                )

            if len(candidates) >= self.MAX_CANDIDATES:
                break

        return candidates


class DependencyStrategy(_BaseStrategy):
    """Strategy 3: Fix dependency violations by adding/removing edges."""

    STRATEGY_NAME = "dependency"

    def generate(
        self, violations: list[DerivedFact], context: dict[str, Any]
    ) -> list[RepairTrajectory]:
        candidates: list[RepairTrajectory] = []
        dep_violations = [
            v for v in violations if "dep" in v.payload.get("rule_id", "").lower()
            or "circular" in v.payload.get("rule_id", "").lower()
            or "orphan" in v.payload.get("rule_id", "").lower()
        ]

        for v in dep_violations:
            entity_id = v.payload.get("entity_id")
            if not entity_id:
                continue
            if isinstance(entity_id, str):
                entity_id = UUID(entity_id)

            rule_id = v.payload.get("rule_id", "")
            actions = []

            if "circular" in rule_id.lower():
                # Remove one edge to break cycle
                edge_id = v.payload.get("edge_id")
                if edge_id:
                    if isinstance(edge_id, str):
                        edge_id = UUID(edge_id)
                    actions.append(
                        RepairAction(
                            action_type=RepairActionType.REMOVE_EDGE,
                            target_entity=edge_id,
                            description="Remove edge to break circular dependency",
                            confidence=0.7,
                            risk=0.4,
                        )
                    )
            elif "orphan" in rule_id.lower():
                # Add edge to connect orphan
                actions.append(
                    RepairAction(
                        action_type=RepairActionType.ADD_EDGE,
                        target_entity=entity_id,
                        parameters={
                            "target_id": str(context.get("root_component_id", uuid4())),
                            "edge_type": "depends_on",
                        },
                        description="Connect orphan component",
                        confidence=0.6,
                        risk=0.3,
                    )
                )
            elif "missing" in rule_id.lower() or "dep" in rule_id.lower():
                # Add missing dependency
                actions.append(
                    RepairAction(
                        action_type=RepairActionType.ADD_EDGE,
                        target_entity=entity_id,
                        parameters={
                            "target_id": str(v.payload.get("missing_target", uuid4())),
                            "edge_type": "depends_on",
                        },
                        description="Add missing dependency edge",
                        confidence=0.6,
                        risk=0.3,
                    )
                )

            if actions:
                candidates.append(
                    RepairTrajectory(
                        violation_ids=[v.derived_id],
                        actions=actions,
                        strategy=self.STRATEGY_NAME,
                        confidence=0.6,
                        risk=0.4,
                    )
                )

            if len(candidates) >= self.MAX_CANDIDATES:
                break

        return candidates


class ConfigStrategy(_BaseStrategy):
    """Strategy 4: Generate configuration parameter changes."""

    STRATEGY_NAME = "config"

    def generate(
        self, violations: list[DerivedFact], context: dict[str, Any]
    ) -> list[RepairTrajectory]:
        candidates: list[RepairTrajectory] = []
        config_violations = [
            v for v in violations if "config" in v.payload.get("rule_id", "").lower()
            or "threshold" in v.payload.get("rule_id", "").lower()
            or "param" in v.payload.get("rule_id", "").lower()
        ]

        for v in config_violations:
            entity_id = v.payload.get("entity_id")
            if not entity_id:
                continue
            if isinstance(entity_id, str):
                entity_id = UUID(entity_id)

            actions = [
                RepairAction(
                    action_type=RepairActionType.RECONFIGURE,
                    target_entity=entity_id,
                    parameters=v.payload.get("suggested_config", {"key": "value"}),
                    description=f"Reconfigure {v.payload.get('rule_id', '')}",
                    confidence=0.6,
                    risk=0.2,
                )
            ]
            candidates.append(
                RepairTrajectory(
                    violation_ids=[v.derived_id],
                    actions=actions,
                    strategy=self.STRATEGY_NAME,
                    confidence=0.6,
                    risk=0.2,
                )
            )
            if len(candidates) >= self.MAX_CANDIDATES:
                break

        return candidates


class CompositeStrategy(_BaseStrategy):
    """Strategy 5: Combine multiple atomic repairs for correlated violations."""

    STRATEGY_NAME = "composite"

    def generate(
        self, violations: list[DerivedFact], context: dict[str, Any]
    ) -> list[RepairTrajectory]:
        if len(violations) < 2:
            return []

        candidates: list[RepairTrajectory] = []

        # Group violations by rule_id prefix
        groups: dict[str, list[DerivedFact]] = {}
        for v in violations:
            rule_id = v.payload.get("rule_id", "unknown")
            prefix = rule_id.split("-")[0] if "-" in rule_id else rule_id
            groups.setdefault(prefix, []).append(v)

        for prefix, group in groups.items():
            if len(group) < 2:
                continue

            actions: list[RepairAction] = []
            violation_ids: list[UUID] = []
            for v in group[:5]:  # Max 5 violations per composite
                violation_ids.append(v.derived_id)
                entity_id = v.payload.get("entity_id")
                if entity_id:
                    if isinstance(entity_id, str):
                        entity_id = UUID(entity_id)
                    actions.append(
                        RepairAction(
                            action_type=RepairActionType.UPDATE_ATTRIBUTE,
                            target_entity=entity_id,
                            parameters={"key": "status", "old_value": "violated", "new_value": "repaired"},
                            description=f"Composite fix for {prefix} group",
                            confidence=0.5,
                            risk=0.5,
                        )
                    )

            if actions:
                candidates.append(
                    RepairTrajectory(
                        violation_ids=violation_ids,
                        actions=actions,
                        strategy=self.STRATEGY_NAME,
                        confidence=0.5,
                        risk=0.5,
                    )
                )

            if len(candidates) >= self.MAX_CANDIDATES:
                break

        return candidates


# ── Main RepairPlanner ──────────────────────────────────────────────────


class RepairPlanner:
    """Generates repair candidates using 5 strategies, then scores and ranks.

    Safeguards: per-strategy cap = 20, total candidates before scoring = max 100.
    """

    MAX_TOTAL_CANDIDATES = 100

    def __init__(self, strategies: list[_BaseStrategy] | None = None) -> None:
        self._strategies = strategies or [
            TemplateStrategy(),
            InverseStrategy(),
            DependencyStrategy(),
            ConfigStrategy(),
            CompositeStrategy(),
        ]

    def generate_candidates(
        self,
        violations: list[DerivedFact],
        context: dict[str, Any] | None = None,
    ) -> list[RepairTrajectory]:
        """Generate repair candidates across all strategies.

        Returns unsorted candidates, capped at MAX_TOTAL_CANDIDATES.
        """
        context = context or {}
        all_candidates: list[RepairTrajectory] = []

        for strategy in self._strategies:
            try:
                candidates = strategy.generate(violations, context)
                logger.debug(
                    "repair_strategy_generated",
                    strategy=strategy.STRATEGY_NAME,
                    count=len(candidates),
                )
                all_candidates.extend(candidates)
            except Exception as exc:
                logger.warning(
                    "repair_strategy_failed",
                    strategy=strategy.STRATEGY_NAME,
                    error=str(exc),
                )

        # Apply total cap
        all_candidates = all_candidates[: self.MAX_TOTAL_CANDIDATES]

        logger.debug("repair_candidates_generated", total=len(all_candidates))
        return all_candidates

    def score_candidates(
        self, candidates: list[RepairTrajectory]
    ) -> list[RepairTrajectory]:
        """Score and sort candidates by J() (handled by RepairScorer).

        This is a convenience that applies a basic score if no external scorer.
        """
        for c in candidates:
            # Basic score: confidence - risk + action_count_penalty
            action_penalty = min(0.3, len(c.actions) * 0.05)
            c.score = c.confidence - (c.risk * 0.5) - action_penalty

        return sorted(candidates, key=lambda c: c.score, reverse=True)
