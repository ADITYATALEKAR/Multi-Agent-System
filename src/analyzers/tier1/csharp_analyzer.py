"""C# Analyzer: regex-based extraction.

Extracts: usings, namespaces, classes, interfaces, structs, enums, methods, properties.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta


class CSharpAnalyzer(BaseAnalyzer):
    ANALYZER_ID = "csharp"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".cs"]

    _USING_RE = re.compile(r"^\s*using\s+([\w.]+)\s*;", re.MULTILINE)
    _NAMESPACE_RE = re.compile(r"^\s*namespace\s+([\w.]+)", re.MULTILINE)
    _CLASS_RE = re.compile(
        r"^\s*(?:public|private|protected|internal)?\s*"
        r"(?:static\s+)?(?:abstract\s+)?(?:sealed\s+)?(?:partial\s+)?"
        r"class\s+(\w+)(?:<[^>]+>)?(?:\s*:\s*([\w.,\s<>]+))?",
        re.MULTILINE,
    )
    _INTERFACE_RE = re.compile(
        r"^\s*(?:public|private|protected|internal)?\s*"
        r"(?:partial\s+)?interface\s+(\w+)(?:<[^>]+>)?(?:\s*:\s*([\w.,\s<>]+))?",
        re.MULTILINE,
    )
    _STRUCT_RE = re.compile(
        r"^\s*(?:public|private|protected|internal)?\s*"
        r"(?:readonly\s+)?(?:partial\s+)?struct\s+(\w+)",
        re.MULTILINE,
    )
    _ENUM_RE = re.compile(
        r"^\s*(?:public|private|protected|internal)?\s*enum\s+(\w+)",
        re.MULTILINE,
    )
    _METHOD_RE = re.compile(
        r"^\s*(?:public|private|protected|internal)?\s*"
        r"(?:static\s+)?(?:virtual\s+)?(?:override\s+)?(?:abstract\s+)?(?:async\s+)?"
        r"(?:[\w<>\[\]?,\s]+)\s+(\w+)\s*\(",
        re.MULTILINE,
    )
    _PROPERTY_RE = re.compile(
        r"^\s*(?:public|private|protected|internal)?\s*"
        r"(?:static\s+)?(?:virtual\s+)?(?:override\s+)?"
        r"(?:[\w<>\[\]?,]+)\s+(\w+)\s*\{",
        re.MULTILINE,
    )

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

        file_id = uuid4()
        ops.append(self._add_node(
            "file", file_path.split("/")[-1],
            file_path=file_path, language="csharp", node_id=file_id,
        ))
        scope.add(file_id)

        # Usings
        for m in self._USING_RE.finditer(source):
            imp_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "import", m.group(1),
                file_path=file_path, start_line=line, language="csharp", node_id=imp_id,
            ))
            scope.add(imp_id)
            ops.append(self._add_edge(file_id, imp_id, "imports"))

        # Namespaces
        for m in self._NAMESPACE_RE.finditer(source):
            ns_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "namespace", m.group(1),
                file_path=file_path, start_line=line, language="csharp", node_id=ns_id,
            ))
            scope.add(ns_id)
            ops.append(self._add_edge(file_id, ns_id, "contains"))

        # Classes
        for m in self._CLASS_RE.finditer(source):
            cls_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "class", m.group(1),
                file_path=file_path, start_line=line, language="csharp", node_id=cls_id,
            ))
            scope.add(cls_id)
            ops.append(self._add_edge(file_id, cls_id, "contains"))

        # Interfaces
        for m in self._INTERFACE_RE.finditer(source):
            iface_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "interface", m.group(1),
                file_path=file_path, start_line=line, language="csharp", node_id=iface_id,
            ))
            scope.add(iface_id)
            ops.append(self._add_edge(file_id, iface_id, "contains"))

        # Structs
        for m in self._STRUCT_RE.finditer(source):
            s_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "class", m.group(1),
                file_path=file_path, start_line=line, language="csharp", node_id=s_id,
                csharp_kind="struct",
            ))
            scope.add(s_id)
            ops.append(self._add_edge(file_id, s_id, "contains"))

        # Enums
        for m in self._ENUM_RE.finditer(source):
            e_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "enum", m.group(1),
                file_path=file_path, start_line=line, language="csharp", node_id=e_id,
            ))
            scope.add(e_id)
            ops.append(self._add_edge(file_id, e_id, "contains"))

        # Methods
        skip_names = {
            "if", "for", "while", "switch", "catch", "return", "class",
            "struct", "enum", "namespace", "using", "get", "set", "new",
        }
        for m in self._METHOD_RE.finditer(source):
            name = m.group(1)
            if name in skip_names:
                continue
            method_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "method", name,
                file_path=file_path, start_line=line, language="csharp", node_id=method_id,
            ))
            scope.add(method_id)

        if not ops:
            return []
        return [self._make_delta(ops, file_path, scope)]
