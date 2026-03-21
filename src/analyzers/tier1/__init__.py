"""Tier 1: Language-specific source code analyzers."""

from __future__ import annotations

from src.analyzers.tier1.python_analyzer import PythonAnalyzer
from src.analyzers.tier1.typescript_analyzer import TypeScriptAnalyzer
from src.analyzers.tier1.java_analyzer import JavaAnalyzer
from src.analyzers.tier1.go_analyzer import GoAnalyzer
from src.analyzers.tier1.cpp_analyzer import CppAnalyzer
from src.analyzers.tier1.rust_analyzer import RustAnalyzer
from src.analyzers.tier1.csharp_analyzer import CSharpAnalyzer

ALL_TIER1_ANALYZERS = [
    PythonAnalyzer,
    TypeScriptAnalyzer,
    JavaAnalyzer,
    GoAnalyzer,
    CppAnalyzer,
    RustAnalyzer,
    CSharpAnalyzer,
]

__all__ = [
    "PythonAnalyzer",
    "TypeScriptAnalyzer",
    "JavaAnalyzer",
    "GoAnalyzer",
    "CppAnalyzer",
    "RustAnalyzer",
    "CSharpAnalyzer",
    "ALL_TIER1_ANALYZERS",
]
