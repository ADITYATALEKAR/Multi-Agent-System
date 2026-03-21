"""Memory subsystem: episodic, causal templates, memoization."""

from __future__ import annotations

from src.memory.types import (
    Episode,
    EpisodeOutcome,
    MemoryResult,
    MemoryType,
    Pattern,
    Procedure,
    ProcedureStep,
    RepairTemplate,
    SemanticRule,
    WorkingMemory,
)
from src.memory.storage import InMemoryBackend, MemoryStore
from src.memory.fingerprint import (
    FingerprintIndex,
    MinHashLSH,
    TwoLevelMemoKey,
    wl_hash,
)
from src.memory.retrieval import (
    CausalPatternRetrieval,
    EnvironmentFilter,
    GraphRegionRetrieval,
    LawBasedRetrieval,
    PatternMatchRetrieval,
    RepairTypeRetrieval,
)
from src.memory.memoization import MemoizationCache, CacheLineageTracker
from src.memory.consolidation import (
    ConsolidationPipeline,
    ConsolidationResult,
    ConfidenceAdjuster,
    PatternMatcher,
    RuleExtractor,
)
from src.memory.causal_template import (
    AbstractEdge,
    AbstractGraph,
    AbstractNode,
    CausalTemplate,
)
from src.memory.abstraction import ApproximateMCS, TemplateAbstractor
from src.memory.agent import MemoryAgent

__all__ = [
    # Types
    "Episode",
    "EpisodeOutcome",
    "MemoryResult",
    "MemoryType",
    "Pattern",
    "Procedure",
    "ProcedureStep",
    "RepairTemplate",
    "SemanticRule",
    "WorkingMemory",
    # Storage
    "InMemoryBackend",
    "MemoryStore",
    # Fingerprint
    "FingerprintIndex",
    "MinHashLSH",
    "TwoLevelMemoKey",
    "wl_hash",
    # Retrieval
    "CausalPatternRetrieval",
    "EnvironmentFilter",
    "GraphRegionRetrieval",
    "LawBasedRetrieval",
    "PatternMatchRetrieval",
    "RepairTypeRetrieval",
    # Memoization
    "MemoizationCache",
    "CacheLineageTracker",
    # Consolidation
    "ConsolidationPipeline",
    "ConsolidationResult",
    "ConfidenceAdjuster",
    "PatternMatcher",
    "RuleExtractor",
    # Causal Template
    "AbstractEdge",
    "AbstractGraph",
    "AbstractNode",
    "CausalTemplate",
    # Abstraction
    "ApproximateMCS",
    "TemplateAbstractor",
    # Agent
    "MemoryAgent",
]
