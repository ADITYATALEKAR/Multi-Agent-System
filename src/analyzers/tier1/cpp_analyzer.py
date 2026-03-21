"""C/C++ Analyzer: regex-based extraction.

Extracts: includes, namespaces, classes, structs, functions, methods, enums, typedefs.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta


class CppAnalyzer(BaseAnalyzer):
    ANALYZER_ID = "cpp"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".cpp", ".cc", ".cxx", ".h", ".hpp"]

    _INCLUDE_RE = re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]', re.MULTILINE)
    _NAMESPACE_RE = re.compile(r"^\s*namespace\s+(\w+)\s*\{", re.MULTILINE)
    _CLASS_RE = re.compile(
        r"^\s*(?:template\s*<[^>]*>\s*)?class\s+(\w+)"
        r"(?:\s*:\s*(?:public|protected|private)\s+(\w+))?",
        re.MULTILINE,
    )
    _STRUCT_RE = re.compile(r"^\s*(?:template\s*<[^>]*>\s*)?struct\s+(\w+)", re.MULTILINE)
    _ENUM_RE = re.compile(r"^\s*enum\s+(?:class\s+)?(\w+)", re.MULTILINE)
    _TYPEDEF_RE = re.compile(r"^\s*typedef\s+.+?\s+(\w+)\s*;", re.MULTILINE)
    _USING_RE = re.compile(r"^\s*using\s+(\w+)\s*=", re.MULTILINE)
    _FUNC_RE = re.compile(
        r"^\s*(?:static\s+)?(?:inline\s+)?(?:virtual\s+)?(?:const\s+)?"
        r"(?:[\w:*&<>]+)\s+(\w+)\s*\([^)]*\)\s*(?:const\s*)?(?:override\s*)?(?:=\s*0\s*)?[{;]",
        re.MULTILINE,
    )
    _METHOD_RE = re.compile(
        r"^\s*(?:[\w:*&<>]+)\s+(\w+)::(\w+)\s*\(",
        re.MULTILINE,
    )

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

        file_id = uuid4()
        ops.append(self._add_node(
            "file", file_path.split("/")[-1],
            file_path=file_path, language="cpp", node_id=file_id,
        ))
        scope.add(file_id)

        # Includes
        for m in self._INCLUDE_RE.finditer(source):
            inc_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "import", m.group(1),
                file_path=file_path, start_line=line, language="cpp", node_id=inc_id,
            ))
            scope.add(inc_id)
            ops.append(self._add_edge(file_id, inc_id, "imports"))

        # Namespaces
        for m in self._NAMESPACE_RE.finditer(source):
            ns_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "namespace", m.group(1),
                file_path=file_path, start_line=line, language="cpp", node_id=ns_id,
            ))
            scope.add(ns_id)
            ops.append(self._add_edge(file_id, ns_id, "contains"))

        # Classes
        class_ids: dict[str, UUID] = {}
        for m in self._CLASS_RE.finditer(source):
            cls_id = uuid4()
            name = m.group(1)
            class_ids[name] = cls_id
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "class", name,
                file_path=file_path, start_line=line, language="cpp", node_id=cls_id,
            ))
            scope.add(cls_id)
            ops.append(self._add_edge(file_id, cls_id, "contains"))

        # Structs
        for m in self._STRUCT_RE.finditer(source):
            s_id = uuid4()
            name = m.group(1)
            if name not in class_ids:
                class_ids[name] = s_id
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "class", name,
                file_path=file_path, start_line=line, language="cpp", node_id=s_id,
                cpp_kind="struct",
            ))
            scope.add(s_id)
            ops.append(self._add_edge(file_id, s_id, "contains"))

        # Enums
        for m in self._ENUM_RE.finditer(source):
            e_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "enum", m.group(1),
                file_path=file_path, start_line=line, language="cpp", node_id=e_id,
            ))
            scope.add(e_id)
            ops.append(self._add_edge(file_id, e_id, "contains"))

        # Typedefs and using aliases
        for m in self._TYPEDEF_RE.finditer(source):
            t_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "type_alias", m.group(1),
                file_path=file_path, start_line=line, language="cpp", node_id=t_id,
            ))
            scope.add(t_id)
            ops.append(self._add_edge(file_id, t_id, "defines"))

        for m in self._USING_RE.finditer(source):
            t_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "type_alias", m.group(1),
                file_path=file_path, start_line=line, language="cpp", node_id=t_id,
            ))
            scope.add(t_id)
            ops.append(self._add_edge(file_id, t_id, "defines"))

        # Out-of-class method definitions (ClassName::MethodName)
        method_positions: set[int] = set()
        for m in self._METHOD_RE.finditer(source):
            method_positions.add(m.start())
            method_id = uuid4()
            cls_name = m.group(1)
            meth_name = m.group(2)
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "method", meth_name,
                file_path=file_path, start_line=line, language="cpp", node_id=method_id,
                parent_class=cls_name,
            ))
            scope.add(method_id)
            parent = class_ids.get(cls_name)
            if parent:
                ops.append(self._add_edge(parent, method_id, "contains"))
            else:
                ops.append(self._add_edge(file_id, method_id, "contains"))

        # Free functions
        skip_names = {"if", "for", "while", "switch", "catch", "return", "class", "struct", "enum", "namespace"}
        for m in self._FUNC_RE.finditer(source):
            name = m.group(1)
            if name in skip_names or m.start() in method_positions:
                continue
            func_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "function", name,
                file_path=file_path, start_line=line, language="cpp", node_id=func_id,
            ))
            scope.add(func_id)
            ops.append(self._add_edge(file_id, func_id, "contains"))

        if not ops:
            return []
        return [self._make_delta(ops, file_path, scope)]
