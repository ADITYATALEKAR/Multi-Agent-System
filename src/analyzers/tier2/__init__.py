"""Tier 2: Structural pattern analyzers."""

from __future__ import annotations

from src.analyzers.tier2.structural_analyzer import StructuralAnalyzer

ALL_TIER2_ANALYZERS = [StructuralAnalyzer]

__all__ = ["StructuralAnalyzer", "ALL_TIER2_ANALYZERS"]
