"""Terraform / HCL Analyzer: regex-based extraction of IaC constructs.

Parses: resource, data, variable, output, module, provider blocks.
Emits terraform_resource, terraform_variable, terraform_output,
terraform_module nodes and their relationships.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)

# ── HCL block regexes ───────────────────────────────────────────────────────

# resource "aws_instance" "web" { ... }
_RESOURCE_RE = re.compile(
    r'^(\s*)resource\s+"([^"]+)"\s+"([^"]+)"\s*\{',
    re.MULTILINE,
)

# data "aws_ami" "ubuntu" { ... }
_DATA_RE = re.compile(
    r'^(\s*)data\s+"([^"]+)"\s+"([^"]+)"\s*\{',
    re.MULTILINE,
)

# variable "instance_type" { ... }
_VARIABLE_RE = re.compile(
    r'^(\s*)variable\s+"([^"]+)"\s*\{',
    re.MULTILINE,
)

# output "ip_address" { ... }
_OUTPUT_RE = re.compile(
    r'^(\s*)output\s+"([^"]+)"\s*\{',
    re.MULTILINE,
)

# module "vpc" { source = "..." ... }
_MODULE_RE = re.compile(
    r'^(\s*)module\s+"([^"]+)"\s*\{',
    re.MULTILINE,
)

# provider "aws" { ... }
_PROVIDER_RE = re.compile(
    r'^(\s*)provider\s+"([^"]+)"\s*\{',
    re.MULTILINE,
)

# var.<name> or module.<name> references inside a block body
_VAR_REF_RE = re.compile(r"\bvar\.(\w+)")
_MODULE_REF_RE = re.compile(r"\bmodule\.(\w+)")
_DATA_REF_RE = re.compile(r"\bdata\.(\w+)\.(\w+)")
_RESOURCE_REF_RE = re.compile(r"\b(\w+)\.(\w+)\.\w+")

# source = "..." inside module blocks
_MODULE_SOURCE_RE = re.compile(r'source\s*=\s*"([^"]+)"')


class TerraformAnalyzer(BaseAnalyzer):
    """Regex-based Terraform/HCL configuration analyzer."""

    ANALYZER_ID = "terraform"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".tf", ".hcl"]

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        """Parse Terraform files and emit graph deltas."""
        ops: list = []
        scope: set[UUID] = set()

        # File node
        file_id = uuid4()
        ops.append(
            self._add_node(
                "file",
                Path(file_path).name,
                file_path=file_path,
                language="terraform",
                node_id=file_id,
            )
        )
        scope.add(file_id)

        # Track names -> UUIDs for reference edges
        var_ids: dict[str, UUID] = {}
        resource_ids: dict[str, UUID] = {}
        data_ids: dict[str, UUID] = {}
        module_ids: dict[str, UUID] = {}

        # ── Resource blocks ──────────────────────────────────────────────
        for m in _RESOURCE_RE.finditer(source):
            res_type = m.group(2)
            res_name = m.group(3)
            line = source[: m.start()].count("\n") + 1
            block_body = self._extract_block_body(source, m.end() - 1)

            nid = uuid4()
            resource_ids[f"{res_type}.{res_name}"] = nid
            ops.append(
                self._add_node(
                    "terraform_resource",
                    f"{res_type}.{res_name}",
                    file_path=file_path,
                    start_line=line,
                    language="terraform",
                    node_id=nid,
                    resource_type=res_type,
                    resource_name=res_name,
                    provider=res_type.split("_")[0] if "_" in res_type else res_type,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "defines"))

            # Collect variable references inside the block
            for vref in _VAR_REF_RE.finditer(block_body):
                var_name = vref.group(1)
                if var_name not in var_ids:
                    # Create a placeholder variable node (will be deduped later)
                    vid = uuid4()
                    var_ids[var_name] = vid
                    ops.append(
                        self._add_node(
                            "terraform_variable",
                            var_name,
                            file_path=file_path,
                            language="terraform",
                            node_id=vid,
                            variable_name=var_name,
                            inferred=True,
                        )
                    )
                    scope.add(vid)
                ops.append(self._add_edge(nid, var_ids[var_name], "references"))

        # ── Data blocks ──────────────────────────────────────────────────
        for m in _DATA_RE.finditer(source):
            data_type = m.group(2)
            data_name = m.group(3)
            line = source[: m.start()].count("\n") + 1

            nid = uuid4()
            data_ids[f"{data_type}.{data_name}"] = nid
            ops.append(
                self._add_node(
                    "terraform_resource",
                    f"data.{data_type}.{data_name}",
                    file_path=file_path,
                    start_line=line,
                    language="terraform",
                    node_id=nid,
                    resource_type=data_type,
                    resource_name=data_name,
                    block_type="data",
                    provider=data_type.split("_")[0] if "_" in data_type else data_type,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "defines"))

        # ── Variable blocks ──────────────────────────────────────────────
        for m in _VARIABLE_RE.finditer(source):
            var_name = m.group(2)
            line = source[: m.start()].count("\n") + 1
            block_body = self._extract_block_body(source, m.end() - 1)

            # Parse optional default, type, description
            default_val = self._extract_attr(block_body, "default")
            var_type = self._extract_attr(block_body, "type")
            description = self._extract_attr(block_body, "description")

            if var_name in var_ids:
                # Already created as inferred reference; skip duplicate
                continue

            nid = uuid4()
            var_ids[var_name] = nid
            ops.append(
                self._add_node(
                    "terraform_variable",
                    var_name,
                    file_path=file_path,
                    start_line=line,
                    language="terraform",
                    node_id=nid,
                    variable_name=var_name,
                    default_value=default_val,
                    var_type=var_type,
                    description=description,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "defines"))

        # ── Output blocks ────────────────────────────────────────────────
        for m in _OUTPUT_RE.finditer(source):
            out_name = m.group(2)
            line = source[: m.start()].count("\n") + 1
            block_body = self._extract_block_body(source, m.end() - 1)
            value_expr = self._extract_attr(block_body, "value")
            description = self._extract_attr(block_body, "description")

            nid = uuid4()
            ops.append(
                self._add_node(
                    "terraform_output",
                    out_name,
                    file_path=file_path,
                    start_line=line,
                    language="terraform",
                    node_id=nid,
                    output_name=out_name,
                    value_expression=value_expr,
                    description=description,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "defines"))

            # Link output to referenced variables
            for vref in _VAR_REF_RE.finditer(block_body):
                vname = vref.group(1)
                if vname in var_ids:
                    ops.append(self._add_edge(nid, var_ids[vname], "references"))

        # ── Module blocks ────────────────────────────────────────────────
        for m in _MODULE_RE.finditer(source):
            mod_name = m.group(2)
            line = source[: m.start()].count("\n") + 1
            block_body = self._extract_block_body(source, m.end() - 1)

            source_match = _MODULE_SOURCE_RE.search(block_body)
            mod_source = source_match.group(1) if source_match else ""

            nid = uuid4()
            module_ids[mod_name] = nid
            ops.append(
                self._add_node(
                    "terraform_module",
                    mod_name,
                    file_path=file_path,
                    start_line=line,
                    language="terraform",
                    node_id=nid,
                    module_name=mod_name,
                    module_source=mod_source,
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "defines"))

            # Module -> variable references
            for vref in _VAR_REF_RE.finditer(block_body):
                vname = vref.group(1)
                if vname in var_ids:
                    ops.append(self._add_edge(nid, var_ids[vname], "references"))

        # ── Provider blocks ──────────────────────────────────────────────
        for m in _PROVIDER_RE.finditer(source):
            provider_name = m.group(2)
            line = source[: m.start()].count("\n") + 1

            nid = uuid4()
            ops.append(
                self._add_node(
                    "terraform_resource",
                    f"provider.{provider_name}",
                    file_path=file_path,
                    start_line=line,
                    language="terraform",
                    node_id=nid,
                    resource_type="provider",
                    resource_name=provider_name,
                    block_type="provider",
                )
            )
            scope.add(nid)
            ops.append(self._add_edge(file_id, nid, "defines"))

        if len(ops) <= 1:
            return []

        logger.debug(
            "terraform_analysis_complete",
            file_path=file_path,
            node_count=len([o for o in ops if getattr(o, "op", "") == "add_node"]),
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_block_body(source: str, brace_pos: int) -> str:
        """Extract the body of an HCL block starting at the opening brace."""
        if brace_pos >= len(source) or source[brace_pos] != "{":
            return ""
        depth = 0
        end = brace_pos
        for i in range(brace_pos, len(source)):
            ch = source[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        return source[brace_pos + 1 : end]

    @staticmethod
    def _extract_attr(block_body: str, attr_name: str) -> str:
        """Extract a simple attribute value from an HCL block body."""
        pattern = re.compile(
            rf'{attr_name}\s*=\s*"([^"]*)"'
            rf"|{attr_name}\s*=\s*(\S+)"
        )
        m = pattern.search(block_body)
        if m:
            return m.group(1) or m.group(2) or ""
        return ""
