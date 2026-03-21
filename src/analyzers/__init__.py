"""Analyzers: language-specific and infrastructure parsers."""

from __future__ import annotations

from src.analyzers.tier1 import ALL_TIER1_ANALYZERS
from src.analyzers.tier2 import ALL_TIER2_ANALYZERS
from src.analyzers.tier3 import ALL_TIER3_ANALYZERS

ALL_ANALYZERS = ALL_TIER1_ANALYZERS + ALL_TIER2_ANALYZERS + ALL_TIER3_ANALYZERS
