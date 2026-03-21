"""Precomputed Structural Indexes (v3.2 Risk Fix A).

DependsClosure, BlastRadiusIndex, ServiceBoundary, ImportResolution, CallGraph.
Maintained incrementally by IndexMaintainer on each delta.
Target: update <100ms per delta.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


class DependsClosure:
    """Transitive closure of dependency edges.

    Answers: "What does X transitively depend on?" in O(1) lookup after build.
    """

    def __init__(self) -> None:
        self._forward: dict[UUID, set[UUID]] = defaultdict(set)  # X depends on Y
        self._reverse: dict[UUID, set[UUID]] = defaultdict(set)  # Y is depended on by X

    def add_edge(self, src: UUID, tgt: UUID) -> None:
        """Add a dependency edge and recompute affected closures."""
        self._forward[src].add(tgt)
        self._reverse[tgt].add(src)

    def remove_edge(self, src: UUID, tgt: UUID) -> None:
        self._forward[src].discard(tgt)
        self._reverse[tgt].discard(src)

    def get_dependencies(self, node_id: UUID) -> set[UUID]:
        """Transitive forward dependencies of node_id."""
        visited: set[UUID] = set()
        queue = deque(self._forward.get(node_id, set()))
        while queue:
            nid = queue.popleft()
            if nid not in visited:
                visited.add(nid)
                queue.extend(self._forward.get(nid, set()) - visited)
        return visited

    def get_dependents(self, node_id: UUID) -> set[UUID]:
        """Transitive reverse dependencies (who depends on node_id)."""
        visited: set[UUID] = set()
        queue = deque(self._reverse.get(node_id, set()))
        while queue:
            nid = queue.popleft()
            if nid not in visited:
                visited.add(nid)
                queue.extend(self._reverse.get(nid, set()) - visited)
        return visited


class BlastRadiusIndex:
    """Estimates blast radius of a change to a node.

    Combines DependsClosure reverse + service boundary crossing.
    """

    def __init__(self, depends: DependsClosure) -> None:
        self._depends = depends
        self._service_map: dict[UUID, str] = {}  # node_id -> service name

    def set_service(self, node_id: UUID, service: str) -> None:
        self._service_map[node_id] = service

    def blast_radius(self, node_id: UUID) -> dict[str, Any]:
        """Compute blast radius for a node change."""
        affected = self._depends.get_dependents(node_id)
        services_affected = {
            self._service_map[nid]
            for nid in affected
            if nid in self._service_map
        }
        return {
            "node_id": node_id,
            "affected_nodes": len(affected),
            "affected_services": list(services_affected),
            "cross_service": len(services_affected) > 1,
        }


class ServiceBoundary:
    """Maps nodes to service boundaries."""

    def __init__(self) -> None:
        self._node_to_service: dict[UUID, str] = {}
        self._service_to_nodes: dict[str, set[UUID]] = defaultdict(set)

    def assign(self, node_id: UUID, service: str) -> None:
        old = self._node_to_service.get(node_id)
        if old:
            self._service_to_nodes[old].discard(node_id)
        self._node_to_service[node_id] = service
        self._service_to_nodes[service].add(node_id)

    def get_service(self, node_id: UUID) -> str | None:
        return self._node_to_service.get(node_id)

    def get_nodes(self, service: str) -> set[UUID]:
        return self._service_to_nodes.get(service, set())

    def all_services(self) -> list[str]:
        return list(self._service_to_nodes.keys())


class ImportResolution:
    """Resolves import relationships to actual module nodes."""

    def __init__(self) -> None:
        self._imports: dict[UUID, set[UUID]] = defaultdict(set)
        self._qualified_names: dict[str, UUID] = {}

    def register_name(self, qualified_name: str, node_id: UUID) -> None:
        self._qualified_names[qualified_name] = node_id

    def add_import(self, importer: UUID, imported: UUID) -> None:
        self._imports[importer].add(imported)

    def resolve(self, qualified_name: str) -> UUID | None:
        return self._qualified_names.get(qualified_name)

    def get_imports(self, node_id: UUID) -> set[UUID]:
        return self._imports.get(node_id, set())


class CallGraph:
    """In-memory call graph index."""

    def __init__(self) -> None:
        self._callers: dict[UUID, set[UUID]] = defaultdict(set)
        self._callees: dict[UUID, set[UUID]] = defaultdict(set)

    def add_call(self, caller: UUID, callee: UUID) -> None:
        self._callers[callee].add(caller)
        self._callees[caller].add(callee)

    def remove_call(self, caller: UUID, callee: UUID) -> None:
        self._callers[callee].discard(caller)
        self._callees[caller].discard(callee)

    def get_callers(self, node_id: UUID) -> set[UUID]:
        return self._callers.get(node_id, set())

    def get_callees(self, node_id: UUID) -> set[UUID]:
        return self._callees.get(node_id, set())


class PrecomputedIndexes:
    """Aggregates all precomputed structural indexes.

    Provides unified query interface and incremental update support.
    """

    def __init__(self) -> None:
        self.depends = DependsClosure()
        self.blast_radius = BlastRadiusIndex(self.depends)
        self.service_boundary = ServiceBoundary()
        self.import_resolution = ImportResolution()
        self.call_graph = CallGraph()
        self._type_index: dict[str, set[UUID]] = defaultdict(set)
        self._attr_index: dict[str, dict[str, set[UUID]]] = defaultdict(lambda: defaultdict(set))

    def add_node(self, node_id: UUID, node_type: str, attributes: dict[str, Any]) -> None:
        """Register a node in all relevant indexes."""
        self._type_index[node_type].add(node_id)
        for key, value in attributes.items():
            self._attr_index[key][str(value)].add(node_id)
        # Auto-assign service if present
        if "service" in attributes:
            self.service_boundary.assign(node_id, str(attributes["service"]))
            self.blast_radius.set_service(node_id, str(attributes["service"]))

    def remove_node(self, node_id: UUID, node_type: str) -> None:
        """Remove a node from all indexes."""
        self._type_index.get(node_type, set()).discard(node_id)

    def add_edge(self, src: UUID, tgt: UUID, edge_type: str) -> None:
        """Register an edge in relevant indexes."""
        if edge_type in ("depends_on", "imports", "uses"):
            self.depends.add_edge(src, tgt)
        if edge_type == "calls":
            self.call_graph.add_call(src, tgt)
        if edge_type == "imports":
            self.import_resolution.add_import(src, tgt)

    def remove_edge(self, src: UUID, tgt: UUID, edge_type: str) -> None:
        """Remove an edge from relevant indexes."""
        if edge_type in ("depends_on", "imports", "uses"):
            self.depends.remove_edge(src, tgt)
        if edge_type == "calls":
            self.call_graph.remove_call(src, tgt)

    def query_by_type(self, node_type: str) -> list[UUID]:
        """Query nodes by type."""
        return list(self._type_index.get(node_type, set()))

    def query_by_attribute(self, key: str, value: Any) -> list[UUID]:
        """Query nodes by attribute value."""
        return list(self._attr_index.get(key, {}).get(str(value), set()))

    def rebuild(self) -> None:
        """Clear and prepare for full rebuild."""
        self.depends = DependsClosure()
        self.blast_radius = BlastRadiusIndex(self.depends)
        self.service_boundary = ServiceBoundary()
        self.import_resolution = ImportResolution()
        self.call_graph = CallGraph()
        self._type_index.clear()
        self._attr_index.clear()
        logger.info("precomputed_indexes_rebuilt")
