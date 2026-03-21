"""Go Analyzer: regex-based extraction.

Extracts: packages, imports, structs, interfaces, functions, methods, types.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta


class GoAnalyzer(BaseAnalyzer):
    ANALYZER_ID = "go"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".go"]

    _PACKAGE_RE = re.compile(r"^\s*package\s+(\w+)", re.MULTILINE)
    _IMPORT_SINGLE_RE = re.compile(r'^\s*import\s+"([^"]+)"', re.MULTILINE)
    _IMPORT_BLOCK_RE = re.compile(r'^\s*import\s*\((.*?)\)', re.MULTILINE | re.DOTALL)
    _IMPORT_LINE_RE = re.compile(r'^\s*(?:(\w+)\s+)?"([^"]+)"', re.MULTILINE)
    _STRUCT_RE = re.compile(r"^\s*type\s+(\w+)\s+struct\s*\{", re.MULTILINE)
    _INTERFACE_RE = re.compile(r"^\s*type\s+(\w+)\s+interface\s*\{", re.MULTILINE)
    _TYPE_ALIAS_RE = re.compile(r"^\s*type\s+(\w+)\s+(?!struct|interface)\w+", re.MULTILINE)
    _FUNC_RE = re.compile(r"^\s*func\s+(\w+)\s*\(", re.MULTILINE)
    _METHOD_RE = re.compile(r"^\s*func\s+\(\s*\w+\s+\*?(\w+)\s*\)\s+(\w+)\s*\(", re.MULTILINE)

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

        file_id = uuid4()
        ops.append(self._add_node(
            "file", file_path.split("/")[-1],
            file_path=file_path, language="go", node_id=file_id,
        ))
        scope.add(file_id)

        # Package
        for m in self._PACKAGE_RE.finditer(source):
            pkg_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "package", m.group(1),
                file_path=file_path, start_line=line, language="go", node_id=pkg_id,
            ))
            scope.add(pkg_id)
            ops.append(self._add_edge(file_id, pkg_id, "declares"))

        # Single-line imports
        for m in self._IMPORT_SINGLE_RE.finditer(source):
            imp_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "import", m.group(1),
                file_path=file_path, start_line=line, language="go", node_id=imp_id,
            ))
            scope.add(imp_id)
            ops.append(self._add_edge(file_id, imp_id, "imports"))

        # Block imports
        for block_m in self._IMPORT_BLOCK_RE.finditer(source):
            block_start = source[:block_m.start()].count("\n") + 1
            block_text = block_m.group(1)
            for m in self._IMPORT_LINE_RE.finditer(block_text):
                imp_id = uuid4()
                line = block_start + block_text[:m.start()].count("\n") + 1
                ops.append(self._add_node(
                    "import", m.group(2),
                    file_path=file_path, start_line=line, language="go", node_id=imp_id,
                    alias=m.group(1),
                ))
                scope.add(imp_id)
                ops.append(self._add_edge(file_id, imp_id, "imports"))

        # Structs
        struct_ids: dict[str, UUID] = {}
        for m in self._STRUCT_RE.finditer(source):
            s_id = uuid4()
            name = m.group(1)
            struct_ids[name] = s_id
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "class", name,
                file_path=file_path, start_line=line, language="go", node_id=s_id,
                go_kind="struct",
            ))
            scope.add(s_id)
            ops.append(self._add_edge(file_id, s_id, "contains"))

        # Interfaces
        for m in self._INTERFACE_RE.finditer(source):
            iface_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "interface", m.group(1),
                file_path=file_path, start_line=line, language="go", node_id=iface_id,
            ))
            scope.add(iface_id)
            ops.append(self._add_edge(file_id, iface_id, "contains"))

        # Type aliases
        for m in self._TYPE_ALIAS_RE.finditer(source):
            name = m.group(1)
            if name in struct_ids:
                continue
            t_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "type_alias", name,
                file_path=file_path, start_line=line, language="go", node_id=t_id,
            ))
            scope.add(t_id)
            ops.append(self._add_edge(file_id, t_id, "defines"))

        # Methods (func (r *Receiver) Name())
        method_positions: set[int] = set()
        for m in self._METHOD_RE.finditer(source):
            method_positions.add(m.start())
            method_id = uuid4()
            receiver_name = m.group(1)
            method_name = m.group(2)
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "method", method_name,
                file_path=file_path, start_line=line, language="go", node_id=method_id,
                receiver=receiver_name,
            ))
            scope.add(method_id)
            parent = struct_ids.get(receiver_name)
            if parent:
                ops.append(self._add_edge(parent, method_id, "contains"))
            else:
                ops.append(self._add_edge(file_id, method_id, "contains"))

        # Free functions (exclude methods)
        for m in self._FUNC_RE.finditer(source):
            if m.start() in method_positions:
                continue
            func_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "function", m.group(1),
                file_path=file_path, start_line=line, language="go", node_id=func_id,
            ))
            scope.add(func_id)
            ops.append(self._add_edge(file_id, func_id, "contains"))

        if not ops:
            return []
        return [self._make_delta(ops, file_path, scope)]
