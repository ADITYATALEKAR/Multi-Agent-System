"""Structural Analyzer: regex-based detection of code smells and structural issues.

Detects: circular import hints, deeply nested blocks, large files, god classes,
long functions. Emits 'smell' nodes linked to file nodes via 'has_smell' edges.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)

# ── Thresholds ───────────────────────────────────────────────────────────────

_LARGE_FILE_LINES = 500
_DEEPLY_NESTED_INDENT = 5  # levels (each level = 4 spaces or 1 tab)
_GOD_CLASS_METHOD_COUNT = 15
_LONG_FUNCTION_LINES = 80

# ── Language-aware regex patterns ────────────────────────────────────────────

# Python imports
_PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.MULTILINE
)

# JS/TS imports
_JS_IMPORT_RE = re.compile(
    r"""^\s*import\s+(?:\{[^}]*\}|[\w*]+(?:\s+as\s+\w+)?)\s+from\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)

# Go imports
_GO_IMPORT_RE = re.compile(r'^\s*"([^"]+)"', re.MULTILINE)

# Java / C# imports
_JAVA_IMPORT_RE = re.compile(
    r"^\s*(?:import|using)\s+(?:static\s+)?([\w.]+)", re.MULTILINE
)

# Rust use
_RUST_USE_RE = re.compile(r"^\s*use\s+([\w:]+)", re.MULTILINE)

# C/C++ includes
_CPP_INCLUDE_RE = re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]', re.MULTILINE)

# Class-like declarations (multi-language)
_CLASS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:abstract\s+)?(?:public\s+|private\s+|protected\s+|internal\s+)?"
    r"(?:partial\s+)?(?:class|struct)\s+(\w+)",
    re.MULTILINE,
)

# Method / function-like patterns inside classes (multi-language)
_METHOD_RE = re.compile(
    r"^\s+(?:pub\s+)?(?:async\s+)?(?:static\s+)?(?:override\s+)?"
    r"(?:def |fn |func |function |(?:public|private|protected|internal)\s+)"
    r"\s*\w+\s*\(",
    re.MULTILINE,
)

# Top-level function definitions (multi-language)
_FUNCTION_DEF_RE = re.compile(
    r"^(?:export\s+)?(?:async\s+)?(?:def |fn |func |function )\s*(\w+)\s*\(",
    re.MULTILINE,
)

# Python-style function (for body length measurement)
_PY_FUNC_RE = re.compile(
    r"^( *)(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE
)

# Brace-delimited function (JS/TS/Java/Go/Rust/C#/C++)
_BRACE_FUNC_RE = re.compile(
    r"^[ \t]*(?:export\s+)?(?:pub(?:\(crate\))?\s+)?(?:async\s+)?(?:static\s+)?"
    r"(?:(?:public|private|protected|internal)\s+)?(?:(?:override|virtual|abstract)\s+)?"
    r"(?:def |fn |func |function |[\w<>\[\]]+\s+)(\w+)\s*\([^)]*\)\s*(?:->.*?)?\{",
    re.MULTILINE,
)


class StructuralAnalyzer(BaseAnalyzer):
    """Detects structural code smells across multiple languages via regex."""

    ANALYZER_ID = "structural"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [
        ".py", ".ts", ".tsx", ".js", ".jsx",
        ".java", ".go", ".rs", ".cs",
        ".cpp", ".cc", ".h", ".hpp",
    ]

    # ── Public API ───────────────────────────────────────────────────────

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        """Analyze source for structural smells and return graph deltas."""
        ops: list = []
        scope: set[UUID] = set()
        lines = source.split("\n")
        ext = Path(file_path).suffix.lower()

        # File node
        file_id = uuid4()
        ops.append(
            self._add_node(
                "file",
                Path(file_path).name,
                file_path=file_path,
                language=self._lang(ext),
                node_id=file_id,
                line_count=len(lines),
            )
        )
        scope.add(file_id)

        # Run each detector
        self._detect_large_file(lines, file_path, file_id, ops, scope)
        self._detect_circular_import_hints(source, file_path, ext, file_id, ops, scope)
        self._detect_deep_nesting(lines, file_path, ext, file_id, ops, scope)
        self._detect_god_classes(source, file_path, ext, file_id, ops, scope)
        self._detect_long_functions(source, lines, file_path, ext, file_id, ops, scope)

        # Only emit a delta if we found at least one smell (beyond the file node).
        if len(ops) <= 1:
            return []

        logger.debug(
            "structural_analysis_complete",
            file_path=file_path,
            smell_count=(len(ops) - 1) // 2,  # each smell = node + edge
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── Detectors ────────────────────────────────────────────────────────

    def _detect_large_file(
        self,
        lines: list[str],
        file_path: str,
        file_id: UUID,
        ops: list,
        scope: set[UUID],
    ) -> None:
        line_count = len(lines)
        if line_count >= _LARGE_FILE_LINES:
            severity = "warning" if line_count < 1000 else "error"
            sid = uuid4()
            ops.append(
                self._add_node(
                    "code_smell",
                    f"large_file:{line_count}_lines",
                    file_path=file_path,
                    node_id=sid,
                    smell_type="large_file",
                    severity=severity,
                    line_count=line_count,
                    threshold=_LARGE_FILE_LINES,
                )
            )
            scope.add(sid)
            ops.append(self._add_edge(file_id, sid, "has_smell"))

    def _detect_circular_import_hints(
        self,
        source: str,
        file_path: str,
        ext: str,
        file_id: UUID,
        ops: list,
        scope: set[UUID],
    ) -> None:
        """Flag self-referencing import paths (a heuristic for circular deps)."""
        stem = Path(file_path).stem
        parent = Path(file_path).parent.name

        import_paths: list[str] = []
        if ext == ".py":
            for m in _PY_IMPORT_RE.finditer(source):
                import_paths.append(m.group(1) or m.group(2))
        elif ext in {".ts", ".tsx", ".js", ".jsx"}:
            for m in _JS_IMPORT_RE.finditer(source):
                import_paths.append(m.group(1))
        elif ext == ".go":
            for m in _GO_IMPORT_RE.finditer(source):
                import_paths.append(m.group(1))
        elif ext in {".java", ".cs"}:
            for m in _JAVA_IMPORT_RE.finditer(source):
                import_paths.append(m.group(1))
        elif ext == ".rs":
            for m in _RUST_USE_RE.finditer(source):
                import_paths.append(m.group(1))
        elif ext in {".cpp", ".cc", ".h", ".hpp"}:
            for m in _CPP_INCLUDE_RE.finditer(source):
                import_paths.append(m.group(1))

        for imp in import_paths:
            # Heuristic: if the import path contains this file's own stem,
            # it *may* indicate a circular dependency.
            segments = re.split(r"[/\\.:]+", imp.lower())
            if stem.lower() in segments:
                sid = uuid4()
                ops.append(
                    self._add_node(
                        "code_smell",
                        f"circular_import_hint:{imp}",
                        file_path=file_path,
                        node_id=sid,
                        smell_type="circular_import_hint",
                        severity="warning",
                        import_path=imp,
                    )
                )
                scope.add(sid)
                ops.append(self._add_edge(file_id, sid, "has_smell"))

    def _detect_deep_nesting(
        self,
        lines: list[str],
        file_path: str,
        ext: str,
        file_id: UUID,
        ops: list,
        scope: set[UUID],
    ) -> None:
        """Detect lines with excessive indentation depth."""
        deepest_line = 0
        deepest_level = 0

        for i, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#") or stripped.startswith("//"):
                continue
            # Compute indent level
            leading = len(line) - len(stripped)
            if "\t" in line[:leading]:
                level = line[:leading].count("\t")
            else:
                level = leading // 4  # assume 4-space indent

            if level > deepest_level:
                deepest_level = level
                deepest_line = i

        if deepest_level >= _DEEPLY_NESTED_INDENT:
            severity = "warning" if deepest_level < 8 else "error"
            sid = uuid4()
            ops.append(
                self._add_node(
                    "code_smell",
                    f"deep_nesting:level_{deepest_level}",
                    file_path=file_path,
                    start_line=deepest_line,
                    node_id=sid,
                    smell_type="deep_nesting",
                    severity=severity,
                    nesting_level=deepest_level,
                    threshold=_DEEPLY_NESTED_INDENT,
                )
            )
            scope.add(sid)
            ops.append(self._add_edge(file_id, sid, "has_smell"))

    def _detect_god_classes(
        self,
        source: str,
        file_path: str,
        ext: str,
        file_id: UUID,
        ops: list,
        scope: set[UUID],
    ) -> None:
        """Detect classes with too many method-like definitions."""
        class_matches = list(_CLASS_RE.finditer(source))
        if not class_matches:
            return

        for idx, cm in enumerate(class_matches):
            class_name = cm.group(1)
            class_start = cm.start()
            # Determine class body boundary
            if idx + 1 < len(class_matches):
                class_end = class_matches[idx + 1].start()
            else:
                class_end = len(source)

            class_body = source[class_start:class_end]
            method_count = len(_METHOD_RE.findall(class_body))

            if method_count >= _GOD_CLASS_METHOD_COUNT:
                severity = "warning" if method_count < 25 else "error"
                start_line = source[:class_start].count("\n") + 1
                sid = uuid4()
                ops.append(
                    self._add_node(
                        "code_smell",
                        f"god_class:{class_name}",
                        file_path=file_path,
                        start_line=start_line,
                        node_id=sid,
                        smell_type="god_class",
                        severity=severity,
                        class_name=class_name,
                        method_count=method_count,
                        threshold=_GOD_CLASS_METHOD_COUNT,
                    )
                )
                scope.add(sid)
                ops.append(self._add_edge(file_id, sid, "has_smell"))

    def _detect_long_functions(
        self,
        source: str,
        lines: list[str],
        file_path: str,
        ext: str,
        file_id: UUID,
        ops: list,
        scope: set[UUID],
    ) -> None:
        """Detect functions whose body exceeds the line threshold."""
        if ext == ".py":
            self._detect_long_py_functions(source, lines, file_path, file_id, ops, scope)
        else:
            self._detect_long_brace_functions(source, lines, file_path, file_id, ops, scope)

    def _detect_long_py_functions(
        self,
        source: str,
        lines: list[str],
        file_path: str,
        file_id: UUID,
        ops: list,
        scope: set[UUID],
    ) -> None:
        """Detect long Python functions using indentation-based body measurement."""
        for m in _PY_FUNC_RE.finditer(source):
            func_name = m.group(2)
            base_indent = len(m.group(1))
            start_line = source[: m.start()].count("\n") + 1
            # Walk forward to find body end
            body_lines = 0
            for line in lines[start_line:]:  # lines after def
                stripped = line.lstrip()
                if not stripped:
                    body_lines += 1
                    continue
                indent = len(line) - len(stripped)
                if indent <= base_indent and stripped and not stripped.startswith("#"):
                    break
                body_lines += 1

            if body_lines >= _LONG_FUNCTION_LINES:
                severity = "warning" if body_lines < 150 else "error"
                sid = uuid4()
                ops.append(
                    self._add_node(
                        "code_smell",
                        f"long_function:{func_name}",
                        file_path=file_path,
                        start_line=start_line,
                        end_line=start_line + body_lines,
                        node_id=sid,
                        smell_type="long_function",
                        severity=severity,
                        function_name=func_name,
                        body_lines=body_lines,
                        threshold=_LONG_FUNCTION_LINES,
                    )
                )
                scope.add(sid)
                ops.append(self._add_edge(file_id, sid, "has_smell"))

    def _detect_long_brace_functions(
        self,
        source: str,
        lines: list[str],
        file_path: str,
        file_id: UUID,
        ops: list,
        scope: set[UUID],
    ) -> None:
        """Detect long brace-delimited functions by counting brace depth."""
        for m in _BRACE_FUNC_RE.finditer(source):
            func_name = m.group(1)
            start_line = source[: m.start()].count("\n") + 1
            # Find the opening brace position, then walk to matching close
            brace_pos = source.index("{", m.start())
            depth = 0
            end_pos = brace_pos
            for i in range(brace_pos, len(source)):
                ch = source[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        end_pos = i
                        break

            end_line = source[:end_pos].count("\n") + 1
            body_lines = end_line - start_line

            if body_lines >= _LONG_FUNCTION_LINES:
                severity = "warning" if body_lines < 150 else "error"
                sid = uuid4()
                ops.append(
                    self._add_node(
                        "code_smell",
                        f"long_function:{func_name}",
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        node_id=sid,
                        smell_type="long_function",
                        severity=severity,
                        function_name=func_name,
                        body_lines=body_lines,
                        threshold=_LONG_FUNCTION_LINES,
                    )
                )
                scope.add(sid)
                ops.append(self._add_edge(file_id, sid, "has_smell"))

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _lang(ext: str) -> str:
        _map = {
            ".py": "python", ".ts": "typescript", ".tsx": "typescript",
            ".js": "javascript", ".jsx": "javascript", ".java": "java",
            ".go": "go", ".rs": "rust", ".cs": "csharp",
            ".cpp": "cpp", ".cc": "cpp", ".h": "cpp", ".hpp": "cpp",
        }
        return _map.get(ext, "unknown")
