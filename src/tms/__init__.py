"""Truth Maintenance System: belief revision and justification tracking."""

from __future__ import annotations

from src.tms.belief import BeliefNode
from src.tms.confidence import ConfidencePropagator
from src.tms.engine import TMSEngine
from src.tms.index import TMSIndex

__all__ = [
    "BeliefNode",
    "ConfidencePropagator",
    "TMSEngine",
    "TMSIndex",
]
