"""TypeScript/JavaScript Analyzer: regex-based extraction.

Extracts: classes, interfaces, functions, arrow functions, imports, exports.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta


class TypeScriptAnalyzer(BaseAnalyzer):
    ANALYZER_ID = "typescript"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".ts", ".tsx", ".js", ".jsx"]

    _CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?", re.MULTILINE)
    _INTERFACE_RE = re.compile(r"^\s*(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+([\w,\s]+))?", re.MULTILINE)
    _FUNCTION_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE)
    _ARROW_RE = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(", re.MULTILINE)
    _IMPORT_RE = re.compile(r"^\s*import\s+(?:\{[^}]*\}|[\w*]+(?:\s+as\s+\w+)?)\s+from\s+['\"]([^'\"]+)['\"]", re.MULTILINE)
    _EXPORT_RE = re.compile(r"^\s*export\s+(?:default\s+)?(?:class|function|const|let|var|interface|type|enum)\s+(\w+)", re.MULTILINE)

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()
        lines = source.split("\n")

        file_id = uuid4()
        ops.append(self._add_node("file", file_path.split("/")[-1], file_path=file_path, language="typescript", node_id=file_id))
        scope.add(file_id)

        for m in self._CLASS_RE.finditer(source):
            cls_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("class", m.group(1), file_path=file_path, start_line=line, language="typescript", node_id=cls_id))
            scope.add(cls_id)
            ops.append(self._add_edge(file_id, cls_id, "contains"))

        for m in self._INTERFACE_RE.finditer(source):
            iface_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("interface", m.group(1), file_path=file_path, start_line=line, language="typescript", node_id=iface_id))
            scope.add(iface_id)
            ops.append(self._add_edge(file_id, iface_id, "contains"))

        for m in self._FUNCTION_RE.finditer(source):
            func_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("function", m.group(1), file_path=file_path, start_line=line, language="typescript", node_id=func_id))
            scope.add(func_id)
            ops.append(self._add_edge(file_id, func_id, "contains"))

        for m in self._ARROW_RE.finditer(source):
            func_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("function", m.group(1), file_path=file_path, start_line=line, language="typescript", node_id=func_id))
            scope.add(func_id)
            ops.append(self._add_edge(file_id, func_id, "contains"))

        for m in self._IMPORT_RE.finditer(source):
            imp_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("import", m.group(1), file_path=file_path, start_line=line, language="typescript", node_id=imp_id))
            scope.add(imp_id)
            ops.append(self._add_edge(file_id, imp_id, "imports"))

        if not ops:
            return []
        return [self._make_delta(ops, file_path, scope)]
