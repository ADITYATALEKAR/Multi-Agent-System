"""DFE Stress Test Gate (v3.3 F2).

Gate requirement: 200 rules, 100K nodes, p99 < 50ms per delta evaluation.
Must pass before TMS wiring (Step 5 gate).
"""

from __future__ import annotations

import gc
import statistics
import time
from uuid import uuid4

import pytest

from src.core.fact import AddNode, GraphDelta
from src.dfe.compiler import RuleCompiler
from src.dfe.rete import ReteNetwork


# ── Helpers ────────────────────────────────────────────────────────────────────


NODE_TYPES = [
    "class", "function", "method", "file", "module",
    "import", "variable", "interface", "enum", "package",
    "service", "struct", "constant", "test_case", "constructor",
    "namespace", "type_alias", "decorator", "parameter", "coroutine",
]


def _generate_rule_def(index: int) -> dict:
    """Generate a unique rule definition for stress testing."""
    node_type = NODE_TYPES[index % len(NODE_TYPES)]
    return {
        "rule_id": f"stress-rule-{index:04d}",
        "name": f"Stress Rule {index}",
        "description": f"Stress test rule #{index}",
        "category": "structural",
        "weight": 1.0,
        "conditions": [
            {"entity": "node", "type": node_type, "bind": "n"},
        ],
        "action": {
            "type": "violation",
            "message": f"Stress violation from rule {index}: $n",
            "confidence": 0.8,
        },
    }


_seq_counter = 0


def _build_delta(node_count: int, node_type: str = "class") -> GraphDelta:
    """Build a GraphDelta with the specified number of AddNode operations."""
    global _seq_counter
    _seq_counter += 1
    ops = []
    for i in range(node_count):
        ops.append(AddNode(
            node_id=uuid4(),
            node_type=node_type,
            attributes={"name": f"Entity_{i}", "line": i + 1},
        ))
    return GraphDelta(operations=ops, sequence_number=_seq_counter, source="stress_test")


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.stress
class TestDFEStressGate:
    """v3.3 F2: DFE stress test — 200 rules, 100K nodes, p99 < 50ms."""

    def _setup_network(self, num_rules: int = 200) -> ReteNetwork:
        """Create a ReteNetwork with the specified number of compiled rules."""
        rete = ReteNetwork()
        compiler = RuleCompiler()

        for i in range(num_rules):
            rule_def = _generate_rule_def(i)
            rule_ir = compiler.compile(rule_def)
            rete.register_rule(rule_ir)

        return rete

    def test_200_rules_registered(self) -> None:
        """Verify that 200 rules can be registered without error."""
        rete = self._setup_network(200)
        assert rete.rule_count == 200

    def test_single_delta_latency(self) -> None:
        """Single delta with 100 nodes should be well under 50ms."""
        rete = self._setup_network(200)
        delta = _build_delta(100, "class")

        start = time.monotonic()
        derived = rete.evaluate(delta)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 50, f"Single delta took {elapsed_ms:.2f}ms (limit: 50ms)"
        assert len(derived) > 0

    def test_100k_nodes_incremental(self) -> None:
        """100K nodes in batches — measure per-delta p99 latency.

        Realistic delta size: 50 operations per delta (typical analyzer output).
        2000 deltas × 50 nodes = 100K nodes total.
        """
        rete = self._setup_network(200)

        batch_size = 50
        warmup_batches = 100
        measured_batches = 2000
        total_batches = warmup_batches + measured_batches
        # total nodes = 50 * 2100 = 105K (100K measured + 5K warmup)
        latencies: list[float] = []

        # Warmup: prime caches, JIT, etc.
        for batch_idx in range(warmup_batches):
            node_type = NODE_TYPES[batch_idx % len(NODE_TYPES)]
            delta = _build_delta(batch_size, node_type)
            rete.evaluate(delta)

        # Measured runs: disable GC to avoid GC-pause outliers
        gc.disable()
        try:
            for batch_idx in range(measured_batches):
                node_type = NODE_TYPES[(warmup_batches + batch_idx) % len(NODE_TYPES)]
                delta = _build_delta(batch_size, node_type)

                start = time.monotonic()
                rete.evaluate(delta)
                elapsed_ms = (time.monotonic() - start) * 1000
                latencies.append(elapsed_ms)

                # Periodic manual GC to avoid OOM (every 200 batches)
                if batch_idx % 200 == 199:
                    gc.collect()
        finally:
            gc.enable()
            gc.collect()

        # Compute p99
        latencies.sort()
        p99_index = int(len(latencies) * 0.99)
        p99 = latencies[p99_index]
        p50 = latencies[len(latencies) // 2]
        mean_lat = statistics.mean(latencies)

        # Log results (pytest -s to see)
        print(f"\n=== DFE Stress Test Results ===")
        print(f"  Rules: 200, Batches: {measured_batches}, Batch size: {batch_size}")
        print(f"  Total nodes: {batch_size * total_batches:,}")
        print(f"  Mean latency: {mean_lat:.2f}ms")
        print(f"  P50 latency:  {p50:.2f}ms")
        print(f"  P99 latency:  {p99:.2f}ms")
        print(f"  Max latency:  {max(latencies):.2f}ms")

        assert p99 < 50, (
            f"p99 latency {p99:.2f}ms exceeds 50ms gate. "
            f"Mean={mean_lat:.2f}ms, Max={max(latencies):.2f}ms"
        )

    def test_beta_memory_cap_enforced(self) -> None:
        """Verify BetaMemory cap is respected under load."""
        from src.dfe.rete import BETA_MEMORY_CAP

        compiler = RuleCompiler()
        rule_def = {
            "rule_id": "cap-test-rule",
            "name": "Cap Test",
            "conditions": [
                {"entity": "node", "type": "class", "bind": "c"},
                {"entity": "node", "type": "function", "bind": "f"},
            ],
            "action": {
                "type": "violation",
                "message": "Cap test: $c $f",
                "confidence": 0.5,
            },
        }
        rule_ir = compiler.compile(rule_def)

        rete = ReteNetwork()
        rete.register_rule(rule_ir)

        # 320 * 320 = 102,400 potential matches > BETA_MEMORY_CAP
        for i in range(320):
            rete.assert_fact(uuid4(), "node", "class", {"name": f"Class{i}"})
        for i in range(320):
            rete.assert_fact(uuid4(), "node", "function", {"name": f"Func{i}"})

        pm_count = rete.get_partial_match_count("cap-test-rule")
        assert pm_count <= BETA_MEMORY_CAP, (
            f"BetaMemory has {pm_count} matches, exceeds cap {BETA_MEMORY_CAP}"
        )

    def test_rule_compilation_throughput(self) -> None:
        """200 rules should compile in under 1 second."""
        compiler = RuleCompiler()

        start = time.monotonic()
        for i in range(200):
            compiler.compile(_generate_rule_def(i))
        elapsed_ms = (time.monotonic() - start) * 1000

        assert elapsed_ms < 1000, f"Compiling 200 rules took {elapsed_ms:.2f}ms"
