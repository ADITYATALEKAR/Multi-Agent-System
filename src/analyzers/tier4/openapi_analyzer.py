"""OpenAPI Analyzer: parses OpenAPI/Swagger specs and extracts endpoints, schemas, and security."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)

# yaml is optional; we try to import it and fall back gracefully.
try:
    import yaml  # type: ignore[import-untyped]

    _HAS_YAML = True
except ImportError:  # pragma: no cover
    _HAS_YAML = False


class OpenAPIAnalyzer(BaseAnalyzer):
    """Analyzes OpenAPI / Swagger specification files.

    Supports YAML (.yaml, .yml) and JSON (.json) formats.
    Detects OpenAPI documents by looking for an ``openapi`` or ``swagger``
    top-level key.  Returns an empty list when the file is not an OpenAPI
    document.
    """

    ANALYZER_ID = "openapi"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".yaml", ".yml", ".json"]

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        doc = self._parse_document(source, file_path)
        if doc is None:
            return []

        # Verify this is actually an OpenAPI / Swagger document
        if not isinstance(doc, dict):
            return []
        if "openapi" not in doc and "swagger" not in doc:
            logger.debug("not_openapi_document", file_path=file_path)
            return []

        ops: list = []
        scope: set[UUID] = set()
        schema_name_to_id: dict[str, UUID] = {}

        file_id = uuid4()
        api_version = doc.get("openapi") or doc.get("swagger") or ""
        info = doc.get("info", {})
        api_title = info.get("title", "") if isinstance(info, dict) else ""

        ops.append(self._add_node(
            "file", file_path.split("/")[-1].split("\\")[-1],
            file_path=file_path, language="openapi", node_id=file_id,
            api_version=api_version, api_title=api_title,
        ))
        scope.add(file_id)

        # ── Schemas / Definitions ─────────────────────────────────────
        schemas = self._get_schemas(doc)
        for schema_name, schema_def in schemas.items():
            schema_id = uuid4()
            schema_name_to_id[schema_name] = schema_id

            schema_type = ""
            properties: list[str] = []
            required_fields: list[str] = []
            if isinstance(schema_def, dict):
                schema_type = schema_def.get("type", "object")
                props = schema_def.get("properties", {})
                if isinstance(props, dict):
                    properties = list(props.keys())
                req = schema_def.get("required", [])
                if isinstance(req, list):
                    required_fields = req

            ops.append(self._add_node(
                "api_schema", schema_name, file_path=file_path,
                language="openapi", node_id=schema_id,
                schema_type=schema_type, properties=properties,
                required_fields=required_fields,
            ))
            scope.add(schema_id)
            ops.append(self._add_edge(file_id, schema_id, "contains"))

        # ── Security Schemes ──────────────────────────────────────────
        security_name_to_id: dict[str, UUID] = {}
        sec_schemes = self._get_security_schemes(doc)
        for sec_name, sec_def in sec_schemes.items():
            sec_id = uuid4()
            security_name_to_id[sec_name] = sec_id

            sec_type = ""
            sec_in = ""
            if isinstance(sec_def, dict):
                sec_type = sec_def.get("type", "")
                sec_in = sec_def.get("in", "")

            ops.append(self._add_node(
                "api_security_scheme", sec_name, file_path=file_path,
                language="openapi", node_id=sec_id,
                security_type=sec_type, security_in=sec_in,
            ))
            scope.add(sec_id)
            ops.append(self._add_edge(file_id, sec_id, "contains"))

        # ── Paths / Endpoints ─────────────────────────────────────────
        paths = doc.get("paths", {})
        if isinstance(paths, dict):
            for path, path_item in paths.items():
                if not isinstance(path_item, dict):
                    continue

                for method in (
                    "get", "post", "put", "patch", "delete",
                    "head", "options", "trace",
                ):
                    operation = path_item.get(method)
                    if not isinstance(operation, dict):
                        continue

                    endpoint_id = uuid4()
                    op_id = operation.get("operationId", "")
                    summary = operation.get("summary", "")
                    tags = operation.get("tags", [])
                    if not isinstance(tags, list):
                        tags = []

                    endpoint_name = op_id if op_id else f"{method.upper()} {path}"
                    ops.append(self._add_node(
                        "api_endpoint", endpoint_name, file_path=file_path,
                        language="openapi", node_id=endpoint_id,
                        path=path, method=method.upper(),
                        operation_id=op_id, summary=summary, tags=tags,
                    ))
                    scope.add(endpoint_id)
                    ops.append(self._add_edge(file_id, endpoint_id, "contains"))

                    # Endpoint -> Schema references
                    referenced_schemas = self._collect_schema_refs(operation)
                    for ref_name in referenced_schemas:
                        ref_id = schema_name_to_id.get(ref_name)
                        if ref_id:
                            ops.append(self._add_edge(endpoint_id, ref_id, "uses"))

                    # Endpoint -> Security requirements
                    security = operation.get("security", [])
                    if not isinstance(security, list):
                        security = []
                    for sec_req in security:
                        if isinstance(sec_req, dict):
                            for sec_name in sec_req:
                                sec_id = security_name_to_id.get(sec_name)
                                if sec_id:
                                    ops.append(self._add_edge(
                                        endpoint_id, sec_id, "requires",
                                    ))

                    # Also apply global security if endpoint has none
                    if not security:
                        global_security = doc.get("security", [])
                        if isinstance(global_security, list):
                            for sec_req in global_security:
                                if isinstance(sec_req, dict):
                                    for sec_name in sec_req:
                                        sec_id = security_name_to_id.get(sec_name)
                                        if sec_id:
                                            ops.append(self._add_edge(
                                                endpoint_id, sec_id, "requires",
                                            ))

        if not ops:
            return []

        logger.debug(
            "openapi_analysis_complete", file_path=file_path, operations=len(ops),
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── Helpers ───────────────────────────────────────────────────────────

    def _parse_document(self, source: str, file_path: str) -> dict[str, Any] | None:
        """Parse the document as YAML or JSON depending on extension."""
        lower_path = file_path.lower()

        if lower_path.endswith(".json"):
            try:
                return json.loads(source)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.debug("json_parse_failed", file_path=file_path, error=str(exc))
                return None

        # YAML files (.yaml, .yml)
        if _HAS_YAML:
            try:
                return yaml.safe_load(source)
            except yaml.YAMLError as exc:
                logger.debug("yaml_parse_failed", file_path=file_path, error=str(exc))
                return None
        else:
            # Fallback: try JSON (some .yaml files are actually JSON)
            try:
                return json.loads(source)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "yaml_not_available",
                    file_path=file_path,
                    hint="Install PyYAML for YAML support",
                )
                return None

    @staticmethod
    def _get_schemas(doc: dict[str, Any]) -> dict[str, Any]:
        """Extract schema definitions from an OpenAPI / Swagger doc."""
        # OpenAPI 3.x
        components = doc.get("components", {})
        if isinstance(components, dict):
            schemas = components.get("schemas", {})
            if isinstance(schemas, dict) and schemas:
                return schemas

        # Swagger 2.x
        definitions = doc.get("definitions", {})
        if isinstance(definitions, dict):
            return definitions

        return {}

    @staticmethod
    def _get_security_schemes(doc: dict[str, Any]) -> dict[str, Any]:
        """Extract security scheme definitions."""
        # OpenAPI 3.x
        components = doc.get("components", {})
        if isinstance(components, dict):
            schemes = components.get("securitySchemes", {})
            if isinstance(schemes, dict) and schemes:
                return schemes

        # Swagger 2.x
        sec_defs = doc.get("securityDefinitions", {})
        if isinstance(sec_defs, dict):
            return sec_defs

        return {}

    @staticmethod
    def _collect_schema_refs(obj: Any) -> set[str]:
        """Recursively collect all $ref schema names from an operation object."""
        refs: set[str] = set()

        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str):
                    # Extract name from "#/components/schemas/Foo" or "#/definitions/Foo"
                    parts = ref.rsplit("/", 1)
                    if len(parts) == 2:
                        refs.add(parts[1])
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(obj)
        return refs
