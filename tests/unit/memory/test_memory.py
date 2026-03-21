"""Comprehensive unit tests for the Memory subsystem (Phase 3).

Covers: types, storage, fingerprint, retrieval, memoization,
consolidation, causal_template, abstraction, and agent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

# ---------------------------------------------------------------------------
# Imports under test
# ---------------------------------------------------------------------------
from src.memory.types import (
    Episode,
    EpisodeOutcome,
    MemoryType,
    Pattern,
    SemanticRule,
    WorkingMemory,
)
from src.memory.storage import InMemoryBackend
from src.memory.fingerprint import (
    FingerprintIndex,
    MinHashLSH,
    TwoLevelMemoKey,
    wl_hash,
)
from src.memory.retrieval import (
    GraphRegionRetrieval,
    LawBasedRetrieval,
    PatternMatchRetrieval,
)
from src.memory.memoization import MemoizationCache
from src.memory.consolidation import (
    ConsolidationPipeline,
    PatternMatcher,
    RuleExtractor,
)
from src.memory.causal_template import (
    AbstractEdge,
    AbstractGraph,
    AbstractNode,
    CausalTemplate,
)
from src.memory.abstraction import TemplateAbstractor
from src.memory.agent import MemoryAgent


# ===================================================================
# 1. types (3 tests)
# ===================================================================


class TestTypes:
    """Tests for memory type definitions."""

    def test_episode_model(self):
        """Create an Episode with all fields, verify defaults."""
        ep = Episode()
        assert isinstance(ep.episode_id, UUID)
        assert ep.tenant_id == "default"
        assert isinstance(ep.incident_id, UUID)
        assert ep.trigger_violations == []
        assert ep.hypotheses_explored == []
        assert ep.root_cause_id is None
        assert ep.repair_actions == []
        assert ep.outcome == EpisodeOutcome.RESOLVED
        assert ep.environment == "production"
        assert ep.region == set()
        assert ep.law_categories == set()
        assert ep.fingerprint == b""
        assert ep.confidence == 0.0
        assert ep.duration_ms == 0.0
        assert isinstance(ep.created_at, datetime)
        assert ep.resolved_at is None
        assert ep.metadata == {}

        # Create with explicit fields
        eid = uuid4()
        iid = uuid4()
        rc = uuid4()
        ep2 = Episode(
            episode_id=eid,
            incident_id=iid,
            tenant_id="tenant-a",
            root_cause_id=rc,
            outcome=EpisodeOutcome.ESCALATED,
            environment="staging",
            confidence=0.85,
            law_categories={"structural", "security"},
        )
        assert ep2.episode_id == eid
        assert ep2.incident_id == iid
        assert ep2.tenant_id == "tenant-a"
        assert ep2.root_cause_id == rc
        assert ep2.outcome == EpisodeOutcome.ESCALATED
        assert ep2.environment == "staging"
        assert ep2.confidence == 0.85
        assert ep2.law_categories == {"structural", "security"}

    def test_semantic_rule_model(self):
        """Create a SemanticRule, verify fields."""
        rid = uuid4()
        rule = SemanticRule(
            rule_id=rid,
            description="Circular deps cause latency",
            condition="circular_deps > 3",
            conclusion="high latency expected",
            confidence=0.9,
            law_categories={"structural"},
            environment="production",
        )
        assert rule.rule_id == rid
        assert rule.description == "Circular deps cause latency"
        assert rule.condition == "circular_deps > 3"
        assert rule.conclusion == "high latency expected"
        assert rule.confidence == 0.9
        assert rule.law_categories == {"structural"}
        assert rule.environment == "production"
        assert rule.tenant_id == "default"
        assert rule.supporting_episodes == []
        assert rule.region == set()
        assert isinstance(rule.created_at, datetime)
        assert rule.last_validated is None
        assert rule.match_count == 0

    def test_working_memory_model(self):
        """Create WorkingMemory, verify defaults."""
        wm = WorkingMemory()
        assert isinstance(wm.incident_id, UUID)
        assert wm.tenant_id == "default"
        assert wm.violations == []
        assert wm.hypothesis_ids == []
        assert wm.attention_scores == {}
        assert wm.context == {}
        assert isinstance(wm.created_at, datetime)
        assert isinstance(wm.updated_at, datetime)

        # With explicit fields
        iid = uuid4()
        v1, v2 = uuid4(), uuid4()
        wm2 = WorkingMemory(
            incident_id=iid,
            tenant_id="tenant-b",
            violations=[v1, v2],
            attention_scores={"svc-a": 0.9},
            context={"law_categories": ["structural"]},
        )
        assert wm2.incident_id == iid
        assert wm2.tenant_id == "tenant-b"
        assert wm2.violations == [v1, v2]
        assert wm2.attention_scores == {"svc-a": 0.9}
        assert wm2.context == {"law_categories": ["structural"]}


# ===================================================================
# 2. storage (5 tests)
# ===================================================================


class TestStorage:
    """Tests for InMemoryBackend storage."""

    def test_store_and_get_episode(self):
        """Store an Episode, get it back by ID."""
        store = InMemoryBackend()
        ep = Episode(
            environment="staging",
            confidence=0.7,
            law_categories={"security"},
        )
        returned_id = store.store_episode(ep)
        assert returned_id == ep.episode_id

        retrieved = store.get_episode(ep.episode_id)
        assert retrieved is not None
        assert retrieved.episode_id == ep.episode_id
        assert retrieved.environment == "staging"
        assert retrieved.confidence == 0.7

        # Non-existent ID returns None
        assert store.get_episode(uuid4()) is None

    def test_query_episodes_with_filters(self):
        """Query episodes by environment, law_categories, outcome."""
        store = InMemoryBackend()

        ep1 = Episode(
            environment="production",
            law_categories={"structural", "security"},
            outcome=EpisodeOutcome.RESOLVED,
        )
        ep2 = Episode(
            environment="staging",
            law_categories={"structural"},
            outcome=EpisodeOutcome.MITIGATED,
        )
        ep3 = Episode(
            environment="production",
            law_categories={"security"},
            outcome=EpisodeOutcome.RESOLVED,
        )
        for ep in [ep1, ep2, ep3]:
            store.store_episode(ep)

        # Filter by environment
        prod = store.query_episodes(environment="production")
        assert len(prod) == 2
        assert all(e.environment == "production" for e in prod)

        # Filter by law_categories (subset check)
        structural = store.query_episodes(law_categories={"structural"})
        assert len(structural) == 2
        ids = {e.episode_id for e in structural}
        assert ep1.episode_id in ids
        assert ep2.episode_id in ids

        # Filter by outcome
        resolved = store.query_episodes(outcome=EpisodeOutcome.RESOLVED)
        assert len(resolved) == 2

        # Combined filter
        combined = store.query_episodes(
            environment="production",
            law_categories={"security"},
            outcome=EpisodeOutcome.RESOLVED,
        )
        assert len(combined) == 2

    def test_store_and_get_rule(self):
        """Store a SemanticRule, query by region."""
        store = InMemoryBackend()
        r1_uuid = uuid4()
        r2_uuid = uuid4()

        rule = SemanticRule(
            description="Test rule",
            condition="x > 5",
            conclusion="fail",
            region={r1_uuid, r2_uuid},
            environment="production",
        )
        returned_id = store.store_rule(rule)
        assert returned_id == rule.rule_id

        # Get by ID
        retrieved = store.get_rule(rule.rule_id)
        assert retrieved is not None
        assert retrieved.description == "Test rule"

        # Query by region overlap
        results = store.query_rules(region={r1_uuid})
        assert len(results) == 1
        assert results[0].rule_id == rule.rule_id

        # No overlap
        results_none = store.query_rules(region={uuid4()})
        assert len(results_none) == 0

    def test_store_and_get_pattern(self):
        """Store a Pattern, query by signature."""
        store = InMemoryBackend()
        sig = b"\x01\x02\x03\x04"
        pat = Pattern(
            name="Test pattern",
            signature=sig,
            occurrence_count=5,
            confidence=0.8,
        )
        returned_id = store.store_pattern(pat)
        assert returned_id == pat.pattern_id

        # Get by ID
        retrieved = store.get_pattern(pat.pattern_id)
        assert retrieved is not None
        assert retrieved.name == "Test pattern"

        # Query by signature
        by_sig = store.query_patterns_by_signature(sig)
        assert len(by_sig) == 1
        assert by_sig[0].pattern_id == pat.pattern_id

        # Different signature returns empty
        assert store.query_patterns_by_signature(b"\xff") == []

    def test_delete_episode(self):
        """Store, delete, verify gone."""
        store = InMemoryBackend()
        ep = Episode(confidence=0.5)
        store.store_episode(ep)

        assert store.get_episode(ep.episode_id) is not None
        assert store.count_episodes() == 1

        deleted = store.delete(MemoryType.EPISODIC, ep.episode_id)
        assert deleted is True
        assert store.get_episode(ep.episode_id) is None
        assert store.count_episodes() == 0

        # Deleting again returns False
        assert store.delete(MemoryType.EPISODIC, ep.episode_id) is False

        # Deleting non-existent ID returns False
        assert store.delete(MemoryType.EPISODIC, uuid4()) is False


# ===================================================================
# 3. fingerprint (5 tests)
# ===================================================================


class TestFingerprint:
    """Tests for WL-hash, MinHashLSH, TwoLevelMemoKey, FingerprintIndex."""

    def test_wl_hash_deterministic(self):
        """Same graph produces the same hash."""
        nodes = [{"label": "A"}, {"label": "B"}, {"label": "C"}]
        edges = [(0, 1), (1, 2)]

        h1 = wl_hash(nodes, edges)
        h2 = wl_hash(nodes, edges)
        assert h1 == h2
        assert isinstance(h1, bytes)
        assert len(h1) == 32  # SHA-256

    def test_wl_hash_different_graphs(self):
        """Different graphs produce different hashes."""
        nodes_a = [{"label": "A"}, {"label": "B"}]
        edges_a = [(0, 1)]

        nodes_b = [{"label": "X"}, {"label": "Y"}, {"label": "Z"}]
        edges_b = [(0, 1), (1, 2), (0, 2)]

        h_a = wl_hash(nodes_a, edges_a)
        h_b = wl_hash(nodes_b, edges_b)
        assert h_a != h_b

        # Empty graph also produces a hash
        h_empty = wl_hash([], [])
        assert isinstance(h_empty, bytes)
        assert h_empty != h_a

    def test_minhash_lsh_insert_and_query(self):
        """Insert identical graphs into LSH, query finds them."""
        lsh = MinHashLSH()
        id1 = uuid4()
        id2 = uuid4()
        id3 = uuid4()

        # Identical shingle sets -> guaranteed match
        shingles_a = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
        shingles_b = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}  # exact same
        shingles_c = {100, 200, 300, 400, 500, 600, 700, 800, 900, 1000}  # no overlap

        lsh.insert(id1, shingles_a)
        lsh.insert(id2, shingles_b)
        lsh.insert(id3, shingles_c)

        assert lsh.size == 3

        # Query with shingles_a should find id1 and id2 (identical)
        results = lsh.query(shingles_a, threshold=0.5)
        result_ids = {uid for uid, _ in results}
        assert id1 in result_ids
        assert id2 in result_ids
        # id3 should NOT be in results (completely different)
        assert id3 not in result_ids

    def test_two_level_memo_key_verify(self):
        """Compute a two-level key, verify match."""
        tk = TwoLevelMemoKey()
        nodes = [{"label": "svc"}, {"label": "db"}, {"label": "cache"}]
        edges = [(0, 1), (0, 2)]

        wl, canonical = tk.compute_key(nodes, edges, environment="prod")
        assert isinstance(wl, bytes)
        assert isinstance(canonical, bytes)
        assert len(wl) == 32
        assert len(canonical) == 32

        # Verify should return True for the same graph
        assert tk.verify(canonical, nodes, edges, environment="prod") is True

        # Different graph should fail verification
        other_nodes = [{"label": "alpha"}, {"label": "beta"}]
        other_edges = [(0, 1)]
        _, other_canonical = tk.compute_key(other_nodes, other_edges, "prod")
        assert tk.verify(other_canonical, nodes, edges, environment="prod") is False

        # Different environment should produce different keys
        wl_stg, can_stg = tk.compute_key(nodes, edges, environment="staging")
        assert wl_stg != wl
        assert can_stg != canonical

    def test_fingerprint_index_exact_and_approx(self):
        """Insert into FingerprintIndex, query exact and approximate."""
        idx = FingerprintIndex()
        id1 = uuid4()
        id2 = uuid4()

        nodes_a = [{"label": "svc"}, {"label": "db"}]
        edges_a = [(0, 1)]
        nodes_b = [{"label": "svc"}, {"label": "db"}, {"label": "cache"}]
        edges_b = [(0, 1), (0, 2)]

        fp1 = idx.insert(id1, nodes_a, edges_a, environment="prod")
        fp2 = idx.insert(id2, nodes_b, edges_b, environment="prod")
        assert idx.size == 2

        # Exact query
        exact_results = idx.query_exact(fp1)
        assert id1 in exact_results
        assert id2 not in exact_results

        # Approximate query (similar graphs)
        approx = idx.query_approximate(nodes_a, edges_a, threshold=0.3)
        approx_ids = {uid for uid, _ in approx}
        # At minimum the exact same graph should appear
        assert id1 in approx_ids


# ===================================================================
# 4. retrieval (3 tests)
# ===================================================================


class TestRetrieval:
    """Tests for retrieval strategies."""

    def test_law_based_retrieval(self):
        """Store episodes with categories, retrieve by law."""
        store = InMemoryBackend()
        ep1 = Episode(
            law_categories={"structural", "security"},
            environment="production",
            confidence=0.8,
        )
        ep2 = Episode(
            law_categories={"performance"},
            environment="production",
            confidence=0.6,
        )
        store.store_episode(ep1)
        store.store_episode(ep2)

        retriever = LawBasedRetrieval(store)

        # Query by structural category
        result = retriever.retrieve({"law_categories": ["structural"]})
        assert len(result.episodes) == 1
        assert result.episodes[0].episode_id == ep1.episode_id

        # Query by performance category
        result2 = retriever.retrieve({"law_categories": ["performance"]})
        assert len(result2.episodes) == 1
        assert result2.episodes[0].episode_id == ep2.episode_id

        # No matching category
        result3 = retriever.retrieve({"law_categories": ["nonexistent"]})
        assert len(result3.episodes) == 0

    def test_region_retrieval(self):
        """Store episodes with regions, retrieve by region overlap."""
        store = InMemoryBackend()
        r1, r2, r3 = uuid4(), uuid4(), uuid4()

        ep1 = Episode(region={r1, r2}, confidence=0.7)
        ep2 = Episode(region={r2, r3}, confidence=0.6)
        ep3 = Episode(region={uuid4()}, confidence=0.5)
        for ep in [ep1, ep2, ep3]:
            store.store_episode(ep)

        # Also store a rule with region overlap
        rule = SemanticRule(
            description="Region rule",
            condition="x",
            conclusion="y",
            region={r1},
        )
        store.store_rule(rule)

        retriever = GraphRegionRetrieval(store)

        # Query with r1 should find ep1 (has r1) and the rule
        result = retriever.retrieve({"region": [r1]})
        assert len(result.episodes) == 1
        assert result.episodes[0].episode_id == ep1.episode_id
        assert len(result.rules) == 1

        # Query with r2 should find ep1 and ep2
        result2 = retriever.retrieve({"region": [r2]})
        ep_ids = {e.episode_id for e in result2.episodes}
        assert ep1.episode_id in ep_ids
        assert ep2.episode_id in ep_ids

        # Empty region returns empty result
        result3 = retriever.retrieve({"region": []})
        assert result3.total_matches == 0

    def test_pattern_match_retrieval(self):
        """Store patterns, retrieve by signature."""
        store = InMemoryBackend()
        sig = b"\xab\xcd\xef"

        pat = Pattern(
            name="Match pattern",
            signature=sig,
            confidence=0.85,
        )
        store.store_pattern(pat)

        retriever = PatternMatchRetrieval(store)

        result = retriever.retrieve({"signature": sig})
        assert len(result.patterns) == 1
        assert result.patterns[0].pattern_id == pat.pattern_id
        assert result.total_matches >= 1

        # No match
        result2 = retriever.retrieve({"signature": b"\xff\xff"})
        assert len(result2.patterns) == 0

        # Empty signature returns empty
        result3 = retriever.retrieve({"signature": b""})
        assert result3.total_matches == 0


# ===================================================================
# 5. memoization (4 tests)
# ===================================================================


class TestMemoization:
    """Tests for the MemoizationCache."""

    def _make_graph(self, label: str = "svc"):
        """Helper to create a simple graph for memoization keys."""
        nodes = [{"label": label}, {"label": "db"}]
        edges = [(0, 1)]
        return nodes, edges

    def test_memo_put_and_get(self):
        """Put a value, get it back."""
        cache = MemoizationCache()
        nodes, edges = self._make_graph()

        entry_id = cache.put(nodes, edges, environment="prod", value={"result": 42})
        assert isinstance(entry_id, UUID)
        assert cache.size == 1

        retrieved = cache.get(nodes, edges, environment="prod")
        assert retrieved == {"result": 42}

    def test_memo_cache_miss(self):
        """Query non-existent key returns None."""
        cache = MemoizationCache()
        nodes, edges = self._make_graph("nonexistent")

        result = cache.get(nodes, edges, environment="prod")
        assert result is None

        # Also verify a miss after putting a different key
        other_nodes = [{"label": "alpha"}]
        other_edges: list[tuple[int, int]] = []
        cache.put(other_nodes, other_edges, environment="prod", value="other")

        result2 = cache.get(nodes, edges, environment="prod")
        assert result2 is None

    def test_memo_hit_ratio(self):
        """Put 3 entries, get 2 hits + 1 miss, verify hit_ratio."""
        cache = MemoizationCache()

        # Put 3 different entries
        graphs = [
            ([{"label": "A"}, {"label": "B"}], [(0, 1)]),
            ([{"label": "C"}, {"label": "D"}], [(0, 1)]),
            ([{"label": "E"}, {"label": "F"}], [(0, 1)]),
        ]
        for nodes, edges in graphs:
            cache.put(nodes, edges, environment="prod", value="ok")

        # 2 hits
        r1 = cache.get(graphs[0][0], graphs[0][1], environment="prod")
        assert r1 == "ok"
        r2 = cache.get(graphs[1][0], graphs[1][1], environment="prod")
        assert r2 == "ok"

        # 1 miss
        miss_nodes = [{"label": "X"}, {"label": "Y"}]
        r3 = cache.get(miss_nodes, [(0, 1)], environment="prod")
        assert r3 is None

        # hit_ratio = 2 / 3
        ratio = cache.hit_ratio()
        assert abs(ratio - 2.0 / 3.0) < 0.01

    def test_memo_invalidation(self):
        """Put with source_entities, invalidate, verify gone."""
        cache = MemoizationCache()
        entity1 = uuid4()
        entity2 = uuid4()
        nodes, edges = self._make_graph()

        cache.put(
            nodes, edges,
            environment="prod",
            value="cached-result",
            source_entities={entity1, entity2},
        )
        assert cache.get(nodes, edges, environment="prod") == "cached-result"

        # Invalidate by one of the source entities
        count = cache.invalidate({entity1})
        assert count > 0

        # Now it should be gone
        assert cache.get(nodes, edges, environment="prod") is None


# ===================================================================
# 6. consolidation (3 tests)
# ===================================================================


class TestConsolidation:
    """Tests for pattern matching, rule extraction, and the pipeline."""

    def test_pattern_matcher_finds_patterns(self):
        """5 episodes with same fingerprint produce a pattern."""
        fp = wl_hash([{"label": "svc"}, {"label": "db"}], [(0, 1)])

        episodes = [
            Episode(
                fingerprint=fp,
                law_categories={"structural"},
                outcome=EpisodeOutcome.RESOLVED,
                confidence=0.8,
            )
            for _ in range(5)
        ]

        matcher = PatternMatcher(min_cluster_size=3)
        patterns = matcher.find_patterns(episodes)
        assert len(patterns) == 1
        assert patterns[0].occurrence_count == 5
        assert patterns[0].signature == fp
        assert patterns[0].confidence > 0.0

    def test_rule_extractor_extracts_rules(self):
        """Episodes with shared categories produce a rule."""
        episodes = [
            Episode(
                law_categories={"structural", "security"},
                environment="production",
                outcome=EpisodeOutcome.RESOLVED,
                confidence=0.8,
            )
            for _ in range(5)
        ]

        extractor = RuleExtractor(min_episodes=3)
        rules = extractor.extract(episodes)
        assert len(rules) == 1
        assert rules[0].environment == "production"
        assert "structural" in rules[0].law_categories
        assert "security" in rules[0].law_categories
        assert rules[0].confidence > 0.0
        assert len(rules[0].supporting_episodes) == 5

    def test_consolidation_pipeline_archives(self):
        """20 episodes with same fingerprint: consolidate, verify archival."""
        store = InMemoryBackend()
        fp = wl_hash([{"label": "web"}, {"label": "api"}], [(0, 1)])

        for i in range(20):
            ep = Episode(
                fingerprint=fp,
                law_categories={"structural"},
                environment="production",
                outcome=EpisodeOutcome.RESOLVED,
                confidence=0.7,
            )
            store.store_episode(ep)

        assert store.count_episodes() == 20

        pipeline = ConsolidationPipeline(
            store,
            min_cluster_size=3,
            archive_threshold=15,
            archive_keep_recent=5,
        )
        result = pipeline.consolidate()

        assert result.patterns_extracted >= 1
        assert result.rules_extracted >= 1
        assert result.episodes_archived > 0
        assert result.episodes_total == 20
        # After archival, remaining episodes should be <= 5 (keep recent)
        assert store.count_episodes() <= 5
        assert result.compression_ratio > 0.4


# ===================================================================
# 7. causal_template (3 tests)
# ===================================================================


class TestCausalTemplate:
    """Tests for CausalTemplate matching, instantiation, and abstraction."""

    def _make_template(self) -> CausalTemplate:
        """Helper: create a template with 2 nodes and 1 edge."""
        n1 = AbstractNode(node_id="src", role="source", node_type="service", label="svc")
        n2 = AbstractNode(node_id="sink", role="sink", node_type="database", label="db")
        edge = AbstractEdge(source="src", target="sink", edge_type="causes", weight=0.9)
        graph = AbstractGraph(nodes=[n1, n2], edges=[edge])
        return CausalTemplate(
            name="Test template",
            graph=graph,
            law_categories={"structural"},
            confidence=0.8,
        )

    def test_causal_template_match(self):
        """Create template, match context, score > 0."""
        template = self._make_template()

        context = {
            "nodes": [
                {"type": "service", "name": "web"},
                {"type": "database", "name": "pg"},
            ],
            "edges": [
                {"type": "causes", "source": "web", "target": "pg"},
            ],
        }

        score = template.match(context)
        assert score > 0.0
        assert score <= 1.0

    def test_causal_template_instantiate(self):
        """Instantiate template with bindings."""
        template = self._make_template()

        bindings = {
            "src": {"name": "order-service", "id": "svc-001"},
            "sink": {"name": "postgres-primary", "id": "db-001"},
        }

        result = template.instantiate(bindings)
        assert result["template_id"] == str(template.template_id)
        assert result["name"] == "Test template"
        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1
        assert result["confidence"] == 0.8

        # Verify bindings are applied
        src_node = next(n for n in result["nodes"] if n["abstract_id"] == "src")
        assert src_node["concrete"]["name"] == "order-service"
        sink_node = next(n for n in result["nodes"] if n["abstract_id"] == "sink")
        assert sink_node["concrete"]["name"] == "postgres-primary"

        edge = result["edges"][0]
        assert edge["source_concrete"]["name"] == "order-service"
        assert edge["target_concrete"]["name"] == "postgres-primary"

    def test_template_abstractor(self):
        """5 episodes with causal_graph metadata produce a template."""
        causal_graph = {
            "nodes": [
                {"type": "service", "role": "source"},
                {"type": "database", "role": "sink"},
            ],
            "edges": [
                {
                    "source_type": "service",
                    "target_type": "database",
                    "edge_type": "causes",
                    "weight": 1.0,
                },
            ],
        }

        episodes = [
            Episode(
                law_categories={"structural"},
                confidence=0.8,
                metadata={"causal_graph": causal_graph},
            )
            for _ in range(5)
        ]

        abstractor = TemplateAbstractor(min_episodes=3, similarity_threshold=0.5)
        template = abstractor.abstract(episodes)

        assert template is not None
        assert isinstance(template, CausalTemplate)
        assert template.graph.node_count >= 1
        assert len(template.source_episodes) == 5
        assert template.fingerprint != b""
        assert "structural" in template.law_categories


# ===================================================================
# 8. agent (3 tests)
# ===================================================================


class TestMemoryAgent:
    """Tests for the MemoryAgent unified interface."""

    def test_memory_agent_store_and_query(self):
        """Store an episode, query with working memory."""
        agent = MemoryAgent()

        ep = Episode(
            law_categories={"structural", "security"},
            environment="production",
            confidence=0.9,
        )
        eid = agent.store_episode(ep)
        assert isinstance(eid, UUID)

        # Query with working memory that includes the law_categories
        wm = WorkingMemory(
            context={"law_categories": ["structural"]},
        )
        result = agent.query(wm)
        assert len(result.episodes) >= 1
        found_ids = {e.episode_id for e in result.episodes}
        assert ep.episode_id in found_ids

    def test_memory_agent_memo_round_trip(self):
        """memo_put, memo_get round-trip."""
        agent = MemoryAgent()

        nodes = [{"label": "svc"}, {"label": "db"}]
        edges = [(0, 1)]

        entry_id = agent.memo_put(
            nodes, edges,
            environment="prod",
            value={"diagnosis": "circular_dep"},
        )
        assert isinstance(entry_id, UUID)

        retrieved = agent.memo_get(nodes, edges, environment="prod")
        assert retrieved == {"diagnosis": "circular_dep"}

        # Miss for different graph
        miss = agent.memo_get([{"label": "x"}], [], environment="prod")
        assert miss is None

    def test_memory_agent_consolidate(self):
        """Store 20 episodes, consolidate."""
        agent = MemoryAgent()
        fp = wl_hash([{"label": "web"}, {"label": "api"}], [(0, 1)])

        for _ in range(20):
            ep = Episode(
                fingerprint=fp,
                law_categories={"structural"},
                environment="production",
                outcome=EpisodeOutcome.RESOLVED,
                confidence=0.7,
            )
            agent.store_episode(ep)

        result = agent.consolidate()
        assert result.patterns_extracted >= 1
        assert result.rules_extracted >= 1
        assert result.episodes_archived > 0
        assert result.episodes_total == 20
