"""Docker Analyzer: regex-based Dockerfile instruction extraction.

Parses FROM, RUN, COPY, ADD, EXPOSE, ENV, WORKDIR, ENTRYPOINT, CMD,
LABEL, ARG, VOLUME instructions and emits graph nodes/edges.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)

# ── Instruction regexes ──────────────────────────────────────────────────────

# FROM [--platform=...] <image>[:<tag>|@<digest>] [AS <alias>]
_FROM_RE = re.compile(
    r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+?)(?:[:@]\S+)?\s*(?:AS\s+(\w+))?\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# EXPOSE <port>[/<proto>] ...
_EXPOSE_RE = re.compile(
    r"^\s*EXPOSE\s+(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# ENV <key>=<value> | ENV <key> <value>
_ENV_RE = re.compile(
    r"^\s*ENV\s+(\w+)[\s=]+(.*)$",
    re.MULTILINE | re.IGNORECASE,
)

# VOLUME ["/data"] or VOLUME /data /logs
_VOLUME_RE = re.compile(
    r"^\s*VOLUME\s+(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# WORKDIR /path
_WORKDIR_RE = re.compile(
    r"^\s*WORKDIR\s+(\S+)",
    re.MULTILINE | re.IGNORECASE,
)

# COPY [--from=...] <src>... <dest>
_COPY_RE = re.compile(
    r"^\s*COPY\s+(?:--from=(\S+)\s+)?(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# ADD <src>... <dest>
_ADD_RE = re.compile(
    r"^\s*ADD\s+(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# RUN <command>
_RUN_RE = re.compile(
    r"^\s*RUN\s+(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# ENTRYPOINT ["exec",...] | ENTRYPOINT exec
_ENTRYPOINT_RE = re.compile(
    r"^\s*ENTRYPOINT\s+(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# CMD ["exec",...] | CMD exec
_CMD_RE = re.compile(
    r"^\s*CMD\s+(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# LABEL <key>=<value> ...
_LABEL_RE = re.compile(
    r"^\s*LABEL\s+(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# ARG <name>[=<default>]
_ARG_RE = re.compile(
    r"^\s*ARG\s+(\w+)(?:=(.*))?$",
    re.MULTILINE | re.IGNORECASE,
)

# Port number extractor (from EXPOSE values)
_PORT_NUM_RE = re.compile(r"(\d+)(?:/(\w+))?")


class DockerAnalyzer(BaseAnalyzer):
    """Regex-based Dockerfile instruction analyzer."""

    ANALYZER_ID = "docker"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".Dockerfile"]

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        """Parse Dockerfile instructions and emit graph deltas."""
        ops: list = []
        scope: set[UUID] = set()

        # Collapse line continuations for easier parsing
        normalized = re.sub(r"\\\s*\n\s*", " ", source)

        # File node
        file_id = uuid4()
        ops.append(
            self._add_node(
                "file",
                Path(file_path).name,
                file_path=file_path,
                language="docker",
                node_id=file_id,
            )
        )
        scope.add(file_id)

        # FROM -> container_image nodes
        for m in _FROM_RE.finditer(normalized):
            image_name = m.group(1)
            alias = m.group(2) or ""
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "container_image",
                    image_name,
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    stage_alias=alias,
                    instruction="FROM",
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "uses"))

        # EXPOSE -> port nodes
        for m in _EXPOSE_RE.finditer(normalized):
            raw_ports = m.group(1).strip()
            line = normalized[: m.start()].count("\n") + 1
            for pm in _PORT_NUM_RE.finditer(raw_ports):
                port_num = pm.group(1)
                protocol = pm.group(2) or "tcp"
                nid = uuid4()
                ops.append(
                    self._add_node(
                        "port",
                        f"{port_num}/{protocol}",
                        file_path=file_path,
                        start_line=line,
                        language="docker",
                        node_id=nid,
                        port_number=int(port_num),
                        protocol=protocol,
                        instruction="EXPOSE",
                    )
                )
                scope.add(nid)
                ops.append(self._add_edge(file_id, nid, "exposes"))

        # ENV -> env_var nodes
        for m in _ENV_RE.finditer(normalized):
            key = m.group(1)
            value = m.group(2).strip().strip('"').strip("'")
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "env_var",
                    key,
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    env_value=value,
                    instruction="ENV",
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "defines"))

        # VOLUME -> volume nodes
        for m in _VOLUME_RE.finditer(normalized):
            raw = m.group(1).strip()
            line = normalized[: m.start()].count("\n") + 1
            # Parse JSON-array or space-separated volumes
            volumes = self._parse_volume_list(raw)
            for vol in volumes:
                nid = uuid4()
                ops.append(
                    self._add_node(
                        "volume",
                        vol,
                        file_path=file_path,
                        start_line=line,
                        language="docker",
                        node_id=nid,
                        mount_path=vol,
                        instruction="VOLUME",
                    )
                )
                scope.add(nid)
                ops.append(self._add_edge(file_id, nid, "mounts"))

        # ARG -> variable nodes
        for m in _ARG_RE.finditer(normalized):
            arg_name = m.group(1)
            default_val = (m.group(2) or "").strip()
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "variable",
                    arg_name,
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    default_value=default_val,
                    instruction="ARG",
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "defines"))

        # WORKDIR
        for m in _WORKDIR_RE.finditer(normalized):
            workdir = m.group(1)
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "variable",
                    f"WORKDIR:{workdir}",
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    workdir=workdir,
                    instruction="WORKDIR",
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "defines"))

        # COPY
        for m in _COPY_RE.finditer(normalized):
            copy_from = m.group(1) or ""
            args = m.group(2).strip()
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "docker_layer",
                    f"COPY:{args[:60]}",
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    copy_from_stage=copy_from,
                    instruction="COPY",
                    raw=args,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "contains"))

        # ADD
        for m in _ADD_RE.finditer(normalized):
            args = m.group(1).strip()
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "docker_layer",
                    f"ADD:{args[:60]}",
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    instruction="ADD",
                    raw=args,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "contains"))

        # RUN
        for m in _RUN_RE.finditer(normalized):
            cmd = m.group(1).strip()
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "docker_layer",
                    f"RUN:{cmd[:60]}",
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    instruction="RUN",
                    raw=cmd,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "contains"))

        # ENTRYPOINT
        for m in _ENTRYPOINT_RE.finditer(normalized):
            cmd = m.group(1).strip()
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "docker_layer",
                    f"ENTRYPOINT:{cmd[:60]}",
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    instruction="ENTRYPOINT",
                    raw=cmd,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "contains"))

        # CMD
        for m in _CMD_RE.finditer(normalized):
            cmd = m.group(1).strip()
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "docker_layer",
                    f"CMD:{cmd[:60]}",
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    instruction="CMD",
                    raw=cmd,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "contains"))

        # LABEL
        for m in _LABEL_RE.finditer(normalized):
            label_raw = m.group(1).strip()
            line = normalized[: m.start()].count("\n") + 1
            nid = uuid4()
            ops.append(
                self._add_node(
                    "docker_layer",
                    f"LABEL:{label_raw[:60]}",
                    file_path=file_path,
                    start_line=line,
                    language="docker",
                    node_id=nid,
                    instruction="LABEL",
                    raw=label_raw,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "contains"))

        if len(ops) <= 1:
            return []

        logger.debug(
            "docker_analysis_complete",
            file_path=file_path,
            node_count=(len(ops) - 1) // 2,
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_volume_list(raw: str) -> list[str]:
        """Parse VOLUME instruction value (JSON array or space-separated)."""
        raw = raw.strip()
        if raw.startswith("["):
            # JSON-style: ["/data", "/logs"]
            return [
                v.strip().strip('"').strip("'")
                for v in raw.strip("[]").split(",")
                if v.strip().strip('"').strip("'")
            ]
        # Space-separated
        return [v for v in raw.split() if v]
