"""API route definitions -- central registry of all routers."""

from __future__ import annotations

from src.api.routes import graph, health, memory, repairs, tasks, violations

__all__ = [
    "graph",
    "health",
    "memory",
    "repairs",
    "tasks",
    "violations",
]
