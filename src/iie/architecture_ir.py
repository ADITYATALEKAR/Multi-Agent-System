"""Architecture Intermediate Representation for IIE passes.

Defines the data model for representing the system architecture:
components, connections, dataflows, and contracts. Built from the
reasoning graph and used by all IIE verification passes.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


class ComponentSpec(BaseModel):
    """Specification of a system component."""

    component_id: str
    name: str
    component_type: str  # "service", "library", "database", "queue", etc.
    contracts_provided: list[str] = Field(default_factory=list)  # contract_ids
    contracts_consumed: list[str] = Field(default_factory=list)  # contract_ids
    dependencies: list[str] = Field(default_factory=list)  # component_ids
    properties: dict[str, Any] = Field(default_factory=dict)


class Connection(BaseModel):
    """A connection between two components."""

    connection_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str  # component_id
    target: str  # component_id
    connection_type: str  # "depends_on", "calls", "publishes_to", "subscribes_to"
    contract_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class DataflowSpec(BaseModel):
    """A dataflow between components."""

    flow_id: str = Field(default_factory=lambda: str(uuid4()))
    source: str  # component_id
    target: str  # component_id
    data_type: str  # what data flows
    ordering: str = "unordered"  # "fifo", "causal_order", "total_order"
    properties: dict[str, Any] = Field(default_factory=dict)


class ArchitectureIR(BaseModel):
    """Complete Architecture Intermediate Representation.

    Built from the reasoning graph and contracts.
    Used by IIE passes for integrity verification.
    """

    components: dict[str, ComponentSpec] = Field(default_factory=dict)
    connections: list[Connection] = Field(default_factory=list)
    dataflows: list[DataflowSpec] = Field(default_factory=list)
    contracts: dict[str, Any] = Field(default_factory=dict)  # contract_id -> Contract dict
    properties: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build_from_graph(cls, graph_context: dict) -> ArchitectureIR:
        """Build an ArchitectureIR from a graph context dictionary.

        The graph_context should contain keys:
        - "components": list of component dicts
        - "connections": list of connection dicts
        - "dataflows": list of dataflow dicts
        - "contracts": dict of contract_id -> contract dict
        - "properties": optional dict of global properties
        """
        ir = cls()

        # Extract components
        for comp_data in graph_context.get("components", []):
            spec = ComponentSpec(**comp_data)
            ir.components[spec.component_id] = spec

        # Extract connections
        for conn_data in graph_context.get("connections", []):
            conn = Connection(**conn_data)
            ir.connections.append(conn)

        # Extract dataflows
        for flow_data in graph_context.get("dataflows", []):
            flow = DataflowSpec(**flow_data)
            ir.dataflows.append(flow)

        # Extract contracts
        ir.contracts = dict(graph_context.get("contracts", {}))

        # Extract global properties
        ir.properties = dict(graph_context.get("properties", {}))

        log.info(
            "architecture_ir.built",
            num_components=len(ir.components),
            num_connections=len(ir.connections),
            num_dataflows=len(ir.dataflows),
            num_contracts=len(ir.contracts),
        )
        return ir

    def get_component(self, component_id: str) -> ComponentSpec | None:
        """Return the component with the given ID, or None."""
        return self.components.get(component_id)

    def get_connections_for(self, component_id: str) -> list[Connection]:
        """Return all connections where the component is source or target."""
        return [
            c
            for c in self.connections
            if c.source == component_id or c.target == component_id
        ]

    def get_dependencies(self, component_id: str) -> set[str]:
        """Return the set of component IDs that *component_id* depends on.

        Combines explicit dependencies from the ComponentSpec and
        inferred dependencies from connections where the component is the source.
        """
        deps: set[str] = set()
        comp = self.get_component(component_id)
        if comp:
            deps.update(comp.dependencies)
        for conn in self.connections:
            if conn.source == component_id:
                deps.add(conn.target)
        return deps

    def get_dependents(self, component_id: str) -> set[str]:
        """Return the set of component IDs that depend on *component_id*."""
        dependents: set[str] = set()
        for cid, comp in self.components.items():
            if component_id in comp.dependencies:
                dependents.add(cid)
        for conn in self.connections:
            if conn.target == component_id:
                dependents.add(conn.source)
        return dependents

    def detect_cycles(self) -> list[list[str]]:
        """Detect all cycles in the dependency graph using DFS.

        Returns a list of cycles, where each cycle is a list of component IDs
        forming a loop (e.g. [A, B, C] means A -> B -> C -> A).
        """
        # Build adjacency list from explicit dependencies and connections
        graph: dict[str, set[str]] = {cid: set() for cid in self.components}
        for cid, comp in self.components.items():
            for dep in comp.dependencies:
                if dep in self.components:
                    graph[cid].add(dep)
        for conn in self.connections:
            if conn.source in self.components and conn.target in self.components:
                graph[conn.source].add(conn.target)

        cycles: list[list[str]] = []
        visited: set[str] = set()
        on_stack: set[str] = set()
        stack: list[str] = []

        def _dfs(node: str) -> None:
            visited.add(node)
            on_stack.add(node)
            stack.append(node)
            for neighbor in graph.get(node, set()):
                if neighbor not in visited:
                    _dfs(neighbor)
                elif neighbor in on_stack:
                    # Found a cycle: extract it from the stack
                    cycle_start = stack.index(neighbor)
                    cycle = list(stack[cycle_start:])
                    cycles.append(cycle)
            stack.pop()
            on_stack.discard(node)

        for node in graph:
            if node not in visited:
                _dfs(node)

        return cycles
