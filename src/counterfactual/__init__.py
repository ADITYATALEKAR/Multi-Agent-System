"""Counterfactual simulation subsystem.

Exports the three main components:
- AdaptiveSimulationBoundary — computes the subgraph boundary for replay
- DeltaReplayEngine — replays delta streams under interventions
- CounterfactualEngine — orchestrates boundary + replay to validate hypotheses
"""

from src.counterfactual.boundary import AdaptiveSimulationBoundary
from src.counterfactual.engine import CounterfactualEngine
from src.counterfactual.replay import DeltaReplayEngine

__all__ = [
    "AdaptiveSimulationBoundary",
    "CounterfactualEngine",
    "DeltaReplayEngine",
]
