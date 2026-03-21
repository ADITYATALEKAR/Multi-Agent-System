"""Python Analyzer: deep analysis using Python's ast module.

Extracts: modules, classes, functions, methods, imports, decorators,
variables, constants, and their relationships (calls, inherits, imports, etc.).
Accuracy targets: 95% entity, 85% deps.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from src.analyzers.harness import BaseAnalyzer
from src.core.fact import GraphDelta


class PythonAnalyzer(BaseAnalyzer):
    """Analyzes Python source code using the ast module."""

    ANALYZER_ID = "python"
    VERSION = "0.1.0"
    SUPPORTED_EXTENSIONS = [".py"]

    def analyze(self, source: str, file_path: str) -> list[GraphDelta]:
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return []

        ops: list = []
        scope: set[UUID] = set()
        name_to_id: dict[str, UUID] = {}

        # File node
        file_id = uuid4()
        module_name = Path(file_path).stem
        ops.append(self._add_node(
            "file", module_name, file_path=file_path, language="python",
            node_id=file_id, qualified_name=file_path,
        ))
        scope.add(file_id)

        # Walk the AST
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                cls_id = uuid4()
                name_to_id[node.name] = cls_id
                ops.append(self._add_node(
                    "class", node.name, file_path=file_path,
                    start_line=node.lineno, end_line=node.end_lineno or node.lineno,
                    language="python", node_id=cls_id,
                    qualified_name=f"{file_path}:{node.name}",
                ))
                scope.add(cls_id)
                ops.append(self._add_edge(file_id, cls_id, "contains"))

                # Inheritance
                for base in node.bases:
                    base_name = self._get_name(base)
                    if base_name and base_name in name_to_id:
                        ops.append(self._add_edge(cls_id, name_to_id[base_name], "inherits"))

                # Decorators
                for dec in node.decorator_list:
                    dec_name = self._get_name(dec)
                    if dec_name:
                        dec_id = uuid4()
                        ops.append(self._add_node(
                            "decorator", dec_name, file_path=file_path,
                            start_line=dec.lineno, language="python", node_id=dec_id,
                        ))
                        scope.add(dec_id)
                        ops.append(self._add_edge(dec_id, cls_id, "decorates"))

                # Methods inside the class
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_id = uuid4()
                        name_to_id[f"{node.name}.{item.name}"] = method_id
                        ops.append(self._add_node(
                            "method", item.name, file_path=file_path,
                            start_line=item.lineno,
                            end_line=item.end_lineno or item.lineno,
                            language="python", node_id=method_id,
                            qualified_name=f"{file_path}:{node.name}.{item.name}",
                            is_async=isinstance(item, ast.AsyncFunctionDef),
                        ))
                        scope.add(method_id)
                        ops.append(self._add_edge(cls_id, method_id, "contains"))

                        # Parameters
                        for arg in item.args.args:
                            if arg.arg == "self" or arg.arg == "cls":
                                continue
                            param_id = uuid4()
                            ops.append(self._add_node(
                                "parameter", arg.arg, file_path=file_path,
                                start_line=arg.lineno if hasattr(arg, "lineno") else item.lineno,
                                language="python", node_id=param_id,
                            ))
                            scope.add(param_id)
                            ops.append(self._add_edge(method_id, param_id, "declares"))

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Top-level functions (not methods — those are handled above)
                if not self._is_method(node, tree):
                    func_id = uuid4()
                    name_to_id[node.name] = func_id
                    ops.append(self._add_node(
                        "function", node.name, file_path=file_path,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        language="python", node_id=func_id,
                        qualified_name=f"{file_path}:{node.name}",
                        is_async=isinstance(node, ast.AsyncFunctionDef),
                    ))
                    scope.add(func_id)
                    ops.append(self._add_edge(file_id, func_id, "contains"))

                    for arg in node.args.args:
                        param_id = uuid4()
                        ops.append(self._add_node(
                            "parameter", arg.arg, file_path=file_path,
                            start_line=arg.lineno if hasattr(arg, "lineno") else node.lineno,
                            language="python", node_id=param_id,
                        ))
                        scope.add(param_id)
                        ops.append(self._add_edge(func_id, param_id, "declares"))

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imp_id = uuid4()
                    ops.append(self._add_node(
                        "import", alias.name, file_path=file_path,
                        start_line=node.lineno, language="python", node_id=imp_id,
                        import_name=alias.name, alias=alias.asname,
                    ))
                    scope.add(imp_id)
                    ops.append(self._add_edge(file_id, imp_id, "imports"))

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in (node.names or []):
                    imp_id = uuid4()
                    full_name = f"{module}.{alias.name}" if module else alias.name
                    ops.append(self._add_node(
                        "import", full_name, file_path=file_path,
                        start_line=node.lineno, language="python", node_id=imp_id,
                        import_name=full_name, alias=alias.asname, from_module=module,
                    ))
                    scope.add(imp_id)
                    ops.append(self._add_edge(file_id, imp_id, "imports"))

            elif isinstance(node, ast.Assign):
                # Module-level constants (ALL_CAPS)
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        const_id = uuid4()
                        ops.append(self._add_node(
                            "constant", target.id, file_path=file_path,
                            start_line=node.lineno, language="python", node_id=const_id,
                        ))
                        scope.add(const_id)
                        ops.append(self._add_edge(file_id, const_id, "defines"))

        # Scan for function calls to create call edges
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call_name = self._get_name(node.func)
                if call_name and call_name in name_to_id:
                    # Find the enclosing function/method
                    # Simplified: just create the edge to the called function
                    pass  # TODO(v3.x): Implement call-site resolution

        if not ops:
            return []

        return [self._make_delta(ops, file_path, scope)]

    @staticmethod
    def _get_name(node: ast.expr) -> str | None:
        """Extract a simple name from an AST node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            parts = []
            current = node
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        elif isinstance(node, ast.Call):
            return PythonAnalyzer._get_name(node.func)
        return None

    @staticmethod
    def _is_method(node: ast.FunctionDef | ast.AsyncFunctionDef, tree: ast.Module) -> bool:
        """Check if a function node is a method (inside a class)."""
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef):
                for item in parent.body:
                    if item is node:
                        return True
        return False
