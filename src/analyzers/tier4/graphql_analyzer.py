"""GraphQL Analyzer: regex-based extraction of types, fields, enums, interfaces, and operations."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)


class GraphQLAnalyzer(BaseAnalyzer):
    """Analyzes GraphQL schema definitions and operations.

    Extracts type definitions, input types, enum types, interface types,
    union types, query/mutation/subscription definitions, and directive
    definitions.
    """

    ANALYZER_ID = "graphql"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".graphql", ".gql"]

    # ── Regex patterns ────────────────────────────────────────────────────

    _TYPE_DEF_RE = re.compile(
        r"^type\s+(\w+)\s*(?:implements\s+([\w\s&]+))?\s*\{(.*?)\}",
        re.MULTILINE | re.DOTALL,
    )

    _INPUT_TYPE_RE = re.compile(
        r"^input\s+(\w+)\s*\{(.*?)\}",
        re.MULTILINE | re.DOTALL,
    )

    _ENUM_RE = re.compile(
        r"^enum\s+(\w+)\s*\{(.*?)\}",
        re.MULTILINE | re.DOTALL,
    )

    _INTERFACE_RE = re.compile(
        r"^interface\s+(\w+)\s*\{(.*?)\}",
        re.MULTILINE | re.DOTALL,
    )

    _UNION_RE = re.compile(
        r"^union\s+(\w+)\s*=\s*(.+?)$",
        re.MULTILINE,
    )

    _DIRECTIVE_RE = re.compile(
        r"^directive\s+@(\w+)(?:\s*\([^)]*\))?\s+on\s+(.+?)$",
        re.MULTILINE,
    )

    _FIELD_RE = re.compile(
        r"^\s+(\w+)\s*(?:\([^)]*\))?\s*:\s*(\[?\w+!?\]?!?)",
        re.MULTILINE,
    )

    _EXTEND_TYPE_RE = re.compile(
        r"^extend\s+type\s+(\w+)\s*\{(.*?)\}",
        re.MULTILINE | re.DOTALL,
    )

    _SCHEMA_DEF_RE = re.compile(
        r"^schema\s*\{(.*?)\}",
        re.MULTILINE | re.DOTALL,
    )

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

        file_id = uuid4()
        ops.append(self._add_node(
            "file", file_path.split("/")[-1].split("\\")[-1],
            file_path=file_path, language="graphql", node_id=file_id,
        ))
        scope.add(file_id)

        # Detect custom root type names from schema definition
        query_root = "Query"
        mutation_root = "Mutation"
        subscription_root = "Subscription"
        schema_m = self._SCHEMA_DEF_RE.search(source)
        if schema_m:
            body = schema_m.group(1)
            q = re.search(r"query\s*:\s*(\w+)", body)
            m_ = re.search(r"mutation\s*:\s*(\w+)", body)
            s = re.search(r"subscription\s*:\s*(\w+)", body)
            if q:
                query_root = q.group(1)
            if m_:
                mutation_root = m_.group(1)
            if s:
                subscription_root = s.group(1)

        # ── Type definitions ──────────────────────────────────────────
        for m in self._TYPE_DEF_RE.finditer(source):
            type_name = m.group(1)
            implements_raw = m.group(2) or ""
            body = m.group(3)
            line = source[:m.start()].count("\n") + 1

            type_id = uuid4()

            # Determine if this is a root operation type
            if type_name == query_root:
                node_type = "graphql_query"
            elif type_name == mutation_root:
                node_type = "graphql_mutation"
            elif type_name == subscription_root:
                node_type = "graphql_mutation"  # subscriptions modeled as mutation variant
            else:
                node_type = "graphql_type"

            ops.append(self._add_node(
                node_type, type_name, file_path=file_path,
                start_line=line, language="graphql", node_id=type_id,
            ))
            scope.add(type_id)
            ops.append(self._add_edge(file_id, type_id, "contains"))

            # Parse implements clause
            if implements_raw.strip():
                interfaces = [
                    iface.strip()
                    for iface in re.split(r"[&,]", implements_raw)
                    if iface.strip()
                ]
                for iface_name in interfaces:
                    iface_id = uuid4()
                    ops.append(self._add_node(
                        "graphql_interface", iface_name, file_path=file_path,
                        start_line=line, language="graphql", node_id=iface_id,
                    ))
                    scope.add(iface_id)
                    ops.append(self._add_edge(type_id, iface_id, "implements"))

            # Parse fields
            self._extract_fields(body, type_id, file_path, line, ops, scope)

        # ── Input types ───────────────────────────────────────────────
        for m in self._INPUT_TYPE_RE.finditer(source):
            input_name = m.group(1)
            body = m.group(2)
            line = source[:m.start()].count("\n") + 1

            input_id = uuid4()
            ops.append(self._add_node(
                "graphql_type", input_name, file_path=file_path,
                start_line=line, language="graphql", node_id=input_id,
                subtype="input",
            ))
            scope.add(input_id)
            ops.append(self._add_edge(file_id, input_id, "contains"))
            self._extract_fields(body, input_id, file_path, line, ops, scope)

        # ── Enum types ────────────────────────────────────────────────
        for m in self._ENUM_RE.finditer(source):
            enum_name = m.group(1)
            body = m.group(2)
            line = source[:m.start()].count("\n") + 1

            enum_id = uuid4()
            values = [
                v.strip()
                for v in body.strip().split("\n")
                if v.strip() and not v.strip().startswith("#")
            ]

            ops.append(self._add_node(
                "graphql_enum", enum_name, file_path=file_path,
                start_line=line, language="graphql", node_id=enum_id,
                values=values,
            ))
            scope.add(enum_id)
            ops.append(self._add_edge(file_id, enum_id, "contains"))

        # ── Interface definitions ─────────────────────────────────────
        for m in self._INTERFACE_RE.finditer(source):
            iface_name = m.group(1)
            body = m.group(2)
            line = source[:m.start()].count("\n") + 1

            iface_id = uuid4()
            ops.append(self._add_node(
                "graphql_interface", iface_name, file_path=file_path,
                start_line=line, language="graphql", node_id=iface_id,
            ))
            scope.add(iface_id)
            ops.append(self._add_edge(file_id, iface_id, "contains"))
            self._extract_fields(body, iface_id, file_path, line, ops, scope)

        # ── Union types ───────────────────────────────────────────────
        for m in self._UNION_RE.finditer(source):
            union_name = m.group(1)
            members_raw = m.group(2)
            line = source[:m.start()].count("\n") + 1

            union_id = uuid4()
            members = [t.strip() for t in members_raw.split("|") if t.strip()]

            ops.append(self._add_node(
                "graphql_type", union_name, file_path=file_path,
                start_line=line, language="graphql", node_id=union_id,
                subtype="union", members=members,
            ))
            scope.add(union_id)
            ops.append(self._add_edge(file_id, union_id, "contains"))

        # ── Directive definitions ─────────────────────────────────────
        for m in self._DIRECTIVE_RE.finditer(source):
            directive_name = m.group(1)
            locations = m.group(2).strip()
            line = source[:m.start()].count("\n") + 1

            dir_id = uuid4()
            ops.append(self._add_node(
                "graphql_type", f"@{directive_name}", file_path=file_path,
                start_line=line, language="graphql", node_id=dir_id,
                subtype="directive", locations=locations,
            ))
            scope.add(dir_id)
            ops.append(self._add_edge(file_id, dir_id, "contains"))

        # ── Extend type ───────────────────────────────────────────────
        for m in self._EXTEND_TYPE_RE.finditer(source):
            ext_name = m.group(1)
            body = m.group(2)
            line = source[:m.start()].count("\n") + 1

            ext_id = uuid4()
            ops.append(self._add_node(
                "graphql_type", f"{ext_name} (extension)", file_path=file_path,
                start_line=line, language="graphql", node_id=ext_id,
                subtype="extension", extends=ext_name,
            ))
            scope.add(ext_id)
            ops.append(self._add_edge(file_id, ext_id, "contains"))
            self._extract_fields(body, ext_id, file_path, line, ops, scope)

        if not ops:
            return []

        logger.debug("graphql_analysis_complete", file_path=file_path, operations=len(ops))
        return [self._make_delta(ops, file_path, scope)]

    # ── Helpers ───────────────────────────────────────────────────────────

    def _extract_fields(
        self,
        body: str,
        parent_id: UUID,
        file_path: str,
        base_line: int,
        ops: list,
        scope: set[UUID],
    ) -> None:
        """Extract fields from a GraphQL type/interface/input body."""
        for fm in self._FIELD_RE.finditer(body):
            field_name = fm.group(1)
            field_type = fm.group(2)
            field_line = base_line + body[:fm.start()].count("\n")

            field_id = uuid4()
            ops.append(self._add_node(
                "graphql_field", field_name, file_path=file_path,
                start_line=field_line, language="graphql", node_id=field_id,
                field_type=field_type,
            ))
            scope.add(field_id)
            ops.append(self._add_edge(parent_id, field_id, "contains"))
