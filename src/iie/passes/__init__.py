"""IIE verification passes.

All 12 passes plus the base class and violation model.
"""

from src.iie.passes.base import BasePass, IntegrityViolation
from src.iie.passes.cache_lineage import CacheLineagePass
from src.iie.passes.circular_dep import CircularDepPass
from src.iie.passes.contract import ContractPass
from src.iie.passes.dataflow import DataflowPass
from src.iie.passes.delta_consumption import DeltaConsumptionPass
from src.iie.passes.determinism import DeterminismPass
from src.iie.passes.nondeterminism import NondeterminismPass
from src.iie.passes.solver_budget import SolverBudgetPass
from src.iie.passes.split_graph import SplitGraphPass
from src.iie.passes.stale_derived import StaleDerivedPass
from src.iie.passes.storage_budget import StorageBudgetPass
from src.iie.passes.structural import StructuralPass

__all__ = [
    "BasePass",
    "IntegrityViolation",
    "StructuralPass",
    "CircularDepPass",
    "ContractPass",
    "DataflowPass",
    "DeterminismPass",
    "NondeterminismPass",
    "SplitGraphPass",
    "DeltaConsumptionPass",
    "CacheLineagePass",
    "StaleDerivedPass",
    "SolverBudgetPass",
    "StorageBudgetPass",
]
