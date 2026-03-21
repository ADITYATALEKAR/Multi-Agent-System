"""Rust Analyzer: regex-based extraction.

Extracts: use/mod imports, structs, enums, traits, impl blocks, functions, macros.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta


class RustAnalyzer(BaseAnalyzer):
    ANALYZER_ID = "rust"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".rs"]

    _USE_RE = re.compile(r"^\s*(?:pub\s+)?use\s+([\w:]+(?:::\{[^}]+\})?)\s*;", re.MULTILINE)
    _MOD_RE = re.compile(r"^\s*(?:pub\s+)?mod\s+(\w+)\s*[;{]", re.MULTILINE)
    _STRUCT_RE = re.compile(
        r"^\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)",
        re.MULTILINE,
    )
    _ENUM_RE = re.compile(
        r"^\s*(?:#\[[^\]]*\]\s*)*(?:pub(?:\([^)]*\))?\s+)?enum\s+(\w+)",
        re.MULTILINE,
    )
    _TRAIT_RE = re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:unsafe\s+)?trait\s+(\w+)",
        re.MULTILINE,
    )
    _IMPL_RE = re.compile(
        r"^\s*impl(?:<[^>]*>)?\s+(?:(\w+)\s+for\s+)?(\w+)",
        re.MULTILINE,
    )
    _FN_RE = re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+(\w+)",
        re.MULTILINE,
    )
    _MACRO_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?macro_rules!\s+(\w+)", re.MULTILINE)
    _TYPE_ALIAS_RE = re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?type\s+(\w+)",
        re.MULTILINE,
    )
    _CONST_RE = re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+(\w+)\s*:",
        re.MULTILINE,
    )

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

        file_id = uuid4()
        ops.append(self._add_node(
            "file", file_path.split("/")[-1],
            file_path=file_path, language="rust", node_id=file_id,
        ))
        scope.add(file_id)

        # use imports
        for m in self._USE_RE.finditer(source):
            imp_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "import", m.group(1),
                file_path=file_path, start_line=line, language="rust", node_id=imp_id,
            ))
            scope.add(imp_id)
            ops.append(self._add_edge(file_id, imp_id, "imports"))

        # mod declarations
        for m in self._MOD_RE.finditer(source):
            mod_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "module", m.group(1),
                file_path=file_path, start_line=line, language="rust", node_id=mod_id,
            ))
            scope.add(mod_id)
            ops.append(self._add_edge(file_id, mod_id, "contains"))

        # Structs
        struct_ids: dict[str, UUID] = {}
        for m in self._STRUCT_RE.finditer(source):
            s_id = uuid4()
            name = m.group(1)
            struct_ids[name] = s_id
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "class", name,
                file_path=file_path, start_line=line, language="rust", node_id=s_id,
                rust_kind="struct",
            ))
            scope.add(s_id)
            ops.append(self._add_edge(file_id, s_id, "contains"))

        # Enums
        for m in self._ENUM_RE.finditer(source):
            e_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "enum", m.group(1),
                file_path=file_path, start_line=line, language="rust", node_id=e_id,
            ))
            scope.add(e_id)
            ops.append(self._add_edge(file_id, e_id, "contains"))

        # Traits
        trait_ids: dict[str, UUID] = {}
        for m in self._TRAIT_RE.finditer(source):
            t_id = uuid4()
            name = m.group(1)
            trait_ids[name] = t_id
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "interface", name,
                file_path=file_path, start_line=line, language="rust", node_id=t_id,
                rust_kind="trait",
            ))
            scope.add(t_id)
            ops.append(self._add_edge(file_id, t_id, "contains"))

        # Impl blocks
        for m in self._IMPL_RE.finditer(source):
            trait_name = m.group(1)  # None for inherent impl
            type_name = m.group(2)
            if trait_name and trait_name in trait_ids and type_name in struct_ids:
                ops.append(self._add_edge(
                    struct_ids[type_name], trait_ids[trait_name], "implements",
                ))

        # Functions
        for m in self._FN_RE.finditer(source):
            func_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "function", m.group(1),
                file_path=file_path, start_line=line, language="rust", node_id=func_id,
            ))
            scope.add(func_id)
            ops.append(self._add_edge(file_id, func_id, "contains"))

        # Macros
        for m in self._MACRO_RE.finditer(source):
            mac_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "macro", m.group(1),
                file_path=file_path, start_line=line, language="rust", node_id=mac_id,
            ))
            scope.add(mac_id)
            ops.append(self._add_edge(file_id, mac_id, "contains"))

        # Type aliases
        for m in self._TYPE_ALIAS_RE.finditer(source):
            ta_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "type_alias", m.group(1),
                file_path=file_path, start_line=line, language="rust", node_id=ta_id,
            ))
            scope.add(ta_id)
            ops.append(self._add_edge(file_id, ta_id, "defines"))

        # Constants / statics
        for m in self._CONST_RE.finditer(source):
            name = m.group(1)
            if name == "_":
                continue
            c_id = uuid4()
            line = source[:m.start()].count("\n") + 1
            ops.append(self._add_node(
                "constant", name,
                file_path=file_path, start_line=line, language="rust", node_id=c_id,
            ))
            scope.add(c_id)
            ops.append(self._add_edge(file_id, c_id, "defines"))

        if not ops:
            return []
        return [self._make_delta(ops, file_path, scope)]
