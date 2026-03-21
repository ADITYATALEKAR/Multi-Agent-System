"""Delta Replay Engine — replays graph deltas under counterfactual interventions.

Supports REMOVE_DELTA, MODIFY_DELTA, and INJECT_DELTA intervention types,
filtering replayed operations to only those touching nodes within a supplied
simulation boundary.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from src.core.counterfactual import Intervention, InterventionType
from src.core.fact import GraphDelta

log = structlog.get_logger(__name__)


def _extract_node_ids_from_op(op) -> set[UUID]:  # noqa: ANN001
    """Return every node UUID referenced by a single DeltaOp."""
    ids: set[UUID] = set()
    # Each DeltaOp variant stores node refs in different fields.
    if hasattr(op, "node_id"):
        ids.add(op.node_id)
    if hasattr(op, "src_id"):
        ids.add(op.src_id)
    if hasattr(op, "tgt_id"):
        ids.add(op.tgt_id)
    if hasattr(op, "entity_id"):
        ids.add(op.entity_id)
    if hasattr(op, "participants"):
        ids.update(op.participants)
    return ids


class DeltaReplayEngine:
    """Replays a delta stream with an intervention applied, scoped to a boundary."""

    def __init__(self) -> None:
        pass

    # ── public API ───────────────────────────────────────────────────────

    def replay(
        self,
        deltas: list[GraphDelta],
        intervention: Intervention,
        boundary: set[UUID],
    ) -> list[GraphDelta]:
        """Apply *intervention* to *deltas* and return the modified stream.

        Only deltas whose operations touch nodes inside *boundary* are
        included in the output.

        Parameters
        ----------
        deltas:
            The original ordered delta log.
        intervention:
            The counterfactual intervention to apply.
        boundary:
            Set of node UUIDs that define the simulation boundary.

        Returns
        -------
        list[GraphDelta]
            The modified (replayed) delta stream.
        """
        target_set: set[UUID] = set(intervention.target_deltas)
        result: list[GraphDelta] = []

        for delta in deltas:
            if intervention.intervention_type == InterventionType.REMOVE_DELTA:
                # Skip deltas whose operations reference any target_delta UUID.
                # We treat target_deltas as delta_ids to remove.
                if delta.delta_id in target_set:
                    log.debug(
                        "replay.remove_delta",
                        delta_id=str(delta.delta_id),
                    )
                    continue
                # Also skip if any operation references a target UUID
                # (e.g. the delta was generated *by* the target).
                op_ids = set()
                for op in delta.operations:
                    op_ids.update(_extract_node_ids_from_op(op))
                if op_ids & target_set:
                    log.debug(
                        "replay.remove_delta_by_op_ref",
                        delta_id=str(delta.delta_id),
                    )
                    continue

            elif intervention.intervention_type == InterventionType.MODIFY_DELTA:
                if delta.delta_id in target_set and intervention.replacement:
                    delta = delta.model_copy(
                        update={"operations": list(intervention.replacement)}
                    )
                    log.debug(
                        "replay.modify_delta",
                        delta_id=str(delta.delta_id),
                    )

            elif intervention.intervention_type == InterventionType.INJECT_DELTA:
                # Injected deltas are appended after the stream is built;
                # see below.
                pass

            # Boundary filter: keep only deltas touching the boundary.
            if self._touches_boundary(delta, boundary):
                result.append(delta)

        # INJECT_DELTA: build synthetic deltas from replacement ops and
        # append them at the end of the stream.
        if (
            intervention.intervention_type == InterventionType.INJECT_DELTA
            and intervention.replacement
        ):
            injected = self._build_injected_delta(deltas, intervention)
            if self._touches_boundary(injected, boundary):
                result.append(injected)
            log.debug(
                "replay.inject_delta",
                injected_delta_id=str(injected.delta_id),
            )

        log.info(
            "replay.complete",
            original_count=len(deltas),
            replayed_count=len(result),
        )
        return result

    def compute_violation_diff(
        self,
        original_violations: set[UUID],
        replayed_violations: set[UUID],
    ) -> tuple[set[UUID], set[UUID]]:
        """Return *(violations_removed, violations_added)*.

        Parameters
        ----------
        original_violations:
            Violation IDs observed in the real (non-counterfactual) world.
        replayed_violations:
            Violation IDs observed after replaying the modified delta stream.
        """
        removed = original_violations - replayed_violations
        added = replayed_violations - original_violations
        log.debug(
            "replay.violation_diff",
            removed=len(removed),
            added=len(added),
        )
        return removed, added

    # ── private helpers ──────────────────────────────────────────────────

    @staticmethod
    def _touches_boundary(delta: GraphDelta, boundary: set[UUID]) -> bool:
        """Return ``True`` if any operation in *delta* references a boundary node."""
        if not boundary:
            return True  # empty boundary ⇒ no filtering
        for op in delta.operations:
            if _extract_node_ids_from_op(op) & boundary:
                return True
        return False

    @staticmethod
    def _build_injected_delta(
        existing_deltas: list[GraphDelta],
        intervention: Intervention,
    ) -> GraphDelta:
        """Create a synthetic delta from *intervention.replacement*."""
        from datetime import datetime, timezone

        next_seq = (
            max((d.sequence_number for d in existing_deltas), default=-1) + 1
        )
        return GraphDelta(
            sequence_number=next_seq,
            timestamp=datetime.now(tz=timezone.utc),
            source="counterfactual_injection",
            operations=list(intervention.replacement or []),
        )
