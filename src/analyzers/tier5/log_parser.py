"""Log Parser: extracts structured log events from common log formats.

Supports syslog, JSON logs, Apache/Nginx access logs, and generic
application log formats.  This is a Tier 5 standalone parser that
does NOT inherit from BaseAnalyzer.
"""

from __future__ import annotations

import json
import re
from uuid import UUID, uuid4

import structlog

from src.core.fact import CURRENT_SCHEMA_VERSION, AddEdge, AddNode, GraphDelta

logger = structlog.get_logger(__name__)


class LogParser:
    """Parses log files and emits GraphDelta objects.

    Recognises several common formats and extracts timestamp, level,
    source, and message from each log line.  Related events are grouped
    under ``log_source`` nodes.
    """

    # ── Regex patterns for common log formats ─────────────────────────────

    # Syslog: "Mar 15 14:23:01 hostname process[pid]: message"
    _SYSLOG_RE = re.compile(
        r"^(?P<timestamp>\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"
        r"(?P<host>\S+)\s+"
        r"(?P<source>\S+?)(?:\[(?P<pid>\d+)\])?\s*:\s+"
        r"(?P<message>.+)$",
        re.MULTILINE,
    )

    # Apache / Nginx combined access log:
    # 127.0.0.1 - user [10/Oct/2000:13:55:36 -0700] "GET /foo HTTP/1.0" 200 2326 ...
    _ACCESS_LOG_RE = re.compile(
        r'^(?P<ip>\S+)\s+\S+\s+\S+\s+'
        r'\[(?P<timestamp>[^\]]+)\]\s+'
        r'"(?P<method>\w+)\s+(?P<path>\S+)\s+\S+"\s+'
        r'(?P<status>\d{3})\s+(?P<size>\d+|-)',
        re.MULTILINE,
    )

    # Generic application log: "2024-01-15 14:23:01.123 [LEVEL] source - message"
    _APP_LOG_RE = re.compile(
        r"^(?P<timestamp>\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
        r"(?:\[?\s*(?P<level>TRACE|DEBUG|INFO|WARN(?:ING)?|ERROR|FATAL|CRITICAL)\s*\]?)\s+"
        r"(?:(?P<source>\S+)\s+[-:]\s+)?"
        r"(?P<message>.+)$",
        re.MULTILINE | re.IGNORECASE,
    )

    # ISO-prefix log without explicit level: "2024-01-15T14:23:01Z source: message"
    _ISO_LOG_RE = re.compile(
        r"^(?P<timestamp>\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+"
        r"(?P<source>\S+?):\s+"
        r"(?P<message>.+)$",
        re.MULTILINE,
    )

    def parse(self, raw: str, source_label: str = "") -> list[GraphDelta]:
        """Parse raw log content and return graph deltas.

        Args:
            raw: Raw log file content.
            source_label: Optional label for the log source (e.g. filename).

        Returns:
            List of GraphDelta objects.
        """
        if not raw or not raw.strip():
            return []

        ops: list = []
        scope: set[UUID] = set()
        source_name_to_id: dict[str, UUID] = {}

        file_id = uuid4()
        ops.append(AddNode(
            node_id=file_id,
            node_type="file",
            attributes={
                "name": source_label or "log_input",
                "parser": "log",
            },
        ))
        scope.add(file_id)

        events_found = 0

        # ── Try JSON lines (one JSON object per line) ─────────────────
        events_found += self._parse_json_lines(raw, ops, scope, source_name_to_id)

        # ── Syslog ────────────────────────────────────────────────────
        if events_found == 0:
            events_found += self._parse_with_pattern(
                raw, self._SYSLOG_RE, ops, scope, source_name_to_id,
                default_level="INFO",
            )

        # ── Apache / Nginx access log ─────────────────────────────────
        if events_found == 0:
            events_found += self._parse_access_log(raw, ops, scope, source_name_to_id)

        # ── Generic application log ───────────────────────────────────
        if events_found == 0:
            events_found += self._parse_with_pattern(
                raw, self._APP_LOG_RE, ops, scope, source_name_to_id,
            )

        # ── Fallback: ISO timestamp log ───────────────────────────────
        if events_found == 0:
            events_found += self._parse_with_pattern(
                raw, self._ISO_LOG_RE, ops, scope, source_name_to_id,
                default_level="INFO",
            )

        # Link log sources to file
        for src_id in source_name_to_id.values():
            ops.append(AddEdge(
                src_id=file_id,
                tgt_id=src_id,
                edge_type="contains",
                attributes={"source_analyzer": "log"},
            ))

        if events_found == 0:
            return []

        logger.debug("log_parse_complete", source=source_label, events=events_found)
        return [GraphDelta(
            sequence_number=0,
            source="parser:log",
            operations=ops,
            scope=scope,
            schema_version=CURRENT_SCHEMA_VERSION,
        )]

    # ── Internal parse helpers ────────────────────────────────────────────

    def _parse_json_lines(
        self,
        raw: str,
        ops: list,
        scope: set[UUID],
        source_map: dict[str, UUID],
    ) -> int:
        """Attempt to parse log lines as newline-delimited JSON."""
        count = 0
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue

            timestamp = str(
                obj.get("timestamp")
                or obj.get("ts")
                or obj.get("@timestamp")
                or obj.get("time")
                or ""
            )
            level = str(
                obj.get("level")
                or obj.get("severity")
                or obj.get("loglevel")
                or "INFO"
            ).upper()
            source = str(
                obj.get("logger")
                or obj.get("source")
                or obj.get("name")
                or obj.get("service")
                or "unknown"
            )
            message = str(
                obj.get("message")
                or obj.get("msg")
                or obj.get("text")
                or ""
            )

            self._emit_event(
                ops, scope, source_map,
                timestamp=timestamp, level=level,
                source=source, message=message,
            )
            count += 1
        return count

    def _parse_access_log(
        self,
        raw: str,
        ops: list,
        scope: set[UUID],
        source_map: dict[str, UUID],
    ) -> int:
        """Parse Apache / Nginx combined access log format."""
        count = 0
        for m in self._ACCESS_LOG_RE.finditer(raw):
            ip = m.group("ip")
            timestamp = m.group("timestamp")
            method = m.group("method")
            path = m.group("path")
            status = m.group("status")
            size = m.group("size")

            message = f"{method} {path} -> {status} ({size} bytes)"
            # Determine level from status code
            status_int = int(status)
            if status_int >= 500:
                level = "ERROR"
            elif status_int >= 400:
                level = "WARN"
            else:
                level = "INFO"

            self._emit_event(
                ops, scope, source_map,
                timestamp=timestamp, level=level,
                source=ip, message=message,
                http_method=method, http_path=path,
                http_status=status, http_size=size,
            )
            count += 1
        return count

    def _parse_with_pattern(
        self,
        raw: str,
        pattern: re.Pattern[str],
        ops: list,
        scope: set[UUID],
        source_map: dict[str, UUID],
        default_level: str = "",
    ) -> int:
        """Parse log lines using a given regex pattern."""
        count = 0
        for m in pattern.finditer(raw):
            groups = m.groupdict()
            timestamp = groups.get("timestamp", "")
            level = (groups.get("level") or default_level or "INFO").upper()
            source = groups.get("source") or groups.get("host") or "unknown"
            message = groups.get("message", "")

            self._emit_event(
                ops, scope, source_map,
                timestamp=timestamp, level=level,
                source=source, message=message,
            )
            count += 1
        return count

    def _emit_event(
        self,
        ops: list,
        scope: set[UUID],
        source_map: dict[str, UUID],
        *,
        timestamp: str,
        level: str,
        source: str,
        message: str,
        **extra_attrs: str,
    ) -> None:
        """Create a log_event node and link it to its log_source."""
        # Get or create log_source node
        src_id = source_map.get(source)
        if src_id is None:
            src_id = uuid4()
            source_map[source] = src_id
            ops.append(AddNode(
                node_id=src_id,
                node_type="log_source",
                attributes={
                    "name": source,
                    "parser": "log",
                },
            ))
            scope.add(src_id)

        event_id = uuid4()
        attrs = {
            "name": message[:120] if message else "log_event",
            "timestamp": timestamp,
            "level": level,
            "source": source,
            "message": message,
            "parser": "log",
        }
        attrs.update(extra_attrs)

        ops.append(AddNode(
            node_id=event_id,
            node_type="log_event",
            attributes=attrs,
        ))
        scope.add(event_id)

        ops.append(AddEdge(
            src_id=src_id,
            tgt_id=event_id,
            edge_type="emits",
            attributes={"source_analyzer": "log"},
        ))
