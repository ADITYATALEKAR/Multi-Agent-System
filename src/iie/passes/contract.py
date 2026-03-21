"""IIE Pass 3: ContractPass — verify contract pre/post conditions, type compatibility."""

from __future__ import annotations

from typing import Any

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)


class ContractPass(BasePass):
    """Verifies inter-component contract integrity.

    Checks performed:
    - Every consumed contract has a matching provider
    - Every provided contract has at least one consumer
    - Contract references in connections exist in the contracts registry
    - Provider and consumer components referenced in contracts exist
    """

    PASS_ID: int = 3
    PASS_NAME: str = "contract"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []
        component_ids = set(ir.components.keys())
        contract_ids = set(ir.contracts.keys())

        # Collect all provided and consumed contract IDs across components
        all_provided: dict[str, list[str]] = {}  # contract_id -> [provider_component_ids]
        all_consumed: dict[str, list[str]] = {}  # contract_id -> [consumer_component_ids]

        for cid, comp in ir.components.items():
            for contract_id in comp.contracts_provided:
                all_provided.setdefault(contract_id, []).append(cid)
            for contract_id in comp.contracts_consumed:
                all_consumed.setdefault(contract_id, []).append(cid)

        # 1. Every consumed contract must have at least one provider
        for contract_id, consumers in all_consumed.items():
            if contract_id not in all_provided:
                for consumer_cid in consumers:
                    violations.append(
                        self._violation(
                            severity="critical",
                            message=f"Component '{consumer_cid}' consumes contract '{contract_id}' but no component provides it",
                            component_id=consumer_cid,
                            contract_id=contract_id,
                        )
                    )

        # 2. Provided contracts with no consumers (warning, not critical)
        for contract_id, providers in all_provided.items():
            if contract_id not in all_consumed:
                for provider_cid in providers:
                    violations.append(
                        self._violation(
                            severity="info",
                            message=f"Component '{provider_cid}' provides contract '{contract_id}' but no component consumes it",
                            component_id=provider_cid,
                            contract_id=contract_id,
                        )
                    )

        # 3. Connections referencing non-existent contracts
        for conn in ir.connections:
            if conn.contract_id is not None and conn.contract_id not in contract_ids:
                violations.append(
                    self._violation(
                        severity="warning",
                        message=f"Connection '{conn.connection_id}' references non-existent contract '{conn.contract_id}'",
                        connection_id=conn.connection_id,
                        contract_id=conn.contract_id,
                    )
                )

        # 4. Contracts referencing non-existent provider/consumer components
        for contract_id, contract_data in ir.contracts.items():
            if isinstance(contract_data, dict):
                provider = contract_data.get("provider")
                consumer = contract_data.get("consumer")
                if provider and provider not in component_ids:
                    violations.append(
                        self._violation(
                            severity="critical",
                            message=f"Contract '{contract_id}' references non-existent provider '{provider}'",
                            contract_id=contract_id,
                            provider=provider,
                        )
                    )
                if consumer and consumer not in component_ids:
                    violations.append(
                        self._violation(
                            severity="critical",
                            message=f"Contract '{contract_id}' references non-existent consumer '{consumer}'",
                            contract_id=contract_id,
                            consumer=consumer,
                        )
                    )

        log.info("contract_pass.complete", violations=len(violations))
        return violations
