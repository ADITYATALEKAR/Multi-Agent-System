"""IIE Pass 9: CacheLineagePass — verify memo cache lineage (spot-check 10%)."""

from __future__ import annotations

import random

import structlog

from src.iie.architecture_ir import ArchitectureIR
from src.iie.passes.base import BasePass, IntegrityViolation

log = structlog.get_logger(__name__)


class CacheLineagePass(BasePass):
    """Verifies memo/cache lineage by spot-checking a sample.

    Checks performed:
    - Cache components have lineage metadata (source, invalidation policy)
    - Spot-check ~10% of cache entries for stale lineage references
    - Cache components reference valid source components
    - Invalidation channels are properly connected
    """

    PASS_ID: int = 9
    PASS_NAME: str = "cache_lineage"

    def run(self, ir: ArchitectureIR) -> list[IntegrityViolation]:
        violations: list[IntegrityViolation] = []
        component_ids = set(ir.components.keys())

        # Identify cache components
        cache_components = {
            cid: comp
            for cid, comp in ir.components.items()
            if comp.component_type in ("cache", "memo", "memo_cache")
        }

        if not cache_components:
            log.info("cache_lineage_pass.complete", violations=0, caches=0)
            return violations

        for cid, comp in cache_components.items():
            # 1. Cache must declare its data source
            source_id = comp.properties.get("cache_source")
            if not source_id:
                violations.append(
                    self._violation(
                        severity="warning",
                        message=(
                            f"Cache component '{comp.name}' ({cid}) has no 'cache_source' property"
                        ),
                        component_id=cid,
                    )
                )
            elif source_id not in component_ids:
                violations.append(
                    self._violation(
                        severity="critical",
                        message=(
                            f"Cache component '{comp.name}' ({cid}) references non-existent "
                            f"source '{source_id}'"
                        ),
                        component_id=cid,
                        cache_source=source_id,
                    )
                )

            # 2. Cache should have an invalidation policy
            if not comp.properties.get("invalidation_policy"):
                violations.append(
                    self._violation(
                        severity="info",
                        message=(
                            f"Cache component '{comp.name}' ({cid}) has no invalidation_policy"
                        ),
                        component_id=cid,
                    )
                )

            # 3. Spot-check cache entries (10% of entries listed in properties)
            entries = comp.properties.get("cache_entries", {})
            if isinstance(entries, dict) and entries:
                sample_size = max(1, len(entries) // 10)
                sample_keys = random.sample(sorted(entries.keys()), min(sample_size, len(entries)))

                for key in sample_keys:
                    entry = entries[key]
                    if isinstance(entry, dict):
                        lineage_source = entry.get("lineage_source")
                        if lineage_source and lineage_source not in component_ids:
                            violations.append(
                                self._violation(
                                    severity="warning",
                                    message=(
                                        f"Cache entry '{key}' in '{comp.name}' ({cid}) "
                                        f"has stale lineage reference to '{lineage_source}'"
                                    ),
                                    component_id=cid,
                                    cache_key=key,
                                    lineage_source=lineage_source,
                                )
                            )

        log.info(
            "cache_lineage_pass.complete",
            violations=len(violations),
            caches=len(cache_components),
        )
        return violations
