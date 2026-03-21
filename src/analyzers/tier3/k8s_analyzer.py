"""Kubernetes Manifest Analyzer: YAML-based K8s resource extraction.

Parses Kubernetes manifests and extracts resources, container images,
ports, and relationships between them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)

# Kind -> canonical node sub-type mapping for logging/metadata
_KIND_MAP: dict[str, str] = {
    "Deployment": "deployment",
    "StatefulSet": "stateful_set",
    "DaemonSet": "daemon_set",
    "ReplicaSet": "replica_set",
    "Job": "job",
    "CronJob": "cron_job",
    "Pod": "pod",
    "Service": "service",
    "Ingress": "ingress",
    "ConfigMap": "config_map",
    "Secret": "secret",
    "PersistentVolumeClaim": "persistent_volume_claim",
    "PersistentVolume": "volume",
    "Namespace": "namespace",
    "ServiceAccount": "service_account",
    "Role": "role",
    "ClusterRole": "role",
    "RoleBinding": "role",
    "ClusterRoleBinding": "role",
    "NetworkPolicy": "network_policy",
    "HorizontalPodAutoscaler": "deployment",
}


class K8sAnalyzer(BaseAnalyzer):
    """YAML-based Kubernetes manifest analyzer."""

    ANALYZER_ID = "k8s"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".yaml", ".yml"]

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        """Parse K8s manifests and emit graph deltas."""
        try:
            import yaml
        except ImportError:
            logger.warning("yaml_import_failed", reason="PyYAML not installed")
            return []

        try:
            docs = list(yaml.safe_load_all(source))
        except Exception as exc:
            logger.debug("yaml_parse_failed", file_path=file_path, error=str(exc))
            return []

        all_deltas: list[GraphDelta] = []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if "apiVersion" not in doc or "kind" not in doc:
                continue
            delta = self._analyze_manifest(doc, file_path)
            if delta:
                all_deltas.append(delta)

        return all_deltas

    # ── Internal ─────────────────────────────────────────────────────────

    def _analyze_manifest(
        self, doc: dict[str, Any], file_path: str
    ) -> GraphDelta | None:
        ops: list = []
        scope: set[UUID] = set()

        kind = doc.get("kind", "Unknown")
        api_version = doc.get("apiVersion", "")
        metadata: dict[str, Any] = doc.get("metadata", {}) or {}
        name = metadata.get("name", "unnamed")
        namespace = metadata.get("namespace", "default")

        # File node
        file_id = uuid4()
        ops.append(
            self._add_node(
                "file",
                Path(file_path).name,
                file_path=file_path,
                language="yaml",
                node_id=file_id,
            )
        )
        scope.add(file_id)

        # K8s resource node
        resource_id = uuid4()
        ops.append(
            self._add_node(
                "k8s_resource",
                name,
                file_path=file_path,
                language="yaml",
                node_id=resource_id,
                kind=kind,
                api_version=api_version,
                namespace=namespace,
                k8s_sub_type=_KIND_MAP.get(kind, kind.lower()),
            )
        )
        scope.add(resource_id)
        ops.append(self._add_edge(file_id, resource_id, "defines"))

        # Extract spec-level details
        spec: dict[str, Any] = doc.get("spec", {}) or {}

        # Container images (Deployment/StatefulSet/DaemonSet/Pod/Job/CronJob)
        containers = self._extract_containers(doc)
        for ctr in containers:
            image = ctr.get("image", "")
            if not image:
                continue
            ctr_name = ctr.get("name", "unnamed")
            img_id = uuid4()
            ops.append(
                self._add_node(
                    "container_image",
                    image,
                    file_path=file_path,
                    language="yaml",
                    node_id=img_id,
                    container_name=ctr_name,
                    kind=kind,
                )
            )
            scope.add(img_id)
            ops.append(self._add_edge(resource_id, img_id, "uses"))

            # Container ports
            for port_spec in ctr.get("ports", []) or []:
                if isinstance(port_spec, dict):
                    port_num = port_spec.get("containerPort") or port_spec.get("port")
                    if port_num:
                        protocol = port_spec.get("protocol", "TCP")
                        port_name = port_spec.get("name", "")
                        pid = uuid4()
                        ops.append(
                            self._add_node(
                                "port",
                                f"{port_num}/{protocol}",
                                file_path=file_path,
                                language="yaml",
                                node_id=pid,
                                port_number=int(port_num),
                                protocol=protocol,
                                port_name=port_name,
                            )
                        )
                        scope.add(pid)
                        ops.append(self._add_edge(resource_id, pid, "exposes"))

        # Service ports
        if kind == "Service":
            for port_spec in spec.get("ports", []) or []:
                if isinstance(port_spec, dict):
                    port_num = port_spec.get("port")
                    target_port = port_spec.get("targetPort", "")
                    protocol = port_spec.get("protocol", "TCP")
                    port_name = port_spec.get("name", "")
                    if port_num:
                        pid = uuid4()
                        ops.append(
                            self._add_node(
                                "port",
                                f"{port_num}/{protocol}",
                                file_path=file_path,
                                language="yaml",
                                node_id=pid,
                                port_number=int(port_num),
                                target_port=str(target_port),
                                protocol=protocol,
                                port_name=port_name,
                            )
                        )
                        scope.add(pid)
                        ops.append(self._add_edge(resource_id, pid, "exposes"))

        # Volumes
        volumes_spec = self._extract_volumes(doc)
        for vol in volumes_spec:
            vol_name = vol.get("name", "unnamed")
            vid = uuid4()
            # Determine volume type
            vol_type = "emptyDir"
            for vt in (
                "persistentVolumeClaim", "configMap", "secret",
                "hostPath", "emptyDir", "nfs",
            ):
                if vt in vol:
                    vol_type = vt
                    break
            ops.append(
                self._add_node(
                    "volume",
                    vol_name,
                    file_path=file_path,
                    language="yaml",
                    node_id=vid,
                    volume_type=vol_type,
                    kind=kind,
                )
            )
            scope.add(vid)
            ops.append(self._add_edge(resource_id, vid, "mounts"))

        if len(ops) <= 2:
            # Only file + resource node, no meaningful extras
            return None

        logger.debug(
            "k8s_analysis_complete",
            file_path=file_path,
            kind=kind,
            name=name,
            node_count=len([o for o in ops if getattr(o, "op", "") == "add_node"]),
        )
        return self._make_delta(ops, file_path, scope)

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_containers(doc: dict[str, Any]) -> list[dict[str, Any]]:
        """Walk the manifest to find container specs regardless of kind."""
        containers: list[dict[str, Any]] = []
        spec = doc.get("spec", {}) or {}

        # Pod spec directly
        if "containers" in spec:
            containers.extend(spec.get("containers") or [])
            containers.extend(spec.get("initContainers") or [])
            return containers

        # Deployment / StatefulSet / DaemonSet / ReplicaSet
        template = spec.get("template", {}) or {}
        tspec = template.get("spec", {}) or {}
        if "containers" in tspec:
            containers.extend(tspec.get("containers") or [])
            containers.extend(tspec.get("initContainers") or [])
            return containers

        # CronJob
        job_template = spec.get("jobTemplate", {}) or {}
        jspec = job_template.get("spec", {}) or {}
        jt = jspec.get("template", {}) or {}
        jtspec = jt.get("spec", {}) or {}
        if "containers" in jtspec:
            containers.extend(jtspec.get("containers") or [])
            containers.extend(jtspec.get("initContainers") or [])

        return containers

    @staticmethod
    def _extract_volumes(doc: dict[str, Any]) -> list[dict[str, Any]]:
        """Walk the manifest to find volume specs."""
        spec = doc.get("spec", {}) or {}

        # Pod
        if "volumes" in spec:
            return spec.get("volumes") or []

        # Deployment-like
        template = spec.get("template", {}) or {}
        tspec = template.get("spec", {}) or {}
        if "volumes" in tspec:
            return tspec.get("volumes") or []

        # CronJob
        job_template = spec.get("jobTemplate", {}) or {}
        jspec = job_template.get("spec", {}) or {}
        jt = jspec.get("template", {}) or {}
        jtspec = jt.get("spec", {}) or {}
        if "volumes" in jtspec:
            return jtspec.get("volumes") or []

        return []
