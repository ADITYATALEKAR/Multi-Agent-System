"""Law Engine: declarative rules, evaluation, governance.

Exports:
    LawDefinition, LawCategory, EvalMode — law model primitives.
    LawLibrary — registry of 100+ built-in laws.
    LawEvaluator — bridges laws to the Rete network for delta evaluation.
"""

from __future__ import annotations

from src.law_engine.evaluator import LawEvaluator
from src.law_engine.law import EvalMode, LawCategory, LawDefinition
from src.law_engine.library import LawLibrary

__all__ = [
    "EvalMode",
    "LawCategory",
    "LawDefinition",
    "LawEvaluator",
    "LawLibrary",
]
