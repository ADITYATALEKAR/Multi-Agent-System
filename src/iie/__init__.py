"""Internal Integrity Engine: compiler-style verification passes.

Exports the architecture IR, engine, runtime monitor, and all passes.
"""

from src.iie.architecture_ir import (
    ArchitectureIR,
    ComponentSpec,
    Connection,
    DataflowSpec,
)
from src.iie.engine import IIEEngine
from src.iie.passes.base import BasePass, IntegrityViolation
from src.iie.runtime_monitor import IIERuntimeMonitor

__all__ = [
    "ArchitectureIR",
    "BasePass",
    "ComponentSpec",
    "Connection",
    "DataflowSpec",
    "IIEEngine",
    "IIERuntimeMonitor",
    "IntegrityViolation",
]
