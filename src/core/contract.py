"""Inter-component contracts: Contract, TypeSpec, Predicate."""

from __future__ import annotations

import enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class OwnershipTransfer(str, enum.Enum):
    NONE = "none"
    EXCLUSIVE = "exclusive"
    SHARED = "shared"
    COPY = "copy"


class Ordering(str, enum.Enum):
    UNORDERED = "unordered"
    FIFO = "fifo"
    CAUSAL_ORDER = "causal_order"
    TOTAL_ORDER = "total_order"


class TypeSpec(BaseModel):
    """Schema specification for contract input/output."""

    type_name: str
    fields: dict[str, Any] = Field(default_factory=dict)


class Predicate(BaseModel):
    """A boolean predicate for contract conditions."""

    expression: str
    description: str = ""


class Contract(BaseModel):
    """Inter-component contract with formal guarantees.

    v3.3 A5: smt_constraints use SMT-LIB2 format, not Z3Expr.
    """

    contract_id: str
    provider: str  # ComponentID
    consumer: str  # ComponentID
    input_schema: TypeSpec
    output_schema: TypeSpec
    preconditions: list[Predicate] = Field(default_factory=list)
    postconditions: list[Predicate] = Field(default_factory=list)
    invariants: list[Predicate] = Field(default_factory=list)
    smt_constraints: Optional[list[str]] = None  # SMT-LIB2 format (v3.3 A5)
    ownership_transfer: OwnershipTransfer = OwnershipTransfer.NONE
    ordering: Ordering = Ordering.UNORDERED
    idempotent: bool = False
    timeout_ms: int = 5000
    staleness_bound_ms: Optional[int] = None
