"""OTLP Parser: extracts traces and spans from OpenTelemetry JSON data.

Parses OTLP JSON trace exports and constructs a graph of traces, spans,
and their parent-child relationships.  This is a Tier 5 standalone parser
that does NOT inherit from BaseAnalyzer.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.core.fact import CURRENT_SCHEMA_VERSION, AddEdge, AddNode, GraphDelta

logger = structlog.get_logger(__name__)


class OTLPParser:
    """Parses OpenTelemetry Protocol (OTLP) JSON traces and emits GraphDelta objects.

    Expects the standard OTLP JSON format with ``resourceSpans`` containing
    ``scopeSpans`` (or ``instrumentationLibrarySpans`` for older exporters)
    each containing a list of spans.

    Produces ``trace`` and ``span`` nodes with ``contains`` and ``child_of``
    edges reflecting the span hierarchy.
    """

    def parse(self, raw: str, source_label: str = "") -> list[GraphDelta]:
        """Parse raw OTLP JSON trace data and return graph deltas.

        Args:
            raw: Raw JSON content (single object or array).
            source_label: Optional label for the data source.

        Returns:
            List of GraphDelta objects.  Returns ``[]`` on parse failure
            or if no spans are found.
        """
        if not raw or not raw.strip():
            return []

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("otlp_json_parse_failed", error=str(exc))
            return []

        ops: list = []
        scope: set[UUID] = set()
        trace_id_map: dict[str, UUID] = {}   # OTLP traceId -> node UUID
        span_id_map: dict[str, UUID] = {}    # OTLP spanId -> node UUID
        parent_edges: list[tuple[str, str]] = []  # (child spanId, parent spanId)

        resource_spans_list = self._extract_resource_spans(data)
        if not resource_spans_list:
            logger.debug("otlp_no_resource_spans", source=source_label)
            return []

        for resource_spans in resource_spans_list:
            # Extract resource-level service name
            service_name = self._extract_service_name(resource_spans)

            scope_spans_list = (
                resource_spans.get("scopeSpans")
                or resource_spans.get("instrumentationLibrarySpans")
                or []
            )
            if not isinstance(scope_spans_list, list):
                continue

            for scope_spans in scope_spans_list:
                if not isinstance(scope_spans, dict):
                    continue

                spans = scope_spans.get("spans", [])
                if not isinstance(spans, list):
                    continue

                for span in spans:
                    if not isinstance(span, dict):
                        continue

                    trace_id_str = str(span.get("traceId", ""))
                    span_id_str = str(span.get("spanId", ""))
                    parent_span_id = str(span.get("parentSpanId", ""))
                    operation_name = str(
                        span.get("name")
                        or span.get("operationName")
                        or ""
                    )
                    kind = span.get("kind", "")
                    status = span.get("status", {})
                    status_code = ""
                    if isinstance(status, dict):
                        status_code = str(status.get("code", ""))

                    # Duration: OTLP uses startTimeUnixNano / endTimeUnixNano
                    start_ns = self._safe_int(span.get("startTimeUnixNano"))
                    end_ns = self._safe_int(span.get("endTimeUnixNano"))
                    duration_ns = (end_ns - start_ns) if (start_ns and end_ns) else 0

                    # Extract span attributes
                    span_attrs = self._extract_attributes(span.get("attributes", []))

                    # ── Trace node (one per unique traceId) ───────
                    if trace_id_str and trace_id_str not in trace_id_map:
                        trace_node_id = uuid4()
                        trace_id_map[trace_id_str] = trace_node_id
                        ops.append(AddNode(
                            node_id=trace_node_id,
                            node_type="trace",
                            attributes={
                                "name": f"trace-{trace_id_str[:12]}",
                                "trace_id": trace_id_str,
                                "service_name": service_name,
                                "parser": "otlp",
                            },
                        ))
                        scope.add(trace_node_id)

                    # ── Span node ─────────────────────────────────
                    span_node_id = uuid4()
                    if span_id_str:
                        span_id_map[span_id_str] = span_node_id

                    ops.append(AddNode(
                        node_id=span_node_id,
                        node_type="span",
                        attributes={
                            "name": operation_name or span_id_str,
                            "span_id": span_id_str,
                            "trace_id": trace_id_str,
                            "parent_span_id": parent_span_id,
                            "operation_name": operation_name,
                            "service_name": service_name,
                            "kind": str(kind),
                            "status_code": status_code,
                            "duration_ns": duration_ns,
                            "span_attributes": span_attrs,
                            "parser": "otlp",
                        },
                    ))
                    scope.add(span_node_id)

                    # ── trace -> span "contains" ──────────────────
                    trace_node_id = trace_id_map.get(trace_id_str)
                    if trace_node_id:
                        ops.append(AddEdge(
                            src_id=trace_node_id,
                            tgt_id=span_node_id,
                            edge_type="contains",
                            attributes={"source_analyzer": "otlp"},
                        ))

                    # Record parent relationship for later resolution
                    if parent_span_id and parent_span_id != "":
                        parent_edges.append((span_id_str, parent_span_id))

        # ── Resolve parent-child span edges ───────────────────────────────
        for child_sid, parent_sid in parent_edges:
            child_nid = span_id_map.get(child_sid)
            parent_nid = span_id_map.get(parent_sid)
            if child_nid and parent_nid:
                ops.append(AddEdge(
                    src_id=child_nid,
                    tgt_id=parent_nid,
                    edge_type="child_of",
                    attributes={"source_analyzer": "otlp"},
                ))

        if not ops:
            return []

        logger.debug(
            "otlp_parse_complete", source=source_label,
            traces=len(trace_id_map), spans=len(span_id_map),
        )
        return [GraphDelta(
            sequence_number=0,
            source="parser:otlp",
            operations=ops,
            scope=scope,
            schema_version=CURRENT_SCHEMA_VERSION,
        )]

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_resource_spans(data: Any) -> list[dict[str, Any]]:
        """Normalise various OTLP envelope shapes into a list of resourceSpans."""
        if isinstance(data, dict):
            rs = data.get("resourceSpans")
            if isinstance(rs, list):
                return rs
            # Single resourceSpan object
            if "resource" in data and ("scopeSpans" in data or "instrumentationLibrarySpans" in data):
                return [data]
            # Wrapped in a "data" key
            inner = data.get("data")
            if isinstance(inner, list):
                return inner
        elif isinstance(data, list):
            # Array of resourceSpans
            return data
        return []

    @staticmethod
    def _extract_service_name(resource_spans: dict[str, Any]) -> str:
        """Extract the service.name from resource attributes."""
        resource = resource_spans.get("resource", {})
        if not isinstance(resource, dict):
            return ""
        attrs = resource.get("attributes", [])
        if isinstance(attrs, list):
            for attr in attrs:
                if isinstance(attr, dict) and attr.get("key") == "service.name":
                    val = attr.get("value", {})
                    if isinstance(val, dict):
                        return str(val.get("stringValue", val.get("Value", "")))
                    return str(val)
        elif isinstance(attrs, dict):
            return str(attrs.get("service.name", ""))
        return ""

    @staticmethod
    def _extract_attributes(attrs: Any) -> dict[str, str]:
        """Convert OTLP attribute list to a simple dict."""
        result: dict[str, str] = {}
        if not isinstance(attrs, list):
            return result
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            key = attr.get("key", "")
            val = attr.get("value", {})
            if isinstance(val, dict):
                # OTLP wraps values in {stringValue: ...}, {intValue: ...}, etc.
                str_val = (
                    val.get("stringValue")
                    or val.get("intValue")
                    or val.get("boolValue")
                    or val.get("doubleValue")
                    or val.get("Value")
                    or ""
                )
                result[key] = str(str_val)
            else:
                result[key] = str(val)
        return result

    @staticmethod
    def _safe_int(val: Any) -> int:
        """Safely convert a value to int, returning 0 on failure."""
        if val is None:
            return 0
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0
