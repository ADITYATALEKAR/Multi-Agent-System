"""Stacktrace Parser: extracts exceptions and stack frames from crash traces.

Supports Python, Java, JavaScript/Node.js, Go, and Rust stack trace
formats.  This is a Tier 5 standalone parser that does NOT inherit from
BaseAnalyzer.
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import structlog

from src.core.fact import CURRENT_SCHEMA_VERSION, AddEdge, AddNode, GraphDelta

logger = structlog.get_logger(__name__)


class StacktraceParser:
    """Parses stack traces from multiple languages and emits GraphDelta objects.

    Each parsed trace produces an ``exception`` node connected to ordered
    ``stack_frame`` nodes via ``contains`` edges.
    """

    # ── Python ────────────────────────────────────────────────────────────
    # Traceback (most recent call last):
    #   File "foo.py", line 42, in bar
    #     some_code()
    # ValueError: bad value

    _PY_TRACEBACK_RE = re.compile(
        r"Traceback \(most recent call last\):\s*\n(?P<frames>(?:.*\n)*?)"
        r"(?P<exc_type>\w[\w.]*):\s*(?P<message>.+)",
    )
    _PY_FRAME_RE = re.compile(
        r'^\s+File "(?P<file>[^"]+)", line (?P<line>\d+), in (?P<func>\S+)',
        re.MULTILINE,
    )

    # ── Java ──────────────────────────────────────────────────────────────
    # java.lang.NullPointerException: some message
    #     at com.example.Foo.bar(Foo.java:42)
    #     at com.example.Main.main(Main.java:10)
    # Caused by: ...

    _JAVA_EXC_RE = re.compile(
        r"^(?P<exc_type>[\w$.]+(?:Exception|Error|Throwable)[\w$.]*):\s*(?P<message>.*)$",
        re.MULTILINE,
    )
    _JAVA_FRAME_RE = re.compile(
        r"^\s+at\s+(?P<func>[\w$.]+)\((?P<file>[^:)]+?)(?::(?P<line>\d+))?\)",
        re.MULTILINE,
    )
    _JAVA_CAUSED_RE = re.compile(
        r"^Caused by:\s+(?P<exc_type>[\w$.]+):\s*(?P<message>.*)$",
        re.MULTILINE,
    )

    # ── JavaScript / Node.js ──────────────────────────────────────────────
    # TypeError: Cannot read property 'foo' of undefined
    #     at Object.<anonymous> (/app/index.js:42:13)
    #     at Module._compile (internal/modules/cjs/loader.js:999:30)

    _JS_EXC_RE = re.compile(
        r"^(?P<exc_type>\w+(?:Error|Exception)):\s*(?P<message>.+)$",
        re.MULTILINE,
    )
    _JS_FRAME_RE = re.compile(
        r"^\s+at\s+(?:(?P<func>[^\s(]+)\s+)?\(?(?P<file>[^:)]+):(?P<line>\d+):(?P<col>\d+)\)?",
        re.MULTILINE,
    )

    # ── Go ────────────────────────────────────────────────────────────────
    # goroutine 1 [running]:
    # main.foo()
    #     /app/main.go:42 +0x1a
    # panic: runtime error: index out of range

    _GO_PANIC_RE = re.compile(
        r"^(?:panic:\s+)?(?P<message>.+?)$\s*\n\s*goroutine\s+\d+\s+\[(?P<state>\w+)\]:",
        re.MULTILINE,
    )
    _GO_GOROUTINE_RE = re.compile(
        r"^goroutine\s+\d+\s+\[(?P<state>\w+)\]:\s*\n(?P<frames>(?:.*\n)*?)(?:\n|$)",
        re.MULTILINE,
    )
    _GO_FRAME_RE = re.compile(
        r"^(?P<func>[\w./]+(?:\.\w+)*)\(.*\)\s*\n\s+(?P<file>[^\s:]+):(?P<line>\d+)",
        re.MULTILINE,
    )

    # ── Rust ──────────────────────────────────────────────────────────────
    # thread 'main' panicked at 'index out of bounds', src/main.rs:42:5
    # stack backtrace:
    #    0: std::panicking::begin_panic
    #              at /rustc/.../library/std/src/panicking.rs:519:12
    #    1: foo::bar
    #              at ./src/main.rs:42:5

    _RUST_PANIC_RE = re.compile(
        r"thread '(?P<thread>[^']+)' panicked at '(?P<message>[^']+)',\s+(?P<file>\S+):(?P<line>\d+)",
    )
    _RUST_FRAME_RE = re.compile(
        r"^\s+\d+:\s+(?P<func>\S+)\s*\n\s+at\s+(?P<file>[^:]+):(?P<line>\d+)",
        re.MULTILINE,
    )

    def parse(self, raw: str, source_label: str = "") -> list[GraphDelta]:
        """Parse raw stack trace text and return graph deltas.

        Args:
            raw: Raw stack trace content (may contain multiple traces).
            source_label: Optional label for the source (e.g. filename).

        Returns:
            List of GraphDelta objects.
        """
        if not raw or not raw.strip():
            return []

        ops: list = []
        scope: set[UUID] = set()
        found = False

        # Try each language parser in order of specificity
        found |= self._parse_python(raw, ops, scope)
        found |= self._parse_java(raw, ops, scope)
        found |= self._parse_javascript(raw, ops, scope)
        found |= self._parse_go(raw, ops, scope)
        found |= self._parse_rust(raw, ops, scope)

        if not found:
            return []

        logger.debug("stacktrace_parse_complete", source=source_label, operations=len(ops))
        return [GraphDelta(
            sequence_number=0,
            source="parser:stacktrace",
            operations=ops,
            scope=scope,
            schema_version=CURRENT_SCHEMA_VERSION,
        )]

    # ── Language-specific parsers ─────────────────────────────────────────

    def _parse_python(self, raw: str, ops: list, scope: set[UUID]) -> bool:
        """Parse Python tracebacks."""
        found = False
        for m in self._PY_TRACEBACK_RE.finditer(raw):
            found = True
            exc_type = m.group("exc_type")
            message = m.group("message")
            frames_text = m.group("frames")

            exc_id = uuid4()
            ops.append(AddNode(
                node_id=exc_id,
                node_type="exception",
                attributes={
                    "name": exc_type,
                    "exception_type": exc_type,
                    "message": message,
                    "language": "python",
                    "parser": "stacktrace",
                },
            ))
            scope.add(exc_id)

            for idx, fm in enumerate(self._PY_FRAME_RE.finditer(frames_text)):
                frame_id = uuid4()
                ops.append(AddNode(
                    node_id=frame_id,
                    node_type="stack_frame",
                    attributes={
                        "name": fm.group("func"),
                        "file": fm.group("file"),
                        "line": int(fm.group("line")),
                        "function": fm.group("func"),
                        "order": idx,
                        "language": "python",
                        "parser": "stacktrace",
                    },
                ))
                scope.add(frame_id)
                ops.append(AddEdge(
                    src_id=exc_id,
                    tgt_id=frame_id,
                    edge_type="contains",
                    attributes={"source_analyzer": "stacktrace", "order": idx},
                ))
        return found

    def _parse_java(self, raw: str, ops: list, scope: set[UUID]) -> bool:
        """Parse Java/JVM stack traces."""
        found = False

        # Split on "Caused by:" to handle chained exceptions
        sections = re.split(r"(?=^Caused by:)", raw, flags=re.MULTILINE)
        sections = [raw] + [s for s in sections if s.startswith("Caused by:")]

        for section in sections:
            # Find exception header
            if section.startswith("Caused by:"):
                exc_m = self._JAVA_CAUSED_RE.search(section)
            else:
                exc_m = self._JAVA_EXC_RE.search(section)

            if exc_m is None:
                continue

            found = True
            exc_type = exc_m.group("exc_type")
            message = exc_m.group("message")

            exc_id = uuid4()
            ops.append(AddNode(
                node_id=exc_id,
                node_type="exception",
                attributes={
                    "name": exc_type,
                    "exception_type": exc_type,
                    "message": message,
                    "language": "java",
                    "parser": "stacktrace",
                },
            ))
            scope.add(exc_id)

            # Extract frames after the exception line
            frame_text = section[exc_m.end():]
            for idx, fm in enumerate(self._JAVA_FRAME_RE.finditer(frame_text)):
                frame_id = uuid4()
                line_num = int(fm.group("line")) if fm.group("line") else 0
                ops.append(AddNode(
                    node_id=frame_id,
                    node_type="stack_frame",
                    attributes={
                        "name": fm.group("func"),
                        "file": fm.group("file"),
                        "line": line_num,
                        "function": fm.group("func"),
                        "order": idx,
                        "language": "java",
                        "parser": "stacktrace",
                    },
                ))
                scope.add(frame_id)
                ops.append(AddEdge(
                    src_id=exc_id,
                    tgt_id=frame_id,
                    edge_type="contains",
                    attributes={"source_analyzer": "stacktrace", "order": idx},
                ))
        return found

    def _parse_javascript(self, raw: str, ops: list, scope: set[UUID]) -> bool:
        """Parse JavaScript / Node.js stack traces."""
        found = False
        for exc_m in self._JS_EXC_RE.finditer(raw):
            found = True
            exc_type = exc_m.group("exc_type")
            message = exc_m.group("message")

            exc_id = uuid4()
            ops.append(AddNode(
                node_id=exc_id,
                node_type="exception",
                attributes={
                    "name": exc_type,
                    "exception_type": exc_type,
                    "message": message,
                    "language": "javascript",
                    "parser": "stacktrace",
                },
            ))
            scope.add(exc_id)

            frame_text = raw[exc_m.end():]
            for idx, fm in enumerate(self._JS_FRAME_RE.finditer(frame_text)):
                # Stop if we hit another exception header
                if self._JS_EXC_RE.match(raw[exc_m.end() + fm.start():]):
                    break

                frame_id = uuid4()
                func_name = fm.group("func") or "<anonymous>"
                ops.append(AddNode(
                    node_id=frame_id,
                    node_type="stack_frame",
                    attributes={
                        "name": func_name,
                        "file": fm.group("file"),
                        "line": int(fm.group("line")),
                        "column": int(fm.group("col")),
                        "function": func_name,
                        "order": idx,
                        "language": "javascript",
                        "parser": "stacktrace",
                    },
                ))
                scope.add(frame_id)
                ops.append(AddEdge(
                    src_id=exc_id,
                    tgt_id=frame_id,
                    edge_type="contains",
                    attributes={"source_analyzer": "stacktrace", "order": idx},
                ))
        return found

    def _parse_go(self, raw: str, ops: list, scope: set[UUID]) -> bool:
        """Parse Go panic / goroutine stack traces."""
        found = False

        # Try panic header first
        panic_m = self._GO_PANIC_RE.search(raw)
        if panic_m:
            message = panic_m.group("message")
        else:
            message = "goroutine dump"

        for m in self._GO_GOROUTINE_RE.finditer(raw):
            found = True
            state = m.group("state")
            frames_text = m.group("frames")

            exc_id = uuid4()
            ops.append(AddNode(
                node_id=exc_id,
                node_type="exception",
                attributes={
                    "name": f"goroutine [{state}]",
                    "exception_type": "panic" if panic_m else "goroutine_dump",
                    "message": message,
                    "state": state,
                    "language": "go",
                    "parser": "stacktrace",
                },
            ))
            scope.add(exc_id)

            for idx, fm in enumerate(self._GO_FRAME_RE.finditer(frames_text)):
                frame_id = uuid4()
                ops.append(AddNode(
                    node_id=frame_id,
                    node_type="stack_frame",
                    attributes={
                        "name": fm.group("func"),
                        "file": fm.group("file"),
                        "line": int(fm.group("line")),
                        "function": fm.group("func"),
                        "order": idx,
                        "language": "go",
                        "parser": "stacktrace",
                    },
                ))
                scope.add(frame_id)
                ops.append(AddEdge(
                    src_id=exc_id,
                    tgt_id=frame_id,
                    edge_type="contains",
                    attributes={"source_analyzer": "stacktrace", "order": idx},
                ))
        return found

    def _parse_rust(self, raw: str, ops: list, scope: set[UUID]) -> bool:
        """Parse Rust panic / backtrace output."""
        found = False
        for m in self._RUST_PANIC_RE.finditer(raw):
            found = True
            thread = m.group("thread")
            message = m.group("message")
            panic_file = m.group("file")
            panic_line = int(m.group("line"))

            exc_id = uuid4()
            ops.append(AddNode(
                node_id=exc_id,
                node_type="exception",
                attributes={
                    "name": f"panic in thread '{thread}'",
                    "exception_type": "panic",
                    "message": message,
                    "thread": thread,
                    "file": panic_file,
                    "line": panic_line,
                    "language": "rust",
                    "parser": "stacktrace",
                },
            ))
            scope.add(exc_id)

            # Parse backtrace frames after this panic
            bt_text = raw[m.end():]
            for idx, fm in enumerate(self._RUST_FRAME_RE.finditer(bt_text)):
                frame_id = uuid4()
                ops.append(AddNode(
                    node_id=frame_id,
                    node_type="stack_frame",
                    attributes={
                        "name": fm.group("func"),
                        "file": fm.group("file"),
                        "line": int(fm.group("line")),
                        "function": fm.group("func"),
                        "order": idx,
                        "language": "rust",
                        "parser": "stacktrace",
                    },
                ))
                scope.add(frame_id)
                ops.append(AddEdge(
                    src_id=exc_id,
                    tgt_id=frame_id,
                    edge_type="contains",
                    attributes={"source_analyzer": "stacktrace", "order": idx},
                ))
        return found
