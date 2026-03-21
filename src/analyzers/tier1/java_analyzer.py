"""Java Analyzer: regex-based extraction of classes, interfaces, methods, imports."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta


class JavaAnalyzer(BaseAnalyzer):
    ANALYZER_ID = "java"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".java"]

    _PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
    _IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)
    _CLASS_RE = re.compile(r"^\s*(?:public|private|protected)?\s*(?:abstract|final)?\s*class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?", re.MULTILINE)
    _INTERFACE_RE = re.compile(r"^\s*(?:public|private|protected)?\s*interface\s+(\w+)(?:\s+extends\s+([\w,\s]+))?", re.MULTILINE)
    _METHOD_RE = re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?(?:synchronized\s+)?(?:\w+(?:<[^>]+>)?)\s+(\w+)\s*\(", re.MULTILINE)
    _ENUM_RE = re.compile(r"^\s*(?:public|private|protected)?\s*enum\s+(\w+)", re.MULTILINE)

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

        file_id = uuid4()
        ops.append(self._add_node("file", file_path.split("/")[-1], file_path=file_path, language="java", node_id=file_id))
        scope.add(file_id)

        for m in self._PACKAGE_RE.finditer(source):
            pkg_id = uuid4()
            ops.append(self._add_node("package", m.group(1), file_path=file_path, language="java", node_id=pkg_id))
            scope.add(pkg_id)
            ops.append(self._add_edge(file_id, pkg_id, "belongs_to"))

        for m in self._IMPORT_RE.finditer(source):
            imp_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("import", m.group(1), file_path=file_path, start_line=line, language="java", node_id=imp_id))
            scope.add(imp_id)
            ops.append(self._add_edge(file_id, imp_id, "imports"))

        for m in self._CLASS_RE.finditer(source):
            cls_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("class", m.group(1), file_path=file_path, start_line=line, language="java", node_id=cls_id))
            scope.add(cls_id)
            ops.append(self._add_edge(file_id, cls_id, "contains"))

        for m in self._INTERFACE_RE.finditer(source):
            iface_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("interface", m.group(1), file_path=file_path, start_line=line, language="java", node_id=iface_id))
            scope.add(iface_id)
            ops.append(self._add_edge(file_id, iface_id, "contains"))

        for m in self._ENUM_RE.finditer(source):
            enum_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("enum", m.group(1), file_path=file_path, start_line=line, language="java", node_id=enum_id))
            scope.add(enum_id)
            ops.append(self._add_edge(file_id, enum_id, "contains"))

        for m in self._METHOD_RE.finditer(source):
            name = m.group(1)
            if name in ("if", "for", "while", "switch", "catch", "return", "class"):
                continue
            method_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node("method", name, file_path=file_path, start_line=line, language="java", node_id=method_id))
            scope.add(method_id)

        if not ops:
            return []
        return [self._make_delta(ops, file_path, scope)]
