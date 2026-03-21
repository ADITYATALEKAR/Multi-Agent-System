from __future__ import annotations

from src.coordination.agents import (
    BaseAgent,
    CausalRCAAgent,
    ExecutorAgent,
    ExplainerAgent,
    HypothesisAgent,
    InfraOpsAgent,
    LawEngineAgent,
    MemoryAgent,
    RepairPlannerAgent,
    RepoMapperAgent,
    VerificationAgent,
)
from src.core.coordination import WorkItem


ALL_AGENT_CLASSES = [
    RepoMapperAgent,
    LawEngineAgent,
    HypothesisAgent,
    CausalRCAAgent,
    MemoryAgent,
    RepairPlannerAgent,
    VerificationAgent,
    InfraOpsAgent,
    ExplainerAgent,
    ExecutorAgent,
]


def _make_item(**kwargs) -> WorkItem:
    defaults = {"task_type": "repo_map", "priority": 0.8}
    defaults.update(kwargs)
    return WorkItem(**defaults)


# ── class hierarchy ──────────────────────────────────────────────────────

def test_all_agents_extend_base_agent():
    """All 10 specialist agents are BaseAgent subclasses."""
    for cls in ALL_AGENT_CLASSES:
        assert issubclass(cls, BaseAgent), f"{cls.__name__} does not extend BaseAgent"


def test_agent_capabilities():
    """Each agent has non-empty CAPABILITIES."""
    for cls in ALL_AGENT_CLASSES:
        agent = cls()
        caps = agent.get_capabilities()
        assert len(caps) > 0, f"{cls.__name__} has empty CAPABILITIES"


def test_agent_ids_unique():
    """All 10 agents have distinct AGENT_ID values."""
    ids = [cls.AGENT_ID for cls in ALL_AGENT_CLASSES]
    assert len(ids) == len(set(ids)), f"Duplicate AGENT_IDs found: {ids}"


# ── execute stubs ────────────────────────────────────────────────────────

def test_repo_mapper_execute():
    agent = RepoMapperAgent()
    item = _make_item(task_type="repo_map")
    result = agent.execute(item)
    assert isinstance(result, dict)
    assert "nodes_mapped" in result


def test_law_engine_execute():
    agent = LawEngineAgent()
    item = _make_item(task_type="law_check")
    result = agent.execute(item)
    assert isinstance(result, dict)
    assert "violations_found" in result


def test_hypothesis_execute():
    agent = HypothesisAgent()
    item = _make_item(task_type="hypothesis_generate")
    result = agent.execute(item)
    assert isinstance(result, dict)
    assert "hypotheses_generated" in result


def test_causal_rca_execute():
    agent = CausalRCAAgent()
    item = _make_item(task_type="causal_analysis")
    result = agent.execute(item)
    assert isinstance(result, dict)
    assert "root_causes" in result


def test_executor_stub():
    agent = ExecutorAgent()
    item = _make_item(task_type="execute_repair")
    result = agent.execute(item)
    assert isinstance(result, dict)
    assert result.get("execution_status") == "stub_ok"
