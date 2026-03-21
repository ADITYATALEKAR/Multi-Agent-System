"""Cloud Audit Parser: extracts events and resources from AWS CloudTrail JSON logs.

Parses the standard CloudTrail log format (``Records`` array) and
emits ``audit_event`` and ``cloud_resource`` nodes.  This is a Tier 5
standalone parser that does NOT inherit from BaseAnalyzer.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.core.fact import CURRENT_SCHEMA_VERSION, AddEdge, AddNode, GraphDelta

logger = structlog.get_logger(__name__)


class CloudAuditParser:
    """Parses AWS CloudTrail JSON audit logs and emits GraphDelta objects.

    Expects a JSON object with a top-level ``Records`` key containing an
    array of CloudTrail event objects.  Each record produces an
    ``audit_event`` node and any referenced resources produce
    ``cloud_resource`` nodes linked via ``accesses`` edges.
    """

    def parse(self, raw: str, source_label: str = "") -> list[GraphDelta]:
        """Parse raw CloudTrail JSON and return graph deltas.

        Args:
            raw: Raw JSON content.
            source_label: Optional label for the data source.

        Returns:
            List of GraphDelta objects.  Returns ``[]`` on parse failure
            or if no records are found.
        """
        if not raw or not raw.strip():
            return []

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.debug("cloudtrail_json_parse_failed", error=str(exc))
            return []

        records = self._extract_records(data)
        if not records:
            logger.debug("cloudtrail_no_records", source=source_label)
            return []

        ops: list = []
        scope: set[UUID] = set()
        resource_arn_to_id: dict[str, UUID] = {}

        for record in records:
            if not isinstance(record, dict):
                continue

            event_name = str(record.get("eventName", ""))
            event_source = str(record.get("eventSource", ""))
            event_time = str(record.get("eventTime", ""))
            event_id_str = str(record.get("eventID", ""))
            aws_region = str(record.get("awsRegion", ""))
            source_ip = str(record.get("sourceIPAddress", ""))
            user_agent = str(record.get("userAgent", ""))
            error_code = str(record.get("errorCode", ""))
            error_message = str(record.get("errorMessage", ""))
            read_only = record.get("readOnly", None)

            # ── Extract principal ─────────────────────────────────────
            user_identity = record.get("userIdentity", {})
            principal_arn = ""
            principal_type = ""
            principal_name = ""
            if isinstance(user_identity, dict):
                principal_arn = str(user_identity.get("arn", ""))
                principal_type = str(user_identity.get("type", ""))
                principal_name = str(
                    user_identity.get("userName")
                    or user_identity.get("principalId")
                    or principal_arn
                )

            # ── Audit event node ──────────────────────────────────────
            event_node_id = uuid4()
            ops.append(AddNode(
                node_id=event_node_id,
                node_type="audit_event",
                attributes={
                    "name": f"{event_source}:{event_name}",
                    "event_name": event_name,
                    "event_source": event_source,
                    "event_time": event_time,
                    "event_id": event_id_str,
                    "aws_region": aws_region,
                    "source_ip": source_ip,
                    "user_agent": user_agent,
                    "principal_arn": principal_arn,
                    "principal_type": principal_type,
                    "principal_name": principal_name,
                    "error_code": error_code,
                    "error_message": error_message,
                    "read_only": read_only,
                    "parser": "cloud_audit",
                },
            ))
            scope.add(event_node_id)

            # ── Extract resources ─────────────────────────────────────
            resources = record.get("resources", [])
            if not isinstance(resources, list):
                resources = []

            # Also attempt to extract resource ARNs from requestParameters
            request_params = record.get("requestParameters", {})
            extra_arns = self._extract_arns_from_params(request_params)

            # Combine explicit resources with inferred ones
            all_resource_arns: list[tuple[str, str]] = []  # (arn, type)

            for res in resources:
                if isinstance(res, dict):
                    arn = str(res.get("ARN", res.get("arn", "")))
                    rtype = str(res.get("type", res.get("resourceType", "")))
                    if arn:
                        all_resource_arns.append((arn, rtype))

            for arn in extra_arns:
                if arn and not any(a == arn for a, _ in all_resource_arns):
                    all_resource_arns.append((arn, ""))

            for arn, rtype in all_resource_arns:
                resource_id = resource_arn_to_id.get(arn)
                if resource_id is None:
                    resource_id = uuid4()
                    resource_arn_to_id[arn] = resource_id

                    # Derive a short name from the ARN
                    resource_name = arn.rsplit("/", 1)[-1] if "/" in arn else arn.rsplit(":", 1)[-1]

                    ops.append(AddNode(
                        node_id=resource_id,
                        node_type="cloud_resource",
                        attributes={
                            "name": resource_name,
                            "arn": arn,
                            "resource_type": rtype,
                            "parser": "cloud_audit",
                        },
                    ))
                    scope.add(resource_id)

                ops.append(AddEdge(
                    src_id=event_node_id,
                    tgt_id=resource_id,
                    edge_type="accesses",
                    attributes={"source_analyzer": "cloud_audit"},
                ))

        if not ops:
            return []

        logger.debug(
            "cloudtrail_parse_complete", source=source_label,
            events=len([o for o in ops if isinstance(o, AddNode) and o.node_type == "audit_event"]),
            resources=len(resource_arn_to_id),
        )
        return [GraphDelta(
            sequence_number=0,
            source="parser:cloud_audit",
            operations=ops,
            scope=scope,
            schema_version=CURRENT_SCHEMA_VERSION,
        )]

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_records(data: Any) -> list[dict[str, Any]]:
        """Normalise various CloudTrail envelope shapes into a list of records."""
        if isinstance(data, dict):
            records = data.get("Records")
            if isinstance(records, list):
                return records
            # Single event object (has eventName)
            if "eventName" in data:
                return [data]
        elif isinstance(data, list):
            # Array of records directly
            return data
        return []

    @staticmethod
    def _extract_arns_from_params(params: Any) -> list[str]:
        """Recursively extract ARN-like strings from request parameters."""
        arns: list[str] = []
        if not isinstance(params, (dict, list)):
            return arns

        def _walk(node: Any) -> None:
            if isinstance(node, str):
                if node.startswith("arn:aws:"):
                    arns.append(node)
            elif isinstance(node, dict):
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(params)
        return arns
