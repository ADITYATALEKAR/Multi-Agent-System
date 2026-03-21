"""SQL Analyzer: regex-based extraction of tables, views, functions, indexes, and relationships."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)


class SQLAnalyzer(BaseAnalyzer):
    """Analyzes SQL schema definitions and queries.

    Extracts CREATE TABLE (with columns and foreign keys), CREATE VIEW,
    CREATE INDEX, CREATE FUNCTION/PROCEDURE, ALTER TABLE, INSERT INTO,
    and SELECT...FROM...JOIN statements.
    """

    ANALYZER_ID = "sql"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".sql"]

    # ── Regex patterns ────────────────────────────────────────────────────

    _CREATE_TABLE_RE = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:`?(\w+)`?\.)?`?(\w+)`?\s*\((.*?)\)\s*;",
        re.IGNORECASE | re.DOTALL,
    )

    _COLUMN_RE = re.compile(
        r"^\s*`?(\w+)`?\s+([\w()]+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\))?)",
        re.MULTILINE,
    )

    _FK_RE = re.compile(
        r"FOREIGN\s+KEY\s*\(\s*`?(\w+)`?\s*\)\s*REFERENCES\s+`?(?:(\w+)\.)?(\w+)`?\s*\(\s*`?(\w+)`?\s*\)",
        re.IGNORECASE,
    )

    _CREATE_INDEX_RE = re.compile(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?\s+ON\s+`?(?:(\w+)\.)?(\w+)`?\s*\(([^)]+)\)",
        re.IGNORECASE,
    )

    _CREATE_VIEW_RE = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:MATERIALIZED\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:`?(\w+)`?\.)?`?(\w+)`?\s+AS\s+(.*?)(?:;|$)",
        re.IGNORECASE | re.DOTALL,
    )

    _CREATE_FUNC_RE = re.compile(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+(?:`?(\w+)`?\.)?`?(\w+)`?\s*\(",
        re.IGNORECASE,
    )

    _ALTER_TABLE_FK_RE = re.compile(
        r"ALTER\s+TABLE\s+(?:`?(\w+)`?\.)?`?(\w+)`?\s+ADD\s+(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\(\s*`?(\w+)`?\s*\)\s*REFERENCES\s+`?(?:(\w+)\.)?(\w+)`?\s*\(\s*`?(\w+)`?\s*\)",
        re.IGNORECASE,
    )

    _SELECT_FROM_RE = re.compile(
        r"(?:FROM|JOIN)\s+`?(?:(\w+)\.)?(\w+)`?",
        re.IGNORECASE,
    )

    _TABLE_REF_IN_BODY_RE = re.compile(
        r"(?:FROM|JOIN)\s+`?(?:(\w+)\.)?(\w+)`?",
        re.IGNORECASE,
    )

    # SQL keywords that should not be treated as column names
    _SQL_KEYWORDS = frozenset({
        "primary", "key", "foreign", "unique", "constraint", "check",
        "index", "references", "not", "null", "default", "auto_increment",
        "serial", "on", "delete", "update", "cascade", "set", "restrict",
        "no", "action", "create", "table", "alter", "add", "drop",
    })

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()
        table_name_to_id: dict[str, UUID] = {}

        file_id = uuid4()
        ops.append(self._add_node(
            "file", file_path.split("/")[-1].split("\\")[-1],
            file_path=file_path, language="sql", node_id=file_id,
        ))
        scope.add(file_id)

        # ── CREATE TABLE ──────────────────────────────────────────────
        for m in self._CREATE_TABLE_RE.finditer(source):
            schema_name = m.group(1) or ""
            table_name = m.group(2)
            body = m.group(3)
            line = source[:m.start()].count("\n") + 1

            table_id = uuid4()
            qualified = f"{schema_name}.{table_name}" if schema_name else table_name
            table_name_to_id[table_name] = table_id

            # Extract columns
            columns: list[dict[str, str]] = []
            for col_m in self._COLUMN_RE.finditer(body):
                col_name = col_m.group(1).lower()
                if col_name not in self._SQL_KEYWORDS:
                    columns.append({
                        "name": col_m.group(1),
                        "type": col_m.group(2).strip(),
                    })

            ops.append(self._add_node(
                "table", qualified, file_path=file_path,
                start_line=line, language="sql", node_id=table_id,
                schema=schema_name, columns=columns,
            ))
            scope.add(table_id)
            ops.append(self._add_edge(file_id, table_id, "contains"))

            # Inline foreign keys
            for fk_m in self._FK_RE.finditer(body):
                ref_table = fk_m.group(3)
                ref_id = table_name_to_id.get(ref_table)
                if ref_id is None:
                    ref_id = uuid4()
                    table_name_to_id[ref_table] = ref_id
                    ops.append(self._add_node(
                        "table", ref_table, file_path=file_path,
                        language="sql", node_id=ref_id,
                    ))
                    scope.add(ref_id)
                ops.append(self._add_edge(
                    table_id, ref_id, "references",
                    fk_column=fk_m.group(1), ref_column=fk_m.group(4),
                ))

        # ── CREATE INDEX ──────────────────────────────────────────────
        for m in self._CREATE_INDEX_RE.finditer(source):
            idx_name = m.group(1)
            target_table = m.group(3)
            idx_columns = m.group(4).strip()
            line = source[:m.start()].count("\n") + 1

            idx_id = uuid4()
            ops.append(self._add_node(
                "index", idx_name, file_path=file_path,
                start_line=line, language="sql", node_id=idx_id,
                target_table=target_table, indexed_columns=idx_columns,
            ))
            scope.add(idx_id)
            ops.append(self._add_edge(file_id, idx_id, "contains"))

            tbl_id = table_name_to_id.get(target_table)
            if tbl_id:
                ops.append(self._add_edge(idx_id, tbl_id, "indexes"))

        # ── CREATE VIEW ───────────────────────────────────────────────
        for m in self._CREATE_VIEW_RE.finditer(source):
            schema_name = m.group(1) or ""
            view_name = m.group(2)
            view_body = m.group(3)
            line = source[:m.start()].count("\n") + 1

            view_id = uuid4()
            qualified = f"{schema_name}.{view_name}" if schema_name else view_name
            ops.append(self._add_node(
                "view", qualified, file_path=file_path,
                start_line=line, language="sql", node_id=view_id,
                schema=schema_name,
            ))
            scope.add(view_id)
            ops.append(self._add_edge(file_id, view_id, "contains"))

            # View depends on tables referenced in its body
            for ref_m in self._TABLE_REF_IN_BODY_RE.finditer(view_body):
                ref_table = ref_m.group(2)
                tbl_id = table_name_to_id.get(ref_table)
                if tbl_id:
                    ops.append(self._add_edge(view_id, tbl_id, "depends_on"))

        # ── CREATE FUNCTION / PROCEDURE ───────────────────────────────
        for m in self._CREATE_FUNC_RE.finditer(source):
            schema_name = m.group(1) or ""
            func_name = m.group(2)
            line = source[:m.start()].count("\n") + 1

            func_id = uuid4()
            qualified = f"{schema_name}.{func_name}" if schema_name else func_name

            # Extract the function body up to the next CREATE or end of file
            func_start = m.end()
            next_create = re.search(r"\bCREATE\b", source[func_start:], re.IGNORECASE)
            func_body = source[func_start:func_start + next_create.start()] if next_create else source[func_start:]

            ops.append(self._add_node(
                "function", qualified, file_path=file_path,
                start_line=line, language="sql", node_id=func_id,
                schema=schema_name,
            ))
            scope.add(func_id)
            ops.append(self._add_edge(file_id, func_id, "contains"))

            # Function accesses tables referenced in its body
            for ref_m in self._TABLE_REF_IN_BODY_RE.finditer(func_body):
                ref_table = ref_m.group(2)
                tbl_id = table_name_to_id.get(ref_table)
                if tbl_id:
                    ops.append(self._add_edge(func_id, tbl_id, "accesses"))

        # ── ALTER TABLE (FK additions) ────────────────────────────────
        for m in self._ALTER_TABLE_FK_RE.finditer(source):
            src_table = m.group(2)
            fk_col = m.group(3)
            ref_table = m.group(5)
            ref_col = m.group(6)

            src_id = table_name_to_id.get(src_table)
            ref_id = table_name_to_id.get(ref_table)

            if src_id is None:
                src_id = uuid4()
                table_name_to_id[src_table] = src_id
                ops.append(self._add_node(
                    "table", src_table, file_path=file_path,
                    language="sql", node_id=src_id,
                ))
                scope.add(src_id)

            if ref_id is None:
                ref_id = uuid4()
                table_name_to_id[ref_table] = ref_id
                ops.append(self._add_node(
                    "table", ref_table, file_path=file_path,
                    language="sql", node_id=ref_id,
                ))
                scope.add(ref_id)

            ops.append(self._add_edge(
                src_id, ref_id, "references",
                fk_column=fk_col, ref_column=ref_col,
            ))

        if not ops:
            return []

        logger.debug(
            "sql_analysis_complete", file_path=file_path,
            tables=len(table_name_to_id), operations=len(ops),
        )
        return [self._make_delta(ops, file_path, scope)]
