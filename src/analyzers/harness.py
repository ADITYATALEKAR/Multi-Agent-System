"""Generic tree-sitter analyzer harness.

Provides BaseAnalyzer abstract class and AnalyzerHarness orchestrator.
All Tier 1-5 analyzers inherit from BaseAnalyzer.
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.core.fact import (
    CURRENT_SCHEMA_VERSION,
    AddEdge,
    AddNode,
    GraphDelta,
)
from src.state_graph.schema import EdgeType, NodeType

logger = structlog.get_logger(__name__)

# Extension -> language mapping
_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".java": "java",
    ".go": "go",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".rs": "rust",
    ".cs": "csharp",
    ".sql": "sql",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".proto": "protobuf",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".tf": "terraform",
    ".hcl": "terraform",
    ".Dockerfile": "docker",
}


def file_fingerprint(content: str) -> str:
    """Compute a content fingerprint for caching."""
    return hashlib.sha256(content.encode()).hexdigest()


class BaseAnalyzer(ABC):
    """Abstract base class for all analyzers."""

    ANALYZER_ID: str = "base"
    VERSION: str = "0.1.0"
    SUPPORTED_EXTENSIONS: list[str] = []

    @abstractmethod
    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        """Analyze source code and return graph deltas.

        Args:
            source: Source code content as string.
            file_path: Path to the source file (for metadata).

        Returns:
            List of GraphDelta objects representing discovered entities and relationships.
        """
        ...

    def _make_delta(
        self, operations: list, file_path: str, scope: set[UUID] | None = None
    ) -> GraphDelta:
        """Helper to create a GraphDelta from operations."""
        return GraphDelta(
            sequence_number=0,  # Assigned by DeltaLogStore on append
            source=f"analyzer:{self.ANALYZER_ID}",
            operations=operations,
            scope=scope or set(),
            schema_version=CURRENT_SCHEMA_VERSION,
        )

    def _add_node(
        self,
        node_type: str,
        name: str,
        file_path: str = "",
        start_line: int = 0,
        end_line: int = 0,
        language: str = "",
        node_id: UUID | None = None,
        **extra_attrs: Any,
    ) -> AddNode:
        """Helper to create an AddNode operation with standard attributes."""
        nid = node_id or uuid4()
        attrs: dict[str, Any] = {
            "name": name,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "language": language,
            "analyzer": self.ANALYZER_ID,
            "analyzer_version": self.VERSION,
        }
        attrs.update(extra_attrs)
        return AddNode(node_id=nid, node_type=node_type, attributes=attrs)

    def _add_edge(
        self,
        src_id: UUID,
        tgt_id: UUID,
        edge_type: str,
        **extra_attrs: Any,
    ) -> AddEdge:
        """Helper to create an AddEdge operation."""
        attrs: dict[str, Any] = {
            "source_analyzer": self.ANALYZER_ID,
            "confidence": 1.0,
        }
        attrs.update(extra_attrs)
        return AddEdge(
            src_id=src_id,
            tgt_id=tgt_id,
            edge_type=edge_type,
            attributes=attrs,
        )


class AnalyzerHarness:
    """Orchestrates registration and execution of analyzers.

    Maps file extensions to analyzers and provides batch analysis capabilities.
    """

    def __init__(self) -> None:
        self._analyzers: dict[str, BaseAnalyzer] = {}
        self._extension_map: dict[str, str] = {}  # ext -> analyzer_id

    def register_analyzer(self, analyzer: BaseAnalyzer) -> None:
        """Register an analyzer and map its supported extensions."""
        self._analyzers[analyzer.ANALYZER_ID] = analyzer
        for ext in analyzer.SUPPORTED_EXTENSIONS:
            self._extension_map[ext] = analyzer.ANALYZER_ID
        logger.info(
            "analyzer_registered",
            analyzer_id=analyzer.ANALYZER_ID,
            extensions=analyzer.SUPPORTED_EXTENSIONS,
        )

    def get_analyzer_for_file(self, file_path: str) -> BaseAnalyzer | None:
        """Find the appropriate analyzer for a file based on extension."""
        ext = Path(file_path).suffix.lower()
        # Special case for Dockerfile
        if Path(file_path).name == "Dockerfile" or Path(file_path).name.startswith("Dockerfile."):
            ext = ".Dockerfile"
        analyzer_id = self._extension_map.get(ext)
        if analyzer_id:
            return self._analyzers.get(analyzer_id)
        return None

    def get_analyzer(self, analyzer_id: str) -> BaseAnalyzer | None:
        """Get an analyzer by ID."""
        return self._analyzers.get(analyzer_id)

    async def analyze_file(self, file_path: str, source: str | None = None) -> list[GraphDelta]:
        """Analyze a single file.

        Args:
            file_path: Path to the file.
            source: Optional pre-loaded source content.

        Returns:
            List of GraphDelta objects.
        """
        analyzer = self.get_analyzer_for_file(file_path)
        if analyzer is None:
            logger.debug("no_analyzer_for_file", file_path=file_path)
            return []

        if source is None:
            try:
                with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                    source = f.read()
            except (OSError, IOError) as e:
                logger.warning("file_read_failed", file_path=file_path, error=str(e))
                return []

        try:
            deltas = analyzer.analyze(source, file_path)
            logger.debug(
                "file_analyzed",
                file_path=file_path,
                analyzer=analyzer.ANALYZER_ID,
                deltas=len(deltas),
            )
            return deltas
        except Exception as e:
            logger.error(
                "analyzer_failed",
                file_path=file_path,
                analyzer=analyzer.ANALYZER_ID,
                error=str(e),
            )
            return []

    async def analyze_directory(self, dir_path: str) -> list[GraphDelta]:
        """Analyze all supported files in a directory recursively."""
        all_deltas: list[GraphDelta] = []
        for root, _dirs, files in os.walk(dir_path):
            for fname in files:
                fpath = os.path.join(root, fname)
                deltas = await self.analyze_file(fpath)
                all_deltas.extend(deltas)
        return all_deltas

    async def run_analyzer(self, analyzer_id: str, input_path: Path) -> list[GraphDelta]:
        """Run a specific analyzer on a file or directory."""
        analyzer = self._analyzers.get(analyzer_id)
        if analyzer is None:
            raise ValueError(f"Unknown analyzer: {analyzer_id}")

        if input_path.is_dir():
            return await self.analyze_directory(str(input_path))
        else:
            return await self.analyze_file(str(input_path))

    @property
    def registered_analyzers(self) -> list[str]:
        return list(self._analyzers.keys())
