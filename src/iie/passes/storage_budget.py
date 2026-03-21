"""IIE Pass 12: StorageBudgetPass — verify memory/storage within limits."""

from __future__ import annotations

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)

# Defaults in bytes
_DEFAULT_MAX_MEMORY_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
_DEFAULT_MAX_STORAGE_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB
_DEFAULT_MAX_COMPONENT_MEMORY_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB per component


class StorageBudgetPass(BasePass):
    """Verifies that memory and storage usage are within configured limits.

    Checks performed:
    - Total memory across all components does not exceed global limit
    - Total storage across all components does not exceed global limit
    - Individual components do not exceed per-component memory limit
    - Components with storage requirements have budget annotations
    - Negative or zero budget values
    """

    PASS_ID: int = 12
    PASS_NAME: str = "storage_budget"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []

        max_memory = ir.properties.get("max_memory_bytes", _DEFAULT_MAX_MEMORY_BYTES)
        max_storage = ir.properties.get("max_storage_bytes", _DEFAULT_MAX_STORAGE_BYTES)
        max_comp_memory = ir.properties.get(
            "max_component_memory_bytes", _DEFAULT_MAX_COMPONENT_MEMORY_BYTES
        )

        total_memory = 0
        total_storage = 0

        for cid, comp in ir.components.items():
            mem_bytes = comp.properties.get("memory_bytes")
            store_bytes = comp.properties.get("storage_bytes")
            uses_storage = comp.component_type in ("database", "cache", "storage", "memo_cache")

            # Storage-type component without budget annotation
            if uses_storage and store_bytes is None:
                violations.append(
                    self._violation(
                        severity="info",
                        message=(
                            f"Storage component '{comp.name}' ({cid}) of type "
                            f"'{comp.component_type}' has no 'storage_bytes' annotation"
                        ),
                        component_id=cid,
                    )
                )

            # Memory budget checks
            if mem_bytes is not None:
                if mem_bytes <= 0:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Component '{comp.name}' ({cid}) has non-positive memory budget: "
                                f"{mem_bytes} bytes"
                            ),
                            component_id=cid,
                            memory_bytes=mem_bytes,
                        )
                    )
                else:
                    total_memory += mem_bytes
                    if mem_bytes > max_comp_memory:
                        violations.append(
                            self._violation(
                                severity="warning",
                                message=(
                                    f"Component '{comp.name}' ({cid}) memory {mem_bytes} bytes "
                                    f"exceeds per-component limit of {max_comp_memory} bytes"
                                ),
                                component_id=cid,
                                memory_bytes=mem_bytes,
                                limit_bytes=max_comp_memory,
                            )
                        )

            # Storage budget checks
            if store_bytes is not None:
                if store_bytes <= 0:
                    violations.append(
                        self._violation(
                            severity="warning",
                            message=(
                                f"Component '{comp.name}' ({cid}) has non-positive storage budget: "
                                f"{store_bytes} bytes"
                            ),
                            component_id=cid,
                            storage_bytes=store_bytes,
                        )
                    )
                else:
                    total_storage += store_bytes

        # Global memory check
        if total_memory > max_memory:
            violations.append(
                self._violation(
                    severity="critical",
                    message=(
                        f"Total memory usage {total_memory} bytes exceeds global limit "
                        f"of {max_memory} bytes"
                    ),
                    total_memory_bytes=total_memory,
                    limit_bytes=max_memory,
                )
            )

        # Global storage check
        if total_storage > max_storage:
            violations.append(
                self._violation(
                    severity="critical",
                    message=(
                        f"Total storage usage {total_storage} bytes exceeds global limit "
                        f"of {max_storage} bytes"
                    ),
                    total_storage_bytes=total_storage,
                    limit_bytes=max_storage,
                )
            )

        log.info(
            "storage_budget_pass.complete",
            violations=len(violations),
            total_memory=total_memory,
            total_storage=total_storage,
        )
        return violations
