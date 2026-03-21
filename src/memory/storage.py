"""Memory storage backends.

Phase 3 — Persistent storage for memory entries.

Provides a MemoryStore ABC and three backends:
    - InMemoryBackend: dict-based, for unit tests and single-process use.
    - PGMemoryBackend: PostgreSQL (stub — requires asyncpg connection).
    - Neo4jMemoryOverlay: Neo4j graph overlay (stub — requires neo4j driver).
    - ObjectStorageBackend: S3/blob storage for large artefacts (stub).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional
from uuid import UUID

import structlog

from src.memory.types import (
    Episode,
    EpisodeOutcome,
    MemoryType,
    Pattern,
    Procedure,
    RepairTemplate,
    SemanticRule,
    WorkingMemory,
)

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class MemoryStore:
    """Abstract memory storage interface.

    All concrete backends must implement these methods.  The store is
    partitioned by ``tenant_id`` for multi-tenancy support.
    """

    # -- Episodes ----------------------------------------------------------

    def store_episode(self, episode: Episode) -> UUID:
        """Persist an episode and return its episode_id."""
        raise NotImplementedError

    def get_episode(self, episode_id: UUID, tenant_id: str = "default") -> Episode | None:
        """Retrieve an episode by ID."""
        raise NotImplementedError

    def query_episodes(
        self,
        tenant_id: str = "default",
        *,
        environment: str | None = None,
        law_categories: set[str] | None = None,
        outcome: EpisodeOutcome | None = None,
        limit: int = 50,
    ) -> list[Episode]:
        """Query episodes with optional filters."""
        raise NotImplementedError

    def count_episodes(self, tenant_id: str = "default") -> int:
        """Return total episode count for a tenant."""
        raise NotImplementedError

    # -- SemanticRules -----------------------------------------------------

    def store_rule(self, rule: SemanticRule) -> UUID:
        raise NotImplementedError

    def get_rule(self, rule_id: UUID, tenant_id: str = "default") -> SemanticRule | None:
        raise NotImplementedError

    def query_rules(
        self,
        tenant_id: str = "default",
        *,
        region: set[UUID] | None = None,
        environment: str | None = None,
        limit: int = 50,
    ) -> list[SemanticRule]:
        raise NotImplementedError

    # -- Procedures --------------------------------------------------------

    def store_procedure(self, procedure: Procedure) -> UUID:
        raise NotImplementedError

    def get_procedure(self, procedure_id: UUID, tenant_id: str = "default") -> Procedure | None:
        raise NotImplementedError

    def query_procedures(
        self,
        tenant_id: str = "default",
        *,
        pattern_fingerprint: bytes | None = None,
        limit: int = 50,
    ) -> list[Procedure]:
        raise NotImplementedError

    # -- Patterns ----------------------------------------------------------

    def store_pattern(self, pattern: Pattern) -> UUID:
        raise NotImplementedError

    def get_pattern(self, pattern_id: UUID, tenant_id: str = "default") -> Pattern | None:
        raise NotImplementedError

    def query_patterns_by_signature(
        self, signature: bytes, tenant_id: str = "default"
    ) -> list[Pattern]:
        raise NotImplementedError

    # -- RepairTemplates ---------------------------------------------------

    def store_repair_template(self, template: RepairTemplate) -> UUID:
        raise NotImplementedError

    def get_repair_template(
        self, template_id: UUID, tenant_id: str = "default"
    ) -> RepairTemplate | None:
        raise NotImplementedError

    def query_repair_templates(
        self,
        tenant_id: str = "default",
        *,
        violation_pattern: bytes | None = None,
        limit: int = 50,
    ) -> list[RepairTemplate]:
        raise NotImplementedError

    # -- Generic -----------------------------------------------------------

    def delete(self, memory_type: MemoryType, item_id: UUID, tenant_id: str = "default") -> bool:
        """Delete an item by type and ID.  Returns True if found and deleted."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# InMemoryBackend
# ---------------------------------------------------------------------------


class InMemoryBackend(MemoryStore):
    """Dict-based in-memory backend for testing and single-process use."""

    def __init__(self) -> None:
        # tenant -> {episode_id -> Episode}
        self._episodes: dict[str, dict[UUID, Episode]] = defaultdict(dict)
        self._rules: dict[str, dict[UUID, SemanticRule]] = defaultdict(dict)
        self._procedures: dict[str, dict[UUID, Procedure]] = defaultdict(dict)
        self._patterns: dict[str, dict[UUID, Pattern]] = defaultdict(dict)
        self._repair_templates: dict[str, dict[UUID, RepairTemplate]] = defaultdict(dict)

    # -- Episodes ----------------------------------------------------------

    def store_episode(self, episode: Episode) -> UUID:
        self._episodes[episode.tenant_id][episode.episode_id] = episode
        log.debug("memory.store_episode", episode_id=str(episode.episode_id))
        return episode.episode_id

    def get_episode(self, episode_id: UUID, tenant_id: str = "default") -> Episode | None:
        return self._episodes[tenant_id].get(episode_id)

    def query_episodes(
        self,
        tenant_id: str = "default",
        *,
        environment: str | None = None,
        law_categories: set[str] | None = None,
        outcome: EpisodeOutcome | None = None,
        limit: int = 50,
    ) -> list[Episode]:
        results: list[Episode] = []
        for ep in self._episodes[tenant_id].values():
            if environment is not None and ep.environment != environment:
                continue
            if law_categories is not None and not law_categories.issubset(ep.law_categories):
                continue
            if outcome is not None and ep.outcome != outcome:
                continue
            results.append(ep)
            if len(results) >= limit:
                break
        return results

    def count_episodes(self, tenant_id: str = "default") -> int:
        return len(self._episodes[tenant_id])

    # -- SemanticRules -----------------------------------------------------

    def store_rule(self, rule: SemanticRule) -> UUID:
        self._rules[rule.tenant_id][rule.rule_id] = rule
        return rule.rule_id

    def get_rule(self, rule_id: UUID, tenant_id: str = "default") -> SemanticRule | None:
        return self._rules[tenant_id].get(rule_id)

    def query_rules(
        self,
        tenant_id: str = "default",
        *,
        region: set[UUID] | None = None,
        environment: str | None = None,
        limit: int = 50,
    ) -> list[SemanticRule]:
        results: list[SemanticRule] = []
        for rule in self._rules[tenant_id].values():
            if environment is not None and rule.environment != environment:
                continue
            if region is not None and not region.intersection(rule.region):
                continue
            results.append(rule)
            if len(results) >= limit:
                break
        return results

    # -- Procedures --------------------------------------------------------

    def store_procedure(self, procedure: Procedure) -> UUID:
        self._procedures[procedure.tenant_id][procedure.procedure_id] = procedure
        return procedure.procedure_id

    def get_procedure(self, procedure_id: UUID, tenant_id: str = "default") -> Procedure | None:
        return self._procedures[tenant_id].get(procedure_id)

    def query_procedures(
        self,
        tenant_id: str = "default",
        *,
        pattern_fingerprint: bytes | None = None,
        limit: int = 50,
    ) -> list[Procedure]:
        results: list[Procedure] = []
        for proc in self._procedures[tenant_id].values():
            if pattern_fingerprint is not None:
                if pattern_fingerprint not in proc.applicable_patterns:
                    continue
            results.append(proc)
            if len(results) >= limit:
                break
        return results

    # -- Patterns ----------------------------------------------------------

    def store_pattern(self, pattern: Pattern) -> UUID:
        self._patterns[pattern.tenant_id][pattern.pattern_id] = pattern
        return pattern.pattern_id

    def get_pattern(self, pattern_id: UUID, tenant_id: str = "default") -> Pattern | None:
        return self._patterns[tenant_id].get(pattern_id)

    def query_patterns_by_signature(
        self, signature: bytes, tenant_id: str = "default"
    ) -> list[Pattern]:
        return [
            p for p in self._patterns[tenant_id].values()
            if p.signature == signature
        ]

    # -- RepairTemplates ---------------------------------------------------

    def store_repair_template(self, template: RepairTemplate) -> UUID:
        self._repair_templates[template.tenant_id][template.template_id] = template
        return template.template_id

    def get_repair_template(
        self, template_id: UUID, tenant_id: str = "default"
    ) -> RepairTemplate | None:
        return self._repair_templates[tenant_id].get(template_id)

    def query_repair_templates(
        self,
        tenant_id: str = "default",
        *,
        violation_pattern: bytes | None = None,
        limit: int = 50,
    ) -> list[RepairTemplate]:
        results: list[RepairTemplate] = []
        for tmpl in self._repair_templates[tenant_id].values():
            if violation_pattern is not None:
                if tmpl.target_violation_pattern != violation_pattern:
                    continue
            results.append(tmpl)
            if len(results) >= limit:
                break
        return results

    # -- Generic -----------------------------------------------------------

    def delete(self, memory_type: MemoryType, item_id: UUID, tenant_id: str = "default") -> bool:
        store_map = {
            MemoryType.EPISODIC: self._episodes,
            MemoryType.SEMANTIC: self._rules,
            MemoryType.PROCEDURAL: self._procedures,
            MemoryType.PATTERN: self._patterns,
            MemoryType.REPAIR_TEMPLATE: self._repair_templates,
        }
        store = store_map.get(memory_type)
        if store is None:
            return False
        tenant_store = store.get(tenant_id, {})
        if item_id in tenant_store:
            del tenant_store[item_id]
            return True
        return False


# ---------------------------------------------------------------------------
# Stub backends (Phase 3 — external DB integration)
# ---------------------------------------------------------------------------


class PGMemoryBackend(MemoryStore):
    """PostgreSQL backend (requires asyncpg connection pool).

    Full implementation uses JSONB columns + GIN indexes for query_*.
    """

    def __init__(self, dsn: str = "") -> None:
        self._dsn = dsn
        log.info("pg_memory_backend.init", dsn=dsn or "(not configured)")


class Neo4jMemoryOverlay(MemoryStore):
    """Neo4j graph overlay for relationship-heavy queries.

    Supplements PGMemoryBackend with Cypher queries for graph-region
    and causal-pattern retrieval.
    """

    def __init__(self, uri: str = "") -> None:
        self._uri = uri
        log.info("neo4j_memory_overlay.init", uri=uri or "(not configured)")


class ObjectStorageBackend:
    """S3 / blob storage for large artefacts (logs, traces, snapshots).

    Not a full MemoryStore — just stores/retrieves binary blobs by key.
    """

    def __init__(self, bucket: str = "") -> None:
        self._bucket = bucket
        log.info("object_storage_backend.init", bucket=bucket or "(not configured)")

    def put(self, key: str, data: bytes) -> None:
        raise NotImplementedError("Requires S3/blob storage configuration")

    def get(self, key: str) -> bytes | None:
        raise NotImplementedError("Requires S3/blob storage configuration")
