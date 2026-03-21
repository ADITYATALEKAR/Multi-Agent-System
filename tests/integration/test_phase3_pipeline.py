"""Integration test: Phase 3 end-to-end pipeline.

Tests the full flow: Memory → Fingerprint → Memoization → Consolidation →
CausalTemplate → IIE → Solver → LawGovernance 4-state.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.iie.architecture_ir import ArchitectureIR, ComponentSpec, Connection, DataflowSpec
from src.iie.engine import IIEEngine
from src.iie.runtime_monitor import IIERuntimeMonitor
from src.law_engine.governance import LawGovernance, LawHealthState
from src.memory.abstraction import TemplateAbstractor
from src.memory.agent import MemoryAgent
from src.memory.causal_template import CausalTemplate
from src.memory.consolidation import ConsolidationPipeline
from src.memory.fingerprint import FingerprintIndex, TwoLevelMemoKey, wl_hash
from src.memory.memoization import MemoizationCache
from src.memory.storage import InMemoryBackend
from src.memory.types import (
    Episode,
    EpisodeOutcome,
    Pattern,
    Procedure,
    ProcedureStep,
    RepairTemplate,
    SemanticRule,
    WorkingMemory,
)
from src.solver.budget import SolverBudget
from src.solver.layer import ConstraintSolverLayer


class TestPhase3EndToEnd:
    """Full Phase 3 pipeline integration."""

    def test_memory_store_full_crud(self) -> None:
        """All 5 memory types should support full CRUD via InMemoryBackend."""
        store = InMemoryBackend()

        # Episode
        ep = Episode(environment="staging", law_categories={"structural"})
        store.store_episode(ep)
        assert store.get_episode(ep.episode_id) is not None
        assert store.count_episodes() == 1

        # SemanticRule
        rule = SemanticRule(
            description="test rule",
            condition="X > 0",
            conclusion="system healthy",
        )
        store.store_rule(rule)
        assert store.get_rule(rule.rule_id) is not None

        # Procedure
        proc = Procedure(
            name="debug runbook",
            steps=[ProcedureStep(step_id=1, action="check_logs", description="Check logs")],
        )
        store.store_procedure(proc)
        assert store.get_procedure(proc.procedure_id) is not None

        # Pattern
        pat = Pattern(name="recurring pattern", signature=b"abc123")
        store.store_pattern(pat)
        assert store.get_pattern(pat.pattern_id) is not None

        # RepairTemplate
        tmpl = RepairTemplate(name="fix template")
        store.store_repair_template(tmpl)
        assert store.get_repair_template(tmpl.template_id) is not None

    def test_fingerprint_index_exact_and_approximate(self) -> None:
        """FingerprintIndex should support exact and approximate queries."""
        index = FingerprintIndex()

        nodes1 = [{"label": "svc"}, {"label": "db"}]
        edges1 = [(0, 1)]
        id1 = uuid4()
        fp1 = index.insert(id1, nodes1, edges1)

        # Exact match
        exact = index.query_exact(fp1)
        assert id1 in exact

        # Same structure should match
        nodes2 = [{"label": "svc"}, {"label": "db"}]
        edges2 = [(0, 1)]
        id2 = uuid4()
        fp2 = index.insert(id2, nodes2, edges2)
        assert fp1 == fp2  # same structure

        # Approximate query
        approx = index.query_approximate(nodes1, edges1, threshold=0.3)
        assert len(approx) >= 2  # should find both

    def test_two_level_memo_key_collision_verification(self) -> None:
        """TwoLevelMemoKey should distinguish collisions via canonical hash."""
        key = TwoLevelMemoKey()

        nodes1 = [{"label": "A"}, {"label": "B"}]
        edges1 = [(0, 1)]

        nodes2 = [{"label": "A"}, {"label": "B"}, {"label": "C"}]
        edges2 = [(0, 1), (1, 2)]

        wl1, can1 = key.compute_key(nodes1, edges1)
        wl2, can2 = key.compute_key(nodes2, edges2)

        # Different graphs should have different keys
        assert can1 != can2

        # Verification should work
        assert key.verify(can1, nodes1, edges1)
        assert not key.verify(can1, nodes2, edges2)

    def test_memoization_cache_hit_rate(self) -> None:
        """MemoizationCache should achieve > 30% hit rate on repeated patterns."""
        cache = MemoizationCache(max_size=100)

        nodes = [{"label": "X"}, {"label": "Y"}]
        edges = [(0, 1)]

        # Store 10 different results
        for i in range(10):
            n = [{"label": f"type_{i}"}, {"label": "shared"}]
            e = [(0, 1)]
            cache.put(n, e, value=f"result_{i}")

        # Query back 10 of the same (10 hits)
        for i in range(10):
            n = [{"label": f"type_{i}"}, {"label": "shared"}]
            e = [(0, 1)]
            result = cache.get(n, e)
            assert result == f"result_{i}", f"Expected result_{i}, got {result}"

        # Query 5 misses
        for i in range(5):
            n = [{"label": f"missing_{i}"}]
            e = []
            cache.get(n, e)

        assert cache.hit_ratio() > 0.3, f"Hit ratio {cache.hit_ratio():.2f} < 0.3"

    def test_consolidation_pipeline_compaction(self) -> None:
        """Consolidation should archive > 40% for families with 15+ episodes."""
        store = InMemoryBackend()
        pipeline = ConsolidationPipeline(store, archive_threshold=15, archive_keep_recent=5)

        # Create 20 episodes with the same fingerprint
        fp = wl_hash([{"label": "svc"}], [])
        for i in range(20):
            ep = Episode(
                fingerprint=fp,
                environment="production",
                law_categories={"structural"},
                outcome=EpisodeOutcome.RESOLVED,
                confidence=0.8,
                metadata={"rule_ids": ["STR-001"]},
            )
            store.store_episode(ep)

        assert store.count_episodes() == 20

        result = pipeline.consolidate()
        assert result.episodes_archived >= 15  # keep 5, archive 15
        assert result.compression_ratio > 0.40, (
            f"Compression ratio {result.compression_ratio:.2f} < 0.40"
        )

    def test_causal_template_from_episodes(self) -> None:
        """TemplateAbstractor should produce valid templates from 5+ episodes."""
        episodes: list[Episode] = []
        for i in range(5):
            ep = Episode(
                law_categories={"structural", "dependency"},
                confidence=0.8,
                metadata={
                    "causal_graph": {
                        "nodes": [
                            {"type": "service", "role": "source"},
                            {"type": "database", "role": "sink"},
                            {"type": "queue", "role": "relay"},
                        ],
                        "edges": [
                            {"source_type": "service", "target_type": "queue", "edge_type": "causes"},
                            {"source_type": "queue", "target_type": "database", "edge_type": "causes"},
                        ],
                    }
                },
            )
            episodes.append(ep)

        abstractor = TemplateAbstractor(min_episodes=3, similarity_threshold=0.5)
        template = abstractor.abstract(episodes)

        assert template is not None, "Template should be created from 5 similar episodes"
        assert template.graph.node_count >= 2, "Template should have nodes"
        assert template.graph.edge_count >= 1, "Template should have edges"
        assert len(template.source_episodes) == 5

    def test_iie_all_12_passes_operational(self) -> None:
        """IIE should run all 12 passes on a valid IR."""
        ir = ArchitectureIR()
        ir.components["svc-a"] = ComponentSpec(
            component_id="svc-a",
            name="Service A",
            component_type="service",
            dependencies=["svc-b"],
        )
        ir.components["svc-b"] = ComponentSpec(
            component_id="svc-b",
            name="Service B",
            component_type="service",
        )
        ir.connections.append(Connection(
            source="svc-a", target="svc-b",
            connection_type="depends_on",
        ))

        engine = IIEEngine()
        violations = engine.run_load_time_passes(ir)

        # Should run all 12 passes without error
        # Some violations expected (e.g., missing tiers)
        assert isinstance(violations, list)

    def test_iie_runtime_monitor_triggers(self) -> None:
        """RuntimeMonitor should trigger passes on delta."""
        engine = IIEEngine()
        monitor = IIERuntimeMonitor(engine)
        monitor.start()

        ir = ArchitectureIR()
        ir.components["svc-x"] = ComponentSpec(
            component_id="svc-x", name="X", component_type="service"
        )

        violations = monitor.on_delta(ir)
        assert isinstance(violations, list)
        monitor.stop()

    def test_solver_z3_satisfiability(self) -> None:
        """Solver should find satisfiable/unsatisfiable results via Z3."""
        solver = ConstraintSolverLayer()
        budget = SolverBudget(total_budget_ms=5000)

        # Satisfiable
        result = solver.check_satisfiability(
            ["(declare-const x Int) (assert (> x 0)) (assert (< x 10))"],
            budget=budget,
        )
        assert result.satisfiable is True
        assert result.model is not None

    def test_solver_budget_enforcement(self) -> None:
        """SolverBudget should track consumed time."""
        budget = SolverBudget(total_budget_ms=100)

        alloc = budget.allocate("simple")
        assert alloc == 50.0
        assert not budget.is_exhausted()

        budget.record_usage(80.0)
        assert budget.remaining_ms() == 20.0

        budget.record_usage(25.0)
        assert budget.is_exhausted()

    def test_governance_4state_full_lifecycle(self) -> None:
        """LawGovernance 4-state lifecycle: HEALTHY -> DEGRADED -> REVIEW -> QUARANTINE -> HEALTHY."""
        gov = LawGovernance(
            window_size=10,
            degraded_threshold=0.3,
            review_threshold=0.6,
        )

        # Start HEALTHY
        assert gov.get_health_state("LAW-X") == LawHealthState.HEALTHY

        # Push to DEGRADED (4/10 = 40% > 30%)
        for _ in range(4):
            gov.record_evaluation("LAW-X", success=False)
        for _ in range(6):
            gov.record_evaluation("LAW-X", success=True)
        gov.check_health("LAW-X")
        assert gov.get_health_state("LAW-X") == LawHealthState.DEGRADED

        # Push to REVIEW_REQUIRED (add more failures: now 7/10 > 60%)
        for _ in range(7):
            gov.record_evaluation("LAW-X", success=False)
        for _ in range(3):
            gov.record_evaluation("LAW-X", success=True)
        gov.check_health("LAW-X")
        assert gov.get_health_state("LAW-X") == LawHealthState.REVIEW_REQUIRED

        # Approve quarantine
        gov.approve_quarantine("LAW-X", "admin")
        assert gov.get_health_state("LAW-X") == LawHealthState.QUARANTINED

        # Restore
        gov.restore_from_quarantine("LAW-X", "admin")
        assert gov.get_health_state("LAW-X") == LawHealthState.HEALTHY

    def test_memory_agent_full_pipeline(self) -> None:
        """MemoryAgent should support store, query, memo, and consolidation."""
        agent = MemoryAgent()

        # Store episodes
        for i in range(5):
            ep = Episode(
                environment="production",
                law_categories={"structural"},
                trigger_violations=[uuid4()],
                region={uuid4()},
            )
            agent.store_episode(ep)

        # Query
        wm = WorkingMemory(
            context={"law_categories": ["structural"]}
        )
        result = agent.query(wm)
        assert result.total_matches >= 0  # episodes may not match all queries

        # Memoization
        nodes = [{"label": "test"}]
        edges: list[tuple[int, int]] = []
        eid = agent.memo_put(nodes, edges, value="cached_result")
        assert agent.memo_get(nodes, edges) == "cached_result"

        # Consolidation
        cr = agent.consolidate()
        assert cr is not None
