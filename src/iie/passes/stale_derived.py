"""IIE Pass 10: StaleDerivedPass — detect stale derived facts."""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)

# Default staleness threshold in seconds
_DEFAULT_STALENESS_THRESHOLD_S = 300  # 5 minutes


class StaleDerivedPass(BasePass):
    """Detects stale derived facts in the architecture.

    Checks performed:
    - Components with derived facts that exceed staleness bounds
    - Derived facts whose source data has been updated more recently
    - Components with staleness_bound_ms contracts that are violated
    """

    PASS_ID: int = 10
    PASS_NAME: str = "stale_derived"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []
        now = datetime.now(timezone.utc)

        for cid, comp in ir.components.items():
            derived_facts = comp.properties.get("derived_facts", [])
            if not isinstance(derived_facts, list):
                continue

            staleness_threshold_s = comp.properties.get(
                "staleness_threshold_s", _DEFAULT_STALENESS_THRESHOLD_S
            )

            for fact in derived_facts:
                if not isinstance(fact, dict):
                    continue

                fact_id = fact.get("derived_id", "unknown")
                timestamp_str = fact.get("timestamp")

                if not timestamp_str:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Derived fact '{fact_id}' in component '{comp.name}' ({cid}) "
                                f"has no timestamp"
                            ),
                            component_id=cid,
                            derived_id=str(fact_id),
                        )
                    )
                    continue

                # Parse timestamp
                try:
                    if isinstance(timestamp_str, datetime):
                        fact_time = timestamp_str
                    else:
                        fact_time = datetime.fromisoformat(str(timestamp_str))
                    # Ensure timezone aware
                    if fact_time.tzinfo is None:
                        fact_time = fact_time.replace(tzinfo=timezone.utc)
                    age_s = (now - fact_time).total_seconds()
                except (ValueError, TypeError):
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Derived fact '{fact_id}' in component '{comp.name}' ({cid}) "
                                f"has unparseable timestamp"
                            ),
                            component_id=cid,
                            derived_id=str(fact_id),
                        )
                    )
                    continue

                if age_s > staleness_threshold_s:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Derived fact '{fact_id}' in component '{comp.name}' ({cid}) "
                                f"is stale: age={age_s:.0f}s exceeds threshold={staleness_threshold_s}s"
                            ),
                            component_id=cid,
                            derived_id=str(fact_id),
                            age_seconds=age_s,
                            threshold_seconds=staleness_threshold_s,
                        )
                    )

                # Check if source data is newer than the derived fact
                source_updated_str = fact.get("source_updated")
                if source_updated_str:
                    try:
                        if isinstance(source_updated_str, datetime):
                            source_time = source_updated_str
                        else:
                            source_time = datetime.fromisoformat(str(source_updated_str))
                        if source_time.tzinfo is None:
                            source_time = source_time.replace(tzinfo=timezone.utc)
                        if source_time > fact_time:
                            violations.append(
                                self._violation(
                                    severity="critical",
                                    message=(
                                        f"Derived fact '{fact_id}' in component '{comp.name}' ({cid}) "
                                        f"is outdated: source updated after derivation"
                                    ),
                                    component_id=cid,
                                    derived_id=str(fact_id),
                                )
                            )
                    except (ValueError, TypeError):
                        pass

        # Check staleness bounds from contracts
        for contract_id, contract_data in ir.contracts.items():
            if isinstance(contract_data, dict):
                staleness_ms = contract_data.get("staleness_bound_ms")
                if staleness_ms is not None and staleness_ms > 0:
                    consumer = contract_data.get("consumer")
                    if consumer:
                        comp = ir.get_component(consumer)
                        if comp:
                            last_refresh_str = comp.properties.get("last_contract_refresh")
                            if last_refresh_str:
                                try:
                                    if isinstance(last_refresh_str, datetime):
                                        last_refresh = last_refresh_str
                                    else:
                                        last_refresh = datetime.fromisoformat(
                                            str(last_refresh_str)
                                        )
                                    if last_refresh.tzinfo is None:
                                        last_refresh = last_refresh.replace(tzinfo=timezone.utc)
                                    age_ms = (now - last_refresh).total_seconds() * 1000
                                    if age_ms > staleness_ms:
                                        violations.append(
                                            self._violation(
                                                severity="warning",
                                                message=(
                                                    f"Contract '{contract_id}' staleness bound "
                                                    f"violated: {age_ms:.0f}ms > {staleness_ms}ms"
                                                ),
                                                component_id=consumer,
                                                contract_id=contract_id,
                                            )
                                        )
                                except (ValueError, TypeError):
                                    pass

        log.info("stale_derived_pass.complete", violations=len(violations))
        return violations
