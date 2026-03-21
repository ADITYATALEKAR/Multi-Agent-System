"""Ansible Analyzer: YAML-based playbook and role task extraction.

Detects Ansible playbooks (list with 'hosts' key) and role tasks
(list with 'name' key and module keys). Extracts plays, tasks,
modules used, and roles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta

logger = structlog.get_logger(__name__)

# Common Ansible module names used for detection heuristics
_KNOWN_MODULES: set[str] = {
    # Package management
    "apt", "yum", "dnf", "pip", "npm", "gem", "apk", "pacman", "zypper",
    "package", "snap",
    # File management
    "copy", "template", "file", "lineinfile", "blockinfile", "replace",
    "fetch", "unarchive", "archive", "synchronize", "stat", "find",
    # Service management
    "service", "systemd", "sysvinit",
    # System
    "user", "group", "cron", "hostname", "sysctl", "mount",
    "authorized_key", "known_hosts", "firewalld", "iptables",
    # Commands
    "command", "shell", "raw", "script", "expect",
    # Cloud
    "ec2", "s3_bucket", "azure_rm_virtualmachine", "gcp_compute_instance",
    # Containers
    "docker_container", "docker_image", "docker_compose",
    "k8s", "helm",
    # Database
    "mysql_db", "mysql_user", "postgresql_db", "postgresql_user",
    # Network
    "uri", "get_url", "wait_for",
    # Config management
    "git", "subversion",
    # Misc
    "debug", "fail", "assert", "set_fact", "register", "include",
    "include_tasks", "import_tasks", "include_role", "import_role",
    "include_vars", "pause", "wait_for_connection",
    "meta", "add_host", "group_by",
    # Collections-style (community.general, ansible.builtin, etc.)
    "ansible.builtin.copy", "ansible.builtin.template",
    "ansible.builtin.service", "ansible.builtin.command",
    "ansible.builtin.shell", "ansible.builtin.file",
    "ansible.builtin.apt", "ansible.builtin.yum",
    "ansible.builtin.pip", "ansible.builtin.debug",
    "ansible.builtin.set_fact", "ansible.builtin.uri",
    "ansible.builtin.git", "ansible.builtin.user",
    "ansible.builtin.group", "ansible.builtin.lineinfile",
    "ansible.builtin.blockinfile", "ansible.builtin.stat",
    "ansible.builtin.fetch", "ansible.builtin.unarchive",
}


def _is_ansible_playbook(data: Any) -> bool:
    """Check if parsed YAML looks like an Ansible playbook."""
    if not isinstance(data, list) or not data:
        return False
    # Playbooks have dicts with 'hosts' key
    return any(isinstance(item, dict) and "hosts" in item for item in data)


def _is_ansible_tasks(data: Any) -> bool:
    """Check if parsed YAML looks like Ansible tasks (role tasks/handlers)."""
    if not isinstance(data, list) or not data:
        return False
    # Tasks are dicts with 'name' and at least one module key
    for item in data:
        if not isinstance(item, dict):
            continue
        if "name" not in item:
            continue
        # Check if any key is a known module
        for key in item:
            if key in _KNOWN_MODULES or "." in key:
                return True
    return False


class AnsibleAnalyzer(BaseAnalyzer):
    """YAML-based Ansible playbook and role task analyzer."""

    ANALYZER_ID = "ansible"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".yaml", ".yml"]

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        """Parse Ansible YAML and emit graph deltas."""
        try:
            import yaml
        except ImportError:
            logger.warning("yaml_import_failed", reason="PyYAML not installed")
            return []

        try:
            data = yaml.safe_load(source)
        except Exception as exc:
            logger.debug("yaml_parse_failed", file_path=file_path, error=str(exc))
            return []

        if _is_ansible_playbook(data):
            return self._analyze_playbook(data, file_path)
        elif _is_ansible_tasks(data):
            return self._analyze_tasks(data, file_path)

        return []

    # ── Playbook analysis ────────────────────────────────────────────────

    def _analyze_playbook(
        self, plays: list[dict[str, Any]], file_path: str
    ) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

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

        for play in plays:
            if not isinstance(play, dict):
                continue
            play_name = play.get("name", play.get("hosts", "unnamed"))
            hosts = play.get("hosts", "")
            become = play.get("become", False)
            gather_facts = play.get("gather_facts", True)

            play_id = uuid4()
            ops.append(
                self._add_node(
                    "ansible_play",
                    play_name,
                    file_path=file_path,
                    language="yaml",
                    node_id=play_id,
                    hosts=str(hosts),
                    become=become,
                    gather_facts=gather_facts,
                )
            )
            scope.add(play_id)
            ops.append(self._add_edge(file_id, play_id, "contains"))

            # Roles
            for role_entry in play.get("roles", []) or []:
                role_name = self._extract_role_name(role_entry)
                if role_name:
                    role_id = uuid4()
                    ops.append(
                        self._add_node(
                            "ansible_role",
                            role_name,
                            file_path=file_path,
                            language="yaml",
                            node_id=role_id,
                            role_name=role_name,
                        )
                    )
                    scope.add(role_id)
                    ops.append(self._add_edge(play_id, role_id, "uses"))

            # Tasks (pre_tasks, tasks, post_tasks, handlers)
            for task_section in ("pre_tasks", "tasks", "post_tasks", "handlers"):
                for task in play.get(task_section, []) or []:
                    if not isinstance(task, dict):
                        continue
                    self._emit_task(
                        task, file_path, play_id, task_section, ops, scope
                    )

        if len(ops) <= 1:
            return []

        logger.debug(
            "ansible_playbook_analysis_complete",
            file_path=file_path,
            play_count=len([
                o for o in ops
                if getattr(o, "op", "") == "add_node"
                and getattr(o, "node_type", "") == "ansible_play"
            ]),
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── Task-file analysis (roles/tasks/main.yml) ────────────────────────

    def _analyze_tasks(
        self, tasks: list[dict[str, Any]], file_path: str
    ) -> list[GraphDelta]:
        ops: list = []
        scope: set[UUID] = set()

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

        for task in tasks:
            if not isinstance(task, dict):
                continue
            self._emit_task(task, file_path, file_id, "tasks", ops, scope)

        if len(ops) <= 1:
            return []

        logger.debug(
            "ansible_tasks_analysis_complete",
            file_path=file_path,
            task_count=len([
                o for o in ops
                if getattr(o, "op", "") == "add_node"
                and getattr(o, "node_type", "") == "ansible_task"
            ]),
        )
        return [self._make_delta(ops, file_path, scope)]

    # ── Shared helpers ───────────────────────────────────────────────────

    def _emit_task(
        self,
        task: dict[str, Any],
        file_path: str,
        parent_id: UUID,
        section: str,
        ops: list,
        scope: set[UUID],
    ) -> None:
        """Emit an ansible_task node and its edge to the parent."""
        task_name = task.get("name", "unnamed_task")
        module_name = self._detect_module(task)
        when = task.get("when", "")
        loop = task.get("loop") or task.get("with_items") or ""
        register_var = task.get("register", "")
        tags = task.get("tags", [])

        task_id = uuid4()
        ops.append(
            self._add_node(
                "ansible_task",
                task_name,
                file_path=file_path,
                language="yaml",
                node_id=task_id,
                module=module_name,
                section=section,
                when=str(when) if when else "",
                loop=str(loop) if loop else "",
                register=register_var,
                tags=tags if isinstance(tags, list) else [tags] if tags else [],
            )
        )
        scope.add(task_id)
        ops.append(self._add_edge(parent_id, task_id, "contains"))

        # If there is an include_role / import_role, emit role node
        for include_key in ("include_role", "import_role", "ansible.builtin.include_role", "ansible.builtin.import_role"):
            role_spec = task.get(include_key)
            if isinstance(role_spec, dict):
                rn = role_spec.get("name", "")
                if rn:
                    rid = uuid4()
                    ops.append(
                        self._add_node(
                            "ansible_role",
                            rn,
                            file_path=file_path,
                            language="yaml",
                            node_id=rid,
                            role_name=rn,
                        )
                    )
                    scope.add(rid)
                    ops.append(self._add_edge(task_id, rid, "uses"))

    @staticmethod
    def _detect_module(task: dict[str, Any]) -> str:
        """Identify which Ansible module a task uses."""
        # Skip meta keys
        _meta_keys = {
            "name", "when", "register", "tags", "notify", "listen",
            "become", "become_user", "become_method", "ignore_errors",
            "changed_when", "failed_when", "no_log", "loop", "with_items",
            "with_dict", "with_fileglob", "with_first_found",
            "environment", "vars", "delegate_to", "run_once",
            "retries", "delay", "until", "block", "rescue", "always",
            "check_mode", "diff", "any_errors_fatal", "throttle",
            "timeout", "collections",
        }
        for key in task:
            if key not in _meta_keys:
                if key in _KNOWN_MODULES or "." in key:
                    return key
        return "unknown"

    @staticmethod
    def _extract_role_name(role_entry: Any) -> str:
        """Extract the role name from various role specification formats."""
        if isinstance(role_entry, str):
            return role_entry
        if isinstance(role_entry, dict):
            return role_entry.get("role", role_entry.get("name", ""))
        return ""
