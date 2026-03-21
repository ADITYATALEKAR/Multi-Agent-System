"""IIE Pass 8: DeltaConsumptionPass — verify all deltas have consumers."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)


class DeltaConsumptionPass(BasePass):
    """Verifies that all graph deltas (change events) have at least one consumer.

    Checks performed:
    - Every component that produces deltas has at least one consumer subscribed
    - Delta channels referenced in connections have matching producers and consumers
    - No orphan delta producers (producing deltas that nobody reads)
    """

    PASS_ID: int = 8
    PASS_NAME: str = "delta_consumption"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []

        # Identify delta producers and consumers from component properties
        delta_producers: dict[str, list[str]] = {}  # channel -> [component_ids]
        delta_consumers: dict[str, list[str]] = {}  # channel -> [component_ids]

        for cid, comp in ir.components.items():
            # Components can declare which delta channels they produce/consume
            produces = comp.properties.get("delta_produces", [])
            consumes = comp.properties.get("delta_consumes", [])

            if isinstance(produces, str):
                produces = [produces]
            if isinstance(consumes, str):
                consumes = [consumes]

            for channel in produces:
                delta_producers.setdefault(channel, []).append(cid)
            for channel in consumes:
                delta_consumers.setdefault(channel, []).append(cid)

        # Also infer from pub/sub connections
        for conn in ir.connections:
            if conn.connection_type == "publishes_to":
                channel = conn.properties.get("delta_channel", conn.target)
                delta_producers.setdefault(channel, []).append(conn.source)
            elif conn.connection_type == "subscribes_to":
                channel = conn.properties.get("delta_channel", conn.source)
                delta_consumers.setdefault(channel, []).append(conn.target)

        # 1. Producers with no consumers
        for channel, producers in delta_producers.items():
            if channel not in delta_consumers:
                for prod_id in producers:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Component '{prod_id}' produces deltas on channel '{channel}' "
                                f"but no component consumes them"
                            ),
                            component_id=prod_id,
                            channel=channel,
                        )
                    )

        # 2. Consumers with no producers
        for channel, consumers in delta_consumers.items():
            if channel not in delta_producers:
                for cons_id in consumers:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Component '{cons_id}' consumes deltas from channel '{channel}' "
                                f"but no component produces them"
                            ),
                            component_id=cons_id,
                            channel=channel,
                        )
                    )

        log.info("delta_consumption_pass.complete", violations=len(violations))
        return violations
