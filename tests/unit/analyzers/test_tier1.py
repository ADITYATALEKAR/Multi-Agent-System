"""Unit tests for all Tier 1 language analyzers."""

from __future__ import annotations

import pytest

from src.core.fact import AddEdge, AddNode, GraphDelta


# ── Python Analyzer ─────────────────────────────────────────────────────────


class TestPythonAnalyzer:
    def setup_method(self) -> None:
        from src.analyzers.tier1.python_analyzer import PythonAnalyzer
        self.analyzer = PythonAnalyzer()

    def test_extract_class(self) -> None:
        src = "class MyClass:\n    pass\n"
        results = self.analyzer.analyze(src, "test.py")
        assert len(results) == 1
        ops = results[0].operations
        node_types = [op.node_type for op in ops if isinstance(op, AddNode)]
        assert "file" in node_types
        assert "class" in node_types
        names = [op.attributes["name"] for op in ops if isinstance(op, AddNode) and op.node_type == "class"]
        assert "MyClass" in names

    def test_extract_function(self) -> None:
        src = "def hello(name):\n    return f'hi {name}'\n"
        results = self.analyzer.analyze(src, "test.py")
        assert len(results) == 1
        ops = results[0].operations
        names = [op.attributes["name"] for op in ops if isinstance(op, AddNode) and op.node_type == "function"]
        assert "hello" in names

    def test_extract_imports(self) -> None:
        src = "import os\nfrom pathlib import Path\n"
        results = self.analyzer.analyze(src, "test.py")
        ops = results[0].operations
        import_names = [op.attributes["name"] for op in ops if isinstance(op, AddNode) and op.node_type == "import"]
        assert "os" in import_names
        assert "pathlib.Path" in import_names

    def test_extract_method(self) -> None:
        src = "class Foo:\n    def bar(self):\n        pass\n"
        results = self.analyzer.analyze(src, "test.py")
        ops = results[0].operations
        method_names = [op.attributes["name"] for op in ops if isinstance(op, AddNode) and op.node_type == "method"]
        assert "bar" in method_names

    def test_extract_constant(self) -> None:
        src = "MAX_SIZE = 100\n"
        results = self.analyzer.analyze(src, "test.py")
        ops = results[0].operations
        const_names = [op.attributes["name"] for op in ops if isinstance(op, AddNode) and op.node_type == "constant"]
        assert "MAX_SIZE" in const_names

    def test_syntax_error_returns_empty(self) -> None:
        src = "def broken(\n"
        results = self.analyzer.analyze(src, "test.py")
        assert results == []

    def test_inheritance(self) -> None:
        src = "class Base:\n    pass\n\nclass Child(Base):\n    pass\n"
        results = self.analyzer.analyze(src, "test.py")
        ops = results[0].operations
        inherits_edges = [op for op in ops if isinstance(op, AddEdge) and op.edge_type == "inherits"]
        assert len(inherits_edges) == 1

    def test_empty_file(self) -> None:
        results = self.analyzer.analyze("", "empty.py")
        # Should still have at least the file node
        assert len(results) == 1

    def test_async_function(self) -> None:
        src = "async def fetch_data():\n    pass\n"
        results = self.analyzer.analyze(src, "test.py")
        ops = results[0].operations
        func_ops = [op for op in ops if isinstance(op, AddNode) and op.node_type == "function"]
        assert len(func_ops) == 1
        assert func_ops[0].attributes.get("is_async") is True

    def test_decorator(self) -> None:
        src = "@staticmethod\nclass Decorated:\n    pass\n"
        results = self.analyzer.analyze(src, "test.py")
        ops = results[0].operations
        dec_ops = [op for op in ops if isinstance(op, AddNode) and op.node_type == "decorator"]
        assert len(dec_ops) == 1


# ── TypeScript Analyzer ─────────────────────────────────────────────────────


class TestTypeScriptAnalyzer:
    def setup_method(self) -> None:
        from src.analyzers.tier1.typescript_analyzer import TypeScriptAnalyzer
        self.analyzer = TypeScriptAnalyzer()

    def test_supported_extensions(self) -> None:
        assert ".ts" in self.analyzer.SUPPORTED_EXTENSIONS
        assert ".tsx" in self.analyzer.SUPPORTED_EXTENSIONS
        assert ".js" in self.analyzer.SUPPORTED_EXTENSIONS

    def test_extract_class(self) -> None:
        src = "export class UserService {\n  constructor() {}\n}\n"
        results = self.analyzer.analyze(src, "test.ts")
        ops = results[0].operations
        classes = [op for op in ops if isinstance(op, AddNode) and op.node_type == "class"]
        assert len(classes) == 1
        assert classes[0].attributes["name"] == "UserService"

    def test_extract_interface(self) -> None:
        src = "interface IUser {\n  name: string;\n}\n"
        results = self.analyzer.analyze(src, "test.ts")
        ops = results[0].operations
        ifaces = [op for op in ops if isinstance(op, AddNode) and op.node_type == "interface"]
        assert len(ifaces) == 1

    def test_extract_function(self) -> None:
        src = "export function fetchData() {\n  return null;\n}\n"
        results = self.analyzer.analyze(src, "test.ts")
        ops = results[0].operations
        funcs = [op for op in ops if isinstance(op, AddNode) and op.node_type == "function"]
        assert any(f.attributes["name"] == "fetchData" for f in funcs)

    def test_extract_arrow_function(self) -> None:
        src = "export const handler = (req: Request) => {\n  return res;\n};\n"
        results = self.analyzer.analyze(src, "test.ts")
        ops = results[0].operations
        funcs = [op for op in ops if isinstance(op, AddNode) and op.node_type == "function"]
        assert any(f.attributes["name"] == "handler" for f in funcs)

    def test_extract_import(self) -> None:
        src = "import { useState } from 'react';\n"
        results = self.analyzer.analyze(src, "test.tsx")
        ops = results[0].operations
        imports = [op for op in ops if isinstance(op, AddNode) and op.node_type == "import"]
        assert any(i.attributes["name"] == "react" for i in imports)


# ── Java Analyzer ───────────────────────────────────────────────────────────


class TestJavaAnalyzer:
    def setup_method(self) -> None:
        from src.analyzers.tier1.java_analyzer import JavaAnalyzer
        self.analyzer = JavaAnalyzer()

    def test_extract_class(self) -> None:
        src = "public class UserController {\n}\n"
        results = self.analyzer.analyze(src, "UserController.java")
        ops = results[0].operations
        classes = [op for op in ops if isinstance(op, AddNode) and op.node_type == "class"]
        assert len(classes) == 1
        assert classes[0].attributes["name"] == "UserController"

    def test_extract_package(self) -> None:
        src = "package com.example.app;\n\npublic class App {}\n"
        results = self.analyzer.analyze(src, "App.java")
        ops = results[0].operations
        pkgs = [op for op in ops if isinstance(op, AddNode) and op.node_type == "package"]
        assert len(pkgs) == 1
        assert pkgs[0].attributes["name"] == "com.example.app"

    def test_extract_import(self) -> None:
        src = "import java.util.List;\n\npublic class App {}\n"
        results = self.analyzer.analyze(src, "App.java")
        ops = results[0].operations
        imports = [op for op in ops if isinstance(op, AddNode) and op.node_type == "import"]
        assert any(i.attributes["name"] == "java.util.List" for i in imports)

    def test_extract_enum(self) -> None:
        src = "public enum Status {\n  ACTIVE, INACTIVE\n}\n"
        results = self.analyzer.analyze(src, "Status.java")
        ops = results[0].operations
        enums = [op for op in ops if isinstance(op, AddNode) and op.node_type == "enum"]
        assert len(enums) == 1


# ── Go Analyzer ─────────────────────────────────────────────────────────────


class TestGoAnalyzer:
    def setup_method(self) -> None:
        from src.analyzers.tier1.go_analyzer import GoAnalyzer
        self.analyzer = GoAnalyzer()

    def test_extract_package(self) -> None:
        src = "package main\n\nfunc main() {}\n"
        results = self.analyzer.analyze(src, "main.go")
        ops = results[0].operations
        pkgs = [op for op in ops if isinstance(op, AddNode) and op.node_type == "package"]
        assert len(pkgs) == 1
        assert pkgs[0].attributes["name"] == "main"

    def test_extract_struct(self) -> None:
        src = "package main\n\ntype User struct {\n\tName string\n}\n"
        results = self.analyzer.analyze(src, "user.go")
        ops = results[0].operations
        structs = [op for op in ops if isinstance(op, AddNode) and op.node_type == "class"]
        assert len(structs) == 1
        assert structs[0].attributes["name"] == "User"

    def test_extract_interface(self) -> None:
        src = "package main\n\ntype Reader interface {\n\tRead(p []byte) (n int, err error)\n}\n"
        results = self.analyzer.analyze(src, "reader.go")
        ops = results[0].operations
        ifaces = [op for op in ops if isinstance(op, AddNode) and op.node_type == "interface"]
        assert len(ifaces) == 1

    def test_extract_function(self) -> None:
        src = "package main\n\nfunc Hello(name string) string {\n\treturn name\n}\n"
        results = self.analyzer.analyze(src, "main.go")
        ops = results[0].operations
        funcs = [op for op in ops if isinstance(op, AddNode) and op.node_type == "function"]
        assert any(f.attributes["name"] == "Hello" for f in funcs)

    def test_extract_method(self) -> None:
        src = "package main\n\ntype Server struct{}\n\nfunc (s *Server) Start() error {\n\treturn nil\n}\n"
        results = self.analyzer.analyze(src, "server.go")
        ops = results[0].operations
        methods = [op for op in ops if isinstance(op, AddNode) and op.node_type == "method"]
        assert len(methods) == 1
        assert methods[0].attributes["name"] == "Start"
        assert methods[0].attributes["receiver"] == "Server"

    def test_extract_imports(self) -> None:
        src = 'package main\n\nimport (\n\t"fmt"\n\t"os"\n)\n'
        results = self.analyzer.analyze(src, "main.go")
        ops = results[0].operations
        imports = [op for op in ops if isinstance(op, AddNode) and op.node_type == "import"]
        names = [i.attributes["name"] for i in imports]
        assert "fmt" in names
        assert "os" in names


# ── C++ Analyzer ────────────────────────────────────────────────────────────


class TestCppAnalyzer:
    def setup_method(self) -> None:
        from src.analyzers.tier1.cpp_analyzer import CppAnalyzer
        self.analyzer = CppAnalyzer()

    def test_extract_include(self) -> None:
        src = '#include <iostream>\n#include "myheader.h"\n'
        results = self.analyzer.analyze(src, "main.cpp")
        ops = results[0].operations
        includes = [op for op in ops if isinstance(op, AddNode) and op.node_type == "import"]
        names = [i.attributes["name"] for i in includes]
        assert "iostream" in names
        assert "myheader.h" in names

    def test_extract_class(self) -> None:
        src = "class MyClass {\npublic:\n  void method();\n};\n"
        results = self.analyzer.analyze(src, "test.cpp")
        ops = results[0].operations
        classes = [op for op in ops if isinstance(op, AddNode) and op.node_type == "class"]
        assert len(classes) >= 1

    def test_extract_namespace(self) -> None:
        src = "namespace myns {\n  class Foo {};\n}\n"
        results = self.analyzer.analyze(src, "test.cpp")
        ops = results[0].operations
        ns = [op for op in ops if isinstance(op, AddNode) and op.node_type == "namespace"]
        assert len(ns) == 1

    def test_extract_enum(self) -> None:
        src = "enum class Color { Red, Green, Blue };\n"
        results = self.analyzer.analyze(src, "test.h")
        ops = results[0].operations
        enums = [op for op in ops if isinstance(op, AddNode) and op.node_type == "enum"]
        assert len(enums) == 1


# ── Rust Analyzer ───────────────────────────────────────────────────────────


class TestRustAnalyzer:
    def setup_method(self) -> None:
        from src.analyzers.tier1.rust_analyzer import RustAnalyzer
        self.analyzer = RustAnalyzer()

    def test_extract_struct(self) -> None:
        src = "pub struct User {\n    name: String,\n}\n"
        results = self.analyzer.analyze(src, "lib.rs")
        ops = results[0].operations
        structs = [op for op in ops if isinstance(op, AddNode) and op.node_type == "class"]
        assert len(structs) == 1
        assert structs[0].attributes["name"] == "User"

    def test_extract_trait(self) -> None:
        src = "pub trait Serialize {\n    fn serialize(&self) -> Vec<u8>;\n}\n"
        results = self.analyzer.analyze(src, "lib.rs")
        ops = results[0].operations
        traits = [op for op in ops if isinstance(op, AddNode) and op.node_type == "interface"]
        assert len(traits) == 1

    def test_extract_enum(self) -> None:
        src = "pub enum Direction {\n    North,\n    South,\n}\n"
        results = self.analyzer.analyze(src, "lib.rs")
        ops = results[0].operations
        enums = [op for op in ops if isinstance(op, AddNode) and op.node_type == "enum"]
        assert len(enums) == 1

    def test_extract_use(self) -> None:
        src = "use std::collections::HashMap;\nuse serde::Serialize;\n"
        results = self.analyzer.analyze(src, "lib.rs")
        ops = results[0].operations
        uses = [op for op in ops if isinstance(op, AddNode) and op.node_type == "import"]
        assert len(uses) == 2

    def test_extract_function(self) -> None:
        src = "pub fn process(data: &[u8]) -> Result<(), Error> {\n    Ok(())\n}\n"
        results = self.analyzer.analyze(src, "lib.rs")
        ops = results[0].operations
        funcs = [op for op in ops if isinstance(op, AddNode) and op.node_type == "function"]
        assert any(f.attributes["name"] == "process" for f in funcs)

    def test_extract_mod(self) -> None:
        src = "pub mod utils;\nmod internal;\n"
        results = self.analyzer.analyze(src, "lib.rs")
        ops = results[0].operations
        mods = [op for op in ops if isinstance(op, AddNode) and op.node_type == "module"]
        assert len(mods) == 2

    def test_extract_macro(self) -> None:
        src = "macro_rules! my_macro {\n    () => {};\n}\n"
        results = self.analyzer.analyze(src, "lib.rs")
        ops = results[0].operations
        macros = [op for op in ops if isinstance(op, AddNode) and op.node_type == "macro"]
        assert len(macros) == 1


# ── C# Analyzer ─────────────────────────────────────────────────────────────


class TestCSharpAnalyzer:
    def setup_method(self) -> None:
        from src.analyzers.tier1.csharp_analyzer import CSharpAnalyzer
        self.analyzer = CSharpAnalyzer()

    def test_extract_using(self) -> None:
        src = "using System;\nusing System.Collections.Generic;\n"
        results = self.analyzer.analyze(src, "Program.cs")
        ops = results[0].operations
        usings = [op for op in ops if isinstance(op, AddNode) and op.node_type == "import"]
        assert len(usings) == 2

    def test_extract_class(self) -> None:
        src = "namespace MyApp\n{\n    public class UserService\n    {\n    }\n}\n"
        results = self.analyzer.analyze(src, "UserService.cs")
        ops = results[0].operations
        classes = [op for op in ops if isinstance(op, AddNode) and op.node_type == "class"]
        assert len(classes) == 1
        assert classes[0].attributes["name"] == "UserService"

    def test_extract_interface(self) -> None:
        src = "public interface IRepository\n{\n    void Save();\n}\n"
        results = self.analyzer.analyze(src, "IRepository.cs")
        ops = results[0].operations
        ifaces = [op for op in ops if isinstance(op, AddNode) and op.node_type == "interface"]
        assert len(ifaces) == 1

    def test_extract_namespace(self) -> None:
        src = "namespace MyApp.Services\n{\n}\n"
        results = self.analyzer.analyze(src, "Service.cs")
        ops = results[0].operations
        ns = [op for op in ops if isinstance(op, AddNode) and op.node_type == "namespace"]
        assert len(ns) == 1

    def test_extract_enum(self) -> None:
        src = "public enum Status\n{\n    Active,\n    Inactive\n}\n"
        results = self.analyzer.analyze(src, "Status.cs")
        ops = results[0].operations
        enums = [op for op in ops if isinstance(op, AddNode) and op.node_type == "enum"]
        assert len(enums) == 1
