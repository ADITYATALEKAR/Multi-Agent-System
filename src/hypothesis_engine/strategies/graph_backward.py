"""Graph-backward strategy — generates hypotheses via backward graph traversal."""

from __future__ import annotations

from typing import Any

import structlog

from src.core.derived import (
    ConfidenceContribution,
    ConfidenceSource,
    DerivedFact,
    DerivedStatus,
    DerivedType,
    ExtendedJustification,
)
from src.hypothesis_engine.base import HypothesisContext, HypothesisStrategy

log = structlog.get_logger(__name__)

# Edge types that represent backward-traversable dependency relationships.
_DEPENDENCY_EDGE_TYPES: set[str] = {"depends_on", "imports", "calls"}


def _find_upstream_nodes(
    graph: Any,
    entity_id: str,
    edge_types: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Walk one hop backward from *entity_id* in the reasoning graph.

    The reasoning-graph object is duck-typed — we expect it to expose either
    ``get_incoming_edges(entity_id)`` or ``edges`` as an iterable of dicts
    with ``src_id``, ``tgt_id``, ``edge_type`` keys.

    Returns a list of dicts ``{"node_id": ..., "edge_type": ...}``.
    """
    edge_types = edge_types or _DEPENDENCY_EDGE_TYPES
    results: list[dict[str, Any]] = []

    if hasattr(graph, "get_incoming_edges"):
        for edge in graph.get_incoming_edges(entity_id):
            etype = edge.get("edge_type", "")
            if etype in edge_types:
                results.append({"node_id": edge["src_id"], "edge_type": etype})
    elif hasattr(graph, "edges"):
        for edge in graph.edges:
            tgt = str(edge.get("tgt_id", ""))
            if tgt == str(entity_id) and edge.get("edge_type", "") in edge_types:
                results.append(
                    {"node_id": edge["src_id"], "edge_type": edge["edge_type"]}
                )

    return results


class GraphBackwardStrategy(HypothesisStrategy):
    """Derives hypotheses by walking the state graph backward from symptoms.

    For each violation the strategy traces backward through dependency edges
    (depends_on, imports, calls) to find potential root causes upstream.
    """

    STRATEGY_ID: str = "graph_backward"
    PRIORITY: int = 2

    async def generate(
        self,
        violations: list[DerivedFact],
        context: HypothesisContext,
    ) -> list[DerivedFact]:
        hypotheses: list[DerivedFact] = []

        if context.reasoning_graph is None:
            log.debug("graph_backward.skipped", reason="no_graph")
            return hypotheses

        for violation in violations:
            entity_id = violation.payload.get(
                "subject_id", violation.payload.get("entity_id")
            )
            if entity_id is None:
                continue

            upstream = _find_upstream_nodes(context.reasoning_graph, str(entity_id))
            for node in upstream:
                confidence = min(violation.confidence * 0.75, 1.0)
                hypothesis = DerivedFact(
                    derived_type=DerivedType.HYPOTHESIS,
                    payload={
                        "violation_id": str(violation.derived_id),
                        "suspected_entity": str(node["node_id"]),
                        "reasoning": (
                            f"Entity '{node['node_id']}' is upstream of the "
                            f"violating entity via '{node['edge_type']}' edge "
                            f"and may be the root cause."
                        ),
                        "edge_type": node["edge_type"],
                        "strategy": self.STRATEGY_ID,
                    },
                    justification=ExtendedJustification(
                        rule_id=violation.justification.rule_id,
                        supporting_facts={violation.derived_id},
                        source_strategy=self.STRATEGY_ID,
                    ),
                    status=DerivedStatus.UNKNOWN,
                    confidence=confidence,
                    confidence_sources=[
                        ConfidenceContribution(
                            source=ConfidenceSource.EVIDENCE,
                            weight=confidence,
                            detail=(
                                f"Backward traversal via "
                                f"'{node['edge_type']}' edge"
                            ),
                        ),
                    ],
                )
                hypotheses.append(hypothesis)

        log.debug(
            "graph_backward.generated",
            count=len(hypotheses),
            violation_count=len(violations),
        )
        return hypotheses
