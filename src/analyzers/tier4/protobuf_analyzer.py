"""Protobuf Analyzer: regex-based extraction of messages, enums, services, and RPCs."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)


class ProtobufAnalyzer(BaseAnalyzer):
    """Analyzes Protocol Buffer definition files.

    Extracts syntax, package, imports, message definitions (with nested
    messages and field types), enum definitions, service definitions,
    and rpc definitions.
    """

    ANALYZER_ID = "protobuf"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".proto"]

    # ── Regex patterns ────────────────────────────────────────────────────

    _SYNTAX_RE = re.compile(r'syntax\s*=\s*"(proto[23])"\s*;', re.IGNORECASE)
    _PACKAGE_RE = re.compile(r"package\s+([\w.]+)\s*;", re.IGNORECASE)
    _IMPORT_RE = re.compile(r'import\s+(?:public\s+|weak\s+)?"([^"]+)"\s*;', re.IGNORECASE)
    _OPTION_RE = re.compile(r"option\s+(\w+)\s*=\s*(.+?)\s*;", re.IGNORECASE)

    # Top-level message (we handle nesting with brace counting)
    _MESSAGE_RE = re.compile(r"^message\s+(\w+)\s*\{", re.MULTILINE)
    _ENUM_RE = re.compile(r"^enum\s+(\w+)\s*\{", re.MULTILINE)
    _SERVICE_RE = re.compile(r"^service\s+(\w+)\s*\{", re.MULTILINE)
    _RPC_RE = re.compile(
        r"rpc\s+(\w+)\s*\(\s*(stream\s+)?(\w+)\s*\)\s*returns\s*\(\s*(stream\s+)?(\w+)\s*\)",
        re.IGNORECASE,
    )

    # Field definition inside a message
    _FIELD_RE = re.compile(
        r"^\s*(?:repeated\s+|optional\s+|required\s+)?(?:map\s*<\s*(\w+)\s*,\s*(\w+)\s*>|(\w+(?:\.\w+)*))\s+(\w+)\s*=\s*(\d+)",
        re.MULTILINE,
    )

    # Proto built-in scalar types
    _SCALAR_TYPES = frozenset({
        "double", "float", "int32", "int64", "uint32", "uint64",
        "sint32", "sint64", "fixed32", "fixed64", "sfixed32", "sfixed64",
        "bool", "string", "bytes",
    })

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()
        message_name_to_id: dict[str, UUID] = {}

        file_id = uuid4()
        ops.append(self._add_node(
            "file", file_path.split("/")[-1].split("\\")[-1],
            file_path=file_path, language="protobuf", node_id=file_id,
        ))
        scope.add(file_id)

        # ── Syntax ────────────────────────────────────────────────────
        syntax_m = self._SYNTAX_RE.search(source)
        syntax_version = syntax_m.group(1) if syntax_m else "proto3"

        # ── Package ───────────────────────────────────────────────────
        package_name = ""
        pkg_m = self._PACKAGE_RE.search(source)
        if pkg_m:
            package_name = pkg_m.group(1)

        # ── Imports ───────────────────────────────────────────────────
        for m in self._IMPORT_RE.finditer(source):
            import_path = m.group(1)
            line = source[:m.start()].count("\n") + 1

            imp_id = uuid4()
            ops.append(self._add_node(
                "import", import_path, file_path=file_path,
                start_line=line, language="protobuf", node_id=imp_id,
            ))
            scope.add(imp_id)
            ops.append(self._add_edge(file_id, imp_id, "imports"))

        # ── Messages ──────────────────────────────────────────────────
        for m in self._MESSAGE_RE.finditer(source):
            msg_name = m.group(1)
            line = source[:m.start()].count("\n") + 1
            body = self._extract_brace_block(source, m.end() - 1)

            qualified = f"{package_name}.{msg_name}" if package_name else msg_name
            msg_id = uuid4()
            message_name_to_id[msg_name] = msg_id

            # Extract fields
            fields: list[dict[str, str]] = []
            field_types: list[str] = []
            for fm in self._FIELD_RE.finditer(body):
                map_key = fm.group(1)
                map_val = fm.group(2)
                plain_type = fm.group(3)
                field_name = fm.group(4)
                field_number = fm.group(5)

                if map_key and map_val:
                    ftype = f"map<{map_key},{map_val}>"
                    field_types.append(map_val)
                else:
                    ftype = plain_type or ""
                    field_types.append(ftype)

                fields.append({
                    "name": field_name,
                    "type": ftype,
                    "number": field_number,
                })

            # Detect nested messages
            nested_msgs = re.findall(r"message\s+(\w+)\s*\{", body)

            ops.append(self._add_node(
                "protobuf_message", qualified, file_path=file_path,
                start_line=line, language="protobuf", node_id=msg_id,
                package=package_name, syntax=syntax_version,
                fields=fields, nested_messages=nested_msgs,
            ))
            scope.add(msg_id)
            ops.append(self._add_edge(file_id, msg_id, "contains"))

        # ── Resolve message -> message references ─────────────────────
        for m in self._MESSAGE_RE.finditer(source):
            msg_name = m.group(1)
            body = self._extract_brace_block(source, m.end() - 1)
            src_id = message_name_to_id.get(msg_name)
            if src_id is None:
                continue

            for fm in self._FIELD_RE.finditer(body):
                map_val = fm.group(2)
                plain_type = fm.group(3)
                ft = map_val if map_val else (plain_type or "")
                base_type = ft.split(".")[-1]

                if base_type.lower() not in self._SCALAR_TYPES and base_type:
                    tgt_id = message_name_to_id.get(base_type)
                    if tgt_id and tgt_id != src_id:
                        ops.append(self._add_edge(src_id, tgt_id, "references"))

        # ── Enums ─────────────────────────────────────────────────────
        for m in self._ENUM_RE.finditer(source):
            enum_name = m.group(1)
            line = source[:m.start()].count("\n") + 1
            body = self._extract_brace_block(source, m.end() - 1)

            qualified = f"{package_name}.{enum_name}" if package_name else enum_name
            values = re.findall(r"(\w+)\s*=\s*(\d+)", body)

            enum_id = uuid4()
            ops.append(self._add_node(
                "protobuf_enum", qualified, file_path=file_path,
                start_line=line, language="protobuf", node_id=enum_id,
                package=package_name,
                values=[{"name": v[0], "number": v[1]} for v in values],
            ))
            scope.add(enum_id)
            ops.append(self._add_edge(file_id, enum_id, "contains"))

        # ── Services and RPCs ─────────────────────────────────────────
        for m in self._SERVICE_RE.finditer(source):
            svc_name = m.group(1)
            line = source[:m.start()].count("\n") + 1
            body = self._extract_brace_block(source, m.end() - 1)

            qualified = f"{package_name}.{svc_name}" if package_name else svc_name
            svc_id = uuid4()
            ops.append(self._add_node(
                "protobuf_service", qualified, file_path=file_path,
                start_line=line, language="protobuf", node_id=svc_id,
                package=package_name,
            ))
            scope.add(svc_id)
            ops.append(self._add_edge(file_id, svc_id, "contains"))

            for rpc_m in self._RPC_RE.finditer(body):
                rpc_name = rpc_m.group(1)
                req_stream = bool(rpc_m.group(2))
                req_type = rpc_m.group(3)
                resp_stream = bool(rpc_m.group(4))
                resp_type = rpc_m.group(5)
                rpc_line = line + body[:rpc_m.start()].count("\n")

                rpc_id = uuid4()
                ops.append(self._add_node(
                    "protobuf_rpc", rpc_name, file_path=file_path,
                    start_line=rpc_line, language="protobuf", node_id=rpc_id,
                    request_type=req_type, response_type=resp_type,
                    request_streaming=req_stream, response_streaming=resp_stream,
                ))
                scope.add(rpc_id)
                ops.append(self._add_edge(svc_id, rpc_id, "contains"))

                # RPC references request/response message types
                for ref_type in (req_type, resp_type):
                    ref_id = message_name_to_id.get(ref_type)
                    if ref_id:
                        ops.append(self._add_edge(rpc_id, ref_id, "references"))

        if not ops:
            return []

        logger.debug(
            "protobuf_analysis_complete", file_path=file_path,
            messages=len(message_name_to_id), operations=len(ops),
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_brace_block(source: str, open_brace_pos: int) -> str:
        """Extract content between matching braces starting at open_brace_pos.

        Returns the content between (but not including) the braces.
        """
        if open_brace_pos >= len(source) or source[open_brace_pos] != "{":
            return ""

        depth = 0
        start = open_brace_pos + 1
        for i in range(open_brace_pos, len(source)):
            if source[i] == "{":
                depth += 1
            elif source[i] == "}":
                depth -= 1
                if depth == 0:
                    return source[start:i]
        return source[start:]
