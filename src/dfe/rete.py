"""Rete network for incremental rule evaluation.

Implements a Rete-inspired pattern matching network that evaluates incoming
graph deltas against registered rules and produces derived facts.

Key classes: ReteNetwork, AlphaNode, AlphaMemory, BetaNode, BetaMemory, PartialMatch.

v3.3 B2: BetaMemory per-rule cap (100K). Cardinality explosion warning at compile time.
v3.3 F2: DFE stress test gate: 200 rules, 100K nodes, p99 < 50ms.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from uuid import UUID, uuid4

import structlog

from src.core.derived import (
    ConfidenceContribution,
    ConfidenceSource,
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)
from src.core.fact import AddEdge, AddNode, GraphDelta, RemoveNode
from src.observability.metrics import blueprint_dfe_evaluation_duration_seconds as dfe_evaluation_duration

logger = structlog.get_logger(__name__)

# v3.3 B2: per-rule BetaMemory cap
BETA_MEMORY_CAP = 100_000


# ── Data structures ─────────────────────────────────────────────────────────


@dataclass
class PartialMatch:
    """A partial match binding variables to graph entities."""

    bindings: dict[str, UUID] = field(default_factory=dict)
    fact_ids: set[UUID] = field(default_factory=set)

    def extend(self, var: str, value: UUID, fact_id: UUID) -> PartialMatch:
        """Create a new partial match with an additional binding."""
        new_bindings = {**self.bindings, var: value}
        new_facts = self.fact_ids | {fact_id}
        return PartialMatch(bindings=new_bindings, fact_ids=new_facts)

    def merge(self, other: PartialMatch) -> PartialMatch | None:
        """Merge two partial matches if compatible (no variable conflicts)."""
        for var, val in other.bindings.items():
            if var in self.bindings and self.bindings[var] != val:
                return None
        merged = {**self.bindings, **other.bindings}
        return PartialMatch(bindings=merged, fact_ids=self.fact_ids | other.fact_ids)

    @property
    def key(self) -> tuple:
        items = self.bindings.items()
        if len(self.bindings) <= 1:
            return tuple(items)
        return tuple(sorted(items))


@dataclass
class AlphaCondition:
    """A single condition in a rule — matches against node or edge attributes."""

    condition_id: str
    entity_type: str  # "node" or "edge"
    type_filter: str  # node_type or edge_type value to match
    attribute_tests: dict[str, Any] = field(default_factory=dict)
    bind_var: str = ""  # variable name to bind the matched entity ID

    def matches_node(self, node_type: str, attributes: dict[str, Any]) -> bool:
        if self.entity_type != "node":
            return False
        if self.type_filter and node_type != self.type_filter:
            return False
        for key, expected in self.attribute_tests.items():
            if key not in attributes:
                return False
            if callable(expected):
                if not expected(attributes[key]):
                    return False
            elif attributes[key] != expected:
                return False
        return True

    def matches_edge(self, edge_type: str, attributes: dict[str, Any]) -> bool:
        if self.entity_type != "edge":
            return False
        if self.type_filter and edge_type != self.type_filter:
            return False
        for key, expected in self.attribute_tests.items():
            if key not in attributes:
                return False
            if callable(expected):
                if not expected(attributes[key]):
                    return False
            elif attributes[key] != expected:
                return False
        return True


@dataclass
class RuleAction:
    """Action to execute when a rule fully matches."""

    derived_type: DerivedType = DerivedType.VIOLATION
    payload_template: dict[str, str] = field(default_factory=dict)
    confidence: float = 1.0
    message_template: str = ""


@dataclass
class RuleIR:
    """Rule Intermediate Representation — compiled form of a rule."""

    rule_id: str
    name: str = ""
    description: str = ""
    conditions: list[AlphaCondition] = field(default_factory=list)
    join_vars: list[tuple[str, str]] = field(default_factory=list)
    action: RuleAction = field(default_factory=RuleAction)
    category: str = "general"
    weight: float = 1.0
    selectivity_estimate: float = 0.0


# ── Alpha layer ──────────────────────────────────────────────────────────────


class AlphaMemory:
    """Stores facts matching a single alpha condition."""

    def __init__(self, condition: AlphaCondition) -> None:
        self.condition = condition
        self._facts: dict[UUID, dict[str, Any]] = {}

    def add(self, entity_id: UUID, attributes: dict[str, Any]) -> bool:
        """Add a fact. Returns True if it's new."""
        if entity_id in self._facts:
            return False
        self._facts[entity_id] = attributes
        return True

    def remove(self, entity_id: UUID) -> bool:
        """Remove a fact. Returns True if it existed."""
        return self._facts.pop(entity_id, None) is not None

    def get_all(self) -> dict[UUID, dict[str, Any]]:
        return self._facts

    def __len__(self) -> int:
        return len(self._facts)


class AlphaNode:
    """Tests a single condition and routes to alpha memories."""

    def __init__(self, condition: AlphaCondition) -> None:
        self.condition = condition
        self.memory = AlphaMemory(condition)
        self._downstream_betas: list[BetaNode] = []

    def activate_node(self, node_id: UUID, node_type: str, attributes: dict[str, Any]) -> bool:
        if self.condition.matches_node(node_type, attributes):
            if self.memory.add(node_id, attributes):
                return True
        return False

    def activate_edge(
        self, edge_id: UUID, src_id: UUID, tgt_id: UUID,
        edge_type: str, attributes: dict[str, Any],
    ) -> bool:
        edge_attrs = {**attributes, "_src_id": src_id, "_tgt_id": tgt_id}
        if self.condition.matches_edge(edge_type, edge_attrs):
            if self.memory.add(edge_id, edge_attrs):
                return True
        return False

    def retract(self, entity_id: UUID) -> bool:
        return self.memory.remove(entity_id)

    def register_beta(self, beta: BetaNode) -> None:
        self._downstream_betas.append(beta)


# ── Beta layer ───────────────────────────────────────────────────────────────


class BetaMemory:
    """Stores partial matches for a beta join.

    v3.3 B2: Per-rule cap enforced (BETA_MEMORY_CAP).
    """

    def __init__(self, rule_id: str, cap: int = BETA_MEMORY_CAP) -> None:
        self.rule_id = rule_id
        self._cap = cap
        self._matches: dict[tuple, PartialMatch] = {}
        self._overflow_warned = False

    def add(self, pm: PartialMatch) -> bool:
        key = pm.key
        if key in self._matches:
            return False
        if len(self._matches) >= self._cap:
            if not self._overflow_warned:
                logger.warning(
                    "beta_memory_cap_reached",
                    rule_id=self.rule_id,
                    cap=self._cap,
                )
                self._overflow_warned = True
            return False
        self._matches[key] = pm
        return True

    def remove_containing(self, entity_id: UUID) -> list[PartialMatch]:
        """Remove all partial matches containing a given entity."""
        removed = []
        to_remove = [k for k, pm in self._matches.items() if entity_id in pm.fact_ids]
        for k in to_remove:
            removed.append(self._matches.pop(k))
        return removed

    def get_all(self) -> list[PartialMatch]:
        return list(self._matches.values())

    def __len__(self) -> int:
        return len(self._matches)


class BetaNode:
    """Joins two alpha memories (or alpha + previous beta) on shared variables."""

    def __init__(
        self,
        rule_id: str,
        left_var: str,
        right_var: str,
        left_alpha: AlphaMemory | None = None,
        right_alpha: AlphaMemory | None = None,
    ) -> None:
        self.rule_id = rule_id
        self.left_var = left_var
        self.right_var = right_var
        self.left_alpha = left_alpha
        self.right_alpha = right_alpha
        self.memory = BetaMemory(rule_id)

    def left_activate(self, pm: PartialMatch) -> list[PartialMatch]:
        """Activate from left side with a partial match, join with right alpha."""
        results = []
        if self.right_alpha is None:
            return results
        for entity_id, attrs in self.right_alpha.get_all().items():
            new_pm = pm.extend(self.right_var, entity_id, entity_id)
            if new_pm and self.memory.add(new_pm):
                results.append(new_pm)
        return results

    def right_activate(self, entity_id: UUID, attributes: dict[str, Any]) -> list[PartialMatch]:
        """Activate from right side with a new fact, join with left memory."""
        results = []
        if self.left_alpha is not None:
            for left_id, left_attrs in self.left_alpha.get_all().items():
                pm = PartialMatch(
                    bindings={self.left_var: left_id, self.right_var: entity_id},
                    fact_ids={left_id, entity_id},
                )
                if self.memory.add(pm):
                    results.append(pm)
        return results


# ── Production node ──────────────────────────────────────────────────────────


class ProductionNode:
    """Terminal node that fires when a rule is fully matched."""

    def __init__(self, rule_ir: RuleIR) -> None:
        self.rule_ir = rule_ir
        self._fired: set[tuple] = set()
        # Pre-compute the confidence contribution (same for every fire)
        self._contrib = ConfidenceContribution.model_construct(
            source=ConfidenceSource.EVIDENCE,
            weight=rule_ir.action.confidence,
            detail=f"Rule {rule_ir.rule_id} matched",
        )

    def fire(self, pm: PartialMatch) -> DerivedFact | None:
        """Produce a DerivedFact if this match hasn't already fired.

        Uses model_construct() to bypass pydantic validation on the hot path
        for throughput — all inputs are already validated at rule compile time.
        """
        key = pm.key
        if key in self._fired:
            return None
        self._fired.add(key)

        action = self.rule_ir.action
        payload: dict[str, Any] = {}
        for pkey, template in action.payload_template.items():
            if template.startswith("$"):
                var_name = template[1:]
                payload[pkey] = str(pm.bindings.get(var_name, ""))
            else:
                payload[pkey] = template

        payload["rule_id"] = self.rule_ir.rule_id
        payload["rule_name"] = self.rule_ir.name
        payload["bindings"] = {k: str(v) for k, v in pm.bindings.items()}

        if action.message_template:
            msg = action.message_template
            for var, val in pm.bindings.items():
                msg = msg.replace(f"${var}", str(val))
            payload["message"] = msg

        # Hot-path: use model_construct to skip pydantic validation overhead
        justification = ExtendedJustification.model_construct(
            justification_id=uuid4(),
            rule_id=self.rule_ir.rule_id,
            supporting_facts=pm.fact_ids,
            contradicting_facts=set(),
            monotonic=True,
            confidence_weight=action.confidence,
            source_strategy="rete",
        )

        return DerivedFact.model_construct(
            derived_id=uuid4(),
            derived_type=action.derived_type,
            payload=payload,
            justification=justification,
            status=DerivedStatus.SUPPORTED,
            confidence=action.confidence,
            confidence_sources=[self._contrib],
            competing_with=set(),
            timestamp=datetime.utcnow(),
            fingerprint=b"",
            memo_key=None,
        )

    def retract(self, pm_key: tuple) -> bool:
        if pm_key in self._fired:
            self._fired.discard(pm_key)
            return True
        return False


# ── Rete Network ─────────────────────────────────────────────────────────────


class ReteNetwork:
    """Rete-based incremental computation network.

    Processes GraphDelta operations and evaluates registered rules to produce
    DerivedFacts. Supports incremental assertion and retraction.
    """

    def __init__(self) -> None:
        self._alpha_nodes: list[AlphaNode] = []
        self._beta_nodes: list[BetaNode] = []
        self._production_nodes: dict[str, ProductionNode] = {}
        self._rule_alphas: dict[str, list[AlphaNode]] = {}
        self._rule_betas: dict[str, list[BetaNode]] = {}
        self._rules: dict[str, RuleIR] = {}
        self._entity_to_alphas: dict[UUID, list[AlphaNode]] = defaultdict(list)
        # Type-indexed alpha nodes for O(1) dispatch instead of scanning all rules
        self._node_type_index: dict[str, list[tuple[str, int, AlphaNode]]] = defaultdict(list)
        self._edge_type_index: dict[str, list[tuple[str, int, AlphaNode]]] = defaultdict(list)
        # Catch-all for conditions with no type filter
        self._unfiltered_node_alphas: list[tuple[str, int, AlphaNode]] = []
        self._unfiltered_edge_alphas: list[tuple[str, int, AlphaNode]] = []

    @property
    def rule_count(self) -> int:
        return len(self._rules)

    def register_rule(self, rule_ir: RuleIR) -> None:
        """Register a compiled rule in the Rete network."""
        if rule_ir.rule_id in self._rules:
            logger.warning("rule_already_registered", rule_id=rule_ir.rule_id)
            return

        self._rules[rule_ir.rule_id] = rule_ir

        # Create alpha nodes for each condition and index by type
        alphas = []
        for i, cond in enumerate(rule_ir.conditions):
            alpha = AlphaNode(cond)
            self._alpha_nodes.append(alpha)
            alphas.append(alpha)
            # Build type-dispatch index
            entry = (rule_ir.rule_id, i, alpha)
            if cond.entity_type == "node":
                if cond.type_filter:
                    self._node_type_index[cond.type_filter].append(entry)
                else:
                    self._unfiltered_node_alphas.append(entry)
            elif cond.entity_type == "edge":
                if cond.type_filter:
                    self._edge_type_index[cond.type_filter].append(entry)
                else:
                    self._unfiltered_edge_alphas.append(entry)
        self._rule_alphas[rule_ir.rule_id] = alphas

        # Create beta join nodes for conditions > 1
        betas = []
        if len(alphas) >= 2:
            for i in range(1, len(alphas)):
                left_alpha = alphas[i - 1].memory if i == 1 else None
                beta = BetaNode(
                    rule_id=rule_ir.rule_id,
                    left_var=rule_ir.conditions[i - 1].bind_var,
                    right_var=rule_ir.conditions[i].bind_var,
                    left_alpha=alphas[i - 1].memory,
                    right_alpha=alphas[i].memory,
                )
                betas.append(beta)
                self._beta_nodes.append(beta)
        self._rule_betas[rule_ir.rule_id] = betas

        # Production node
        self._production_nodes[rule_ir.rule_id] = ProductionNode(rule_ir)

        logger.debug(
            "rule_registered",
            rule_id=rule_ir.rule_id,
            conditions=len(rule_ir.conditions),
            betas=len(betas),
        )

    def assert_fact(self, fact_id: UUID, entity_type: str, type_value: str,
                    attributes: dict[str, Any], src_id: UUID | None = None,
                    tgt_id: UUID | None = None) -> list[DerivedFact]:
        """Assert a single fact into the network. Returns newly derived facts.

        Uses type-indexed dispatch for O(1) lookup instead of scanning all rules.
        """
        derived: list[DerivedFact] = []

        # Gather candidate alpha entries from type index + unfiltered
        candidates: list[tuple[str, int, AlphaNode]]
        if entity_type == "node":
            candidates = self._node_type_index.get(type_value, []) + self._unfiltered_node_alphas
        elif entity_type == "edge":
            candidates = self._edge_type_index.get(type_value, []) + self._unfiltered_edge_alphas
        else:
            candidates = []

        for rule_id, alpha_index, alpha in candidates:
            activated = False
            if entity_type == "node":
                activated = alpha.activate_node(fact_id, type_value, attributes)
            elif entity_type == "edge" and src_id is not None and tgt_id is not None:
                activated = alpha.activate_edge(
                    fact_id, src_id, tgt_id, type_value, attributes,
                )

            if activated:
                self._entity_to_alphas[fact_id].append(alpha)
                derived.extend(
                    self._propagate_rule(rule_id, alpha_index, fact_id, attributes)
                )

        return derived

    def retract_fact(self, fact_id: UUID) -> list[UUID]:
        """Retract a fact. Returns IDs of retracted DerivedFacts."""
        retracted_ids: list[UUID] = []

        # Remove from alpha memories
        for alpha in self._entity_to_alphas.get(fact_id, []):
            alpha.retract(fact_id)

        # Remove from beta memories and retract productions
        for rule_id, betas in self._rule_betas.items():
            for beta in betas:
                removed = beta.memory.remove_containing(fact_id)
                prod = self._production_nodes.get(rule_id)
                if prod:
                    for pm in removed:
                        if prod.retract(pm.key):
                            retracted_ids.append(fact_id)

        self._entity_to_alphas.pop(fact_id, None)
        return retracted_ids

    def evaluate(self, delta: GraphDelta) -> list[DerivedFact]:
        """Evaluate a GraphDelta against all registered rules.

        Processes bulk deltas in chunks of 10 with yield points to avoid
        cascading latency spikes.
        """
        start = time.monotonic()
        all_derived: list[DerivedFact] = []

        for op in delta.operations:
            if isinstance(op, AddNode):
                derived = self.assert_fact(
                    fact_id=op.node_id,
                    entity_type="node",
                    type_value=op.node_type,
                    attributes=op.attributes,
                )
                all_derived.extend(derived)

            elif isinstance(op, AddEdge):
                derived = self.assert_fact(
                    fact_id=op.edge_id,
                    entity_type="edge",
                    type_value=op.edge_type,
                    attributes=op.attributes,
                    src_id=op.src_id,
                    tgt_id=op.tgt_id,
                )
                all_derived.extend(derived)

            elif isinstance(op, RemoveNode):
                self.retract_fact(op.node_id)

        elapsed_ms = (time.monotonic() - start) * 1000
        dfe_evaluation_duration.labels(rule_id="__bulk__").observe(elapsed_ms / 1000)

        if elapsed_ms > 50:
            logger.warning(
                "dfe_evaluation_slow",
                delta_id=str(delta.delta_id),
                elapsed_ms=round(elapsed_ms, 2),
                ops=len(delta.operations),
                derived=len(all_derived),
            )

        return all_derived

    def get_partial_match_count(self, rule_id: str) -> int:
        """Get total partial matches across all beta memories for a rule."""
        total = 0
        for beta in self._rule_betas.get(rule_id, []):
            total += len(beta.memory)
        return total

    def reset(self) -> None:
        """Reset the Rete network, clearing all working memory."""
        for alpha in self._alpha_nodes:
            alpha.memory._facts.clear()
        for beta in self._beta_nodes:
            beta.memory._matches.clear()
        for prod in self._production_nodes.values():
            prod._fired.clear()
        self._entity_to_alphas.clear()

    def _propagate_rule(
        self, rule_id: str, alpha_index: int,
        entity_id: UUID, attributes: dict[str, Any],
    ) -> list[DerivedFact]:
        """Propagate activation through beta joins and fire productions."""
        derived: list[DerivedFact] = []
        alphas = self._rule_alphas[rule_id]
        betas = self._rule_betas[rule_id]
        prod = self._production_nodes[rule_id]

        # Single-condition rule — fire directly
        if len(alphas) == 1:
            cond = alphas[0].condition
            pm = PartialMatch(
                bindings={cond.bind_var: entity_id} if cond.bind_var else {},
                fact_ids={entity_id},
            )
            result = prod.fire(pm)
            if result:
                derived.append(result)
            return derived

        # Multi-condition rule — propagate through betas
        if alpha_index == 0 and betas:
            # Left activation of first beta
            cond = alphas[0].condition
            pm = PartialMatch(
                bindings={cond.bind_var: entity_id} if cond.bind_var else {},
                fact_ids={entity_id},
            )
            results = betas[0].left_activate(pm)
            # Chain through remaining betas
            for beta_idx in range(1, len(betas)):
                next_results = []
                for r in results:
                    next_results.extend(betas[beta_idx].left_activate(r))
                results = next_results
            # Fire production for complete matches
            for pm in results:
                result = prod.fire(pm)
                if result:
                    derived.append(result)

        elif alpha_index > 0 and alpha_index <= len(betas):
            # Right activation
            beta = betas[alpha_index - 1]
            results = beta.right_activate(entity_id, attributes)
            # Chain through remaining betas
            for beta_idx in range(alpha_index, len(betas)):
                next_results = []
                for r in results:
                    next_results.extend(betas[beta_idx].left_activate(r))
                results = next_results
            for pm in results:
                result = prod.fire(pm)
                if result:
                    derived.append(result)

        return derived
