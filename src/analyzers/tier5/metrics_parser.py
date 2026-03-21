"""Metrics Parser: extracts metric definitions from Prometheus exposition format.

Parses ``# HELP``, ``# TYPE``, and metric sample lines from the
Prometheus text-based exposition format.  This is a Tier 5 standalone
parser that does NOT inherit from BaseAnalyzer.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import structlog

from src.core.fact import CURRENT_SCHEMA_VERSION, AddEdge, AddNode, GraphDelta

logger = structlog.get_logger(__name__)


class MetricsParser:
    """Parses Prometheus exposition-format text and emits GraphDelta objects.

    Produces ``metric`` nodes with name, type, and help text.  Each metric
    is linked to a synthetic ``file`` node via a ``defines`` edge.
    """

    # ── Regex patterns ────────────────────────────────────────────────────

    # # HELP <metric_name> <docstring>
    _HELP_RE = re.compile(r"^#\s+HELP\s+(\S+)\s+(.+)$", re.MULTILINE)

    # # TYPE <metric_name> <type>
    _TYPE_RE = re.compile(r"^#\s+TYPE\s+(\S+)\s+(\S+)$", re.MULTILINE)

    # Metric sample: name{labels} value [timestamp]
    # Also handles names without labels: name value [timestamp]
    _SAMPLE_RE = re.compile(
        r"^(?!#)(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
        r"(?:\{(?P<labels>[^}]*)\})?\s+"
        r"(?P<value>\S+)"
        r"(?:\s+(?P<timestamp>\S+))?$",
        re.MULTILINE,
    )

    # Individual label: key="value"
    _LABEL_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')

    def parse(self, raw: str, source_label: str = "") -> list[GraphDelta]:
        """Parse Prometheus exposition-format text and return graph deltas.

        Args:
            raw: Raw Prometheus metrics text.
            source_label: Optional label for the data source.

        Returns:
            List of GraphDelta objects.
        """
        if not raw or not raw.strip():
            return []

        ops: list = []
        scope: set[UUID] = set()

        # Create a synthetic file node
        file_id = uuid4()
        ops.append(AddNode(
            node_id=file_id,
            node_type="file",
            attributes={
                "name": source_label or "metrics_input",
                "parser": "metrics",
            },
        ))
        scope.add(file_id)

        # ── Collect HELP and TYPE metadata ────────────────────────────
        help_map: dict[str, str] = {}
        type_map: dict[str, str] = {}

        for m in self._HELP_RE.finditer(raw):
            help_map[m.group(1)] = m.group(2)

        for m in self._TYPE_RE.finditer(raw):
            type_map[m.group(1)] = m.group(2)

        # ── Collect all unique metric base names from samples ─────────
        # Prometheus convention: histogram/summary have _bucket, _sum,
        # _count, _total suffixes.  We group them under the base name.
        seen_metrics: dict[str, UUID] = {}
        sample_counts: dict[str, int] = {}
        sample_labels: dict[str, set[str]] = {}

        for m in self._SAMPLE_RE.finditer(raw):
            metric_name = m.group("name")
            labels_raw = m.group("labels") or ""

            # Determine base metric name (strip known suffixes)
            base_name = self._base_metric_name(metric_name)

            if base_name not in sample_counts:
                sample_counts[base_name] = 0
                sample_labels[base_name] = set()

            sample_counts[base_name] += 1

            # Collect label keys
            for lm in self._LABEL_RE.finditer(labels_raw):
                sample_labels[base_name].add(lm.group(1))

        # ── Emit metric nodes ─────────────────────────────────────────
        for base_name in sorted(set(list(help_map.keys()) + list(type_map.keys()) + list(sample_counts.keys()))):
            if base_name in seen_metrics:
                continue

            metric_id = uuid4()
            seen_metrics[base_name] = metric_id

            metric_type = type_map.get(base_name, "untyped")
            help_text = help_map.get(base_name, "")
            label_keys = sorted(sample_labels.get(base_name, set()))
            count = sample_counts.get(base_name, 0)

            ops.append(AddNode(
                node_id=metric_id,
                node_type="metric",
                attributes={
                    "name": base_name,
                    "metric_type": metric_type,
                    "help": help_text,
                    "label_keys": label_keys,
                    "sample_count": count,
                    "parser": "metrics",
                },
            ))
            scope.add(metric_id)

            ops.append(AddEdge(
                src_id=file_id,
                tgt_id=metric_id,
                edge_type="defines",
                attributes={"source_analyzer": "metrics"},
            ))

        if len(seen_metrics) == 0:
            return []

        logger.debug(
            "metrics_parse_complete", source=source_label,
            metrics=len(seen_metrics),
        )
        return [GraphDelta(
            sequence_number=0,
            source="parser:metrics",
            operations=ops,
            scope=scope,
            schema_version=CURRENT_SCHEMA_VERSION,
        )]

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _base_metric_name(name: str) -> str:
        """Strip known Prometheus suffixes to get the base metric name.

        For example:
            http_requests_total         -> http_requests_total  (total is the real name)
            http_request_duration_bucket -> http_request_duration
            http_request_duration_sum    -> http_request_duration
            http_request_duration_count  -> http_request_duration
        """
        for suffix in ("_bucket", "_sum", "_count", "_created", "_info"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name
