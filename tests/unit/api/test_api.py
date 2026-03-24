"""Unit tests for the Phase 6 API layer: routes, auth, and rate limiter."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth import AuthMiddleware
from src.api.middleware import RateLimiter
from src.runtime.service import get_runtime

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Create a fresh TestClient for each test."""
    app = create_app()
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    """GET /health -> 200 with healthy status."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"


def test_readiness_endpoint(client):
    """GET /health/ready -> 200 with healthy status."""
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"


# ---------------------------------------------------------------------------
# Task endpoints
# ---------------------------------------------------------------------------


def test_submit_task(client):
    """POST /api/v1/tasks -> 201 with task_id in response."""
    resp = client.post("/api/v1/tasks", json={"task_type": "analysis"})
    assert resp.status_code == 201
    body = resp.json()
    assert "task_id" in body
    assert body["status"] == "completed"


def test_get_task(client):
    """Submit a task, then GET /api/v1/tasks/{id} -> 200."""
    submit = client.post("/api/v1/tasks", json={"task_type": "analysis"})
    task_id = submit.json()["task_id"]
    resp = client.get(f"/api/v1/tasks/{task_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == task_id
    assert body["status"] == "completed"
    assert len(body["work_items"]) >= 1
    assert "violations" in body
    assert "repairs" in body


def test_submit_task_with_repo_path_completes_immediately(client):
    """Task submission with an explicit repo path should still run inline."""
    resp = client.post(
        "/api/v1/tasks",
        json={"task_type": "analysis", "repo_path": str(Path.cwd())},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"


def test_list_tasks(client):
    """GET /api/v1/tasks -> 200 with recent task records."""
    client.post("/api/v1/tasks", json={"task_type": "analysis"})
    resp = client.get("/api/v1/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) >= 1
    assert "task_id" in body[0]
    assert "violations" in body[0]


# ---------------------------------------------------------------------------
# Violations endpoint
# ---------------------------------------------------------------------------


def test_list_violations(client):
    """GET /api/v1/violations -> 200 with violations list."""
    resp = client.get("/api/v1/violations")
    assert resp.status_code == 200
    body = resp.json()
    assert "violations" in body
    assert isinstance(body["violations"], list)


# ---------------------------------------------------------------------------
# Repairs endpoint
# ---------------------------------------------------------------------------


def test_get_repairs(client):
    """GET /api/v1/repairs/{task_id} -> 200 with repairs list."""
    submit = client.post("/api/v1/tasks", json={"task_type": "analysis"})
    task_id = submit.json()["task_id"]
    resp = client.get(f"/api/v1/repairs/{task_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert "repairs" in body
    assert body["task_id"] == task_id


# ---------------------------------------------------------------------------
# Graph endpoint
# ---------------------------------------------------------------------------


def test_get_subgraph(client):
    """GET /api/v1/graph/subgraph/{node_id} -> 200 with subgraph."""
    resp = client.get("/api/v1/graph/subgraph/node-123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["root_id"] == "node-123"
    assert isinstance(body["nodes"], list)


# ---------------------------------------------------------------------------
# Memory endpoint
# ---------------------------------------------------------------------------


def test_search_episodes(client):
    """GET /api/v1/memory/episodes -> 200 with episodes list."""
    resp = client.get("/api/v1/memory/episodes", params={"query": "test"})
    assert resp.status_code == 200
    body = resp.json()
    assert "episodes" in body
    assert body["query"] == "test"


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


def test_chat_status(client):
    """POST /api/v1/chat -> status-aware response with backend reasoning."""
    resp = client.post("/api/v1/chat", json={"prompt": "what is the MAS status?"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "answer"
    assert "MAS is healthy" in body["answer"]
    assert body["summary"]
    assert "suggestions" in body
    assert "next_step" in body
    assert "files_changed" in body
    assert "code_changes" in body
    assert "cards" in body
    assert "follow_up_actions" in body


def test_chat_summarizes_latest_task(client):
    """Chat can summarize the latest analysis task from runtime state."""
    submit = client.post("/api/v1/tasks", json={"task_type": "analysis"})
    task_id = submit.json()["task_id"]
    resp = client.post(
        "/api/v1/chat",
        json={
            "prompt": "summarize the latest analysis",
            "task_id": task_id,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "answer"
    assert body["source_task_id"] == task_id
    assert task_id in body["answer"]
    assert "violations" in body["answer"]
    assert body["actions_taken"]
    assert body["files_in_focus"]
    assert "files_changed" in body
    assert "code_changes" in body


def test_chat_can_produce_project_summary(client):
    """Project-summary prompts should return a repo-level overview, not the generic fallback."""
    submit = client.post("/api/v1/tasks", json={"task_type": "analysis"})
    task_id = submit.json()["task_id"]
    resp = client.post(
        "/api/v1/chat",
        json={
            "prompt": "read my entire project and give me summary",
            "task_id": task_id,
            "repo_path": str(Path.cwd()),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "answer"
    assert body["source_task_id"] == task_id
    assert "multi-agent" in body["answer"].lower() or "workspace" in body["answer"].lower()
    assert "architecture:" in " ".join(card["title"] for card in body["cards"]).lower()
    assert body["files_changed"] == []
    assert body["code_changes"] == []


def test_chat_summary_includes_top_findings_for_problem_repo(client, tmp_path):
    """Summary-style replies should surface the main findings in human terms."""
    repo = tmp_path / "problem-repo"
    repo.mkdir()
    (repo / ".venv312").mkdir()
    (repo / ".masi_runtime").mkdir()
    (repo / "node_modules").mkdir()
    (repo / "temp").mkdir()
    (repo / "vscode-extension").mkdir()
    (repo / "vscode-extension" / "sample.vsix").write_text("placeholder", encoding="utf-8")

    submit = client.post(
        "/api/v1/tasks",
        json={"task_type": "analysis", "repo_path": str(repo)},
    )
    task_id = submit.json()["task_id"]

    resp = client.post(
        "/api/v1/chat",
        json={
            "prompt": "give me summary",
            "task_id": task_id,
            "repo_path": str(repo),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    card_titles = " ".join(card["title"] for card in body["cards"]).lower()
    assert "top finding:" in card_titles
    assert "top findings:" in body["answer"].lower()
    assert "virtual-environment" in body["answer"].lower() or "vsix" in body["answer"].lower()


def test_chat_does_not_reuse_stale_task_for_different_repo(client, tmp_path):
    """A stale task ID from one repo should not be reused for a different workspace prompt."""
    submit = client.post("/api/v1/tasks", json={"task_type": "analysis"})
    task_id = submit.json()["task_id"]
    other_repo = tmp_path / "other-repo"
    other_repo.mkdir()
    (other_repo / "README.md").write_text(
        "Different repo for MAS selection test\n",
        encoding="utf-8",
    )

    resp = client.post(
        "/api/v1/chat",
        json={
            "prompt": "read my repository",
            "task_id": task_id,
            "repo_path": str(other_repo),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "action"
    assert body["recommended_action"] == "analyzeWorkspace"
    assert "fresh workspace analysis" in body["answer"].lower()


def test_chat_can_recommend_action(client):
    """Chat can return a backend-selected action recommendation."""
    resp = client.post("/api/v1/chat", json={"prompt": "analyze this workspace"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "action"
    assert body["recommended_action"] == "analyzeWorkspace"
    assert body["follow_up_actions"][0]["action"] == "analyzeWorkspace"


def test_chat_project_summary_request_recommends_analysis_when_latest_task_is_pending(client):
    """Whole-project summary prompts should trigger analyze when the latest task is unfinished."""
    pending = get_runtime().enqueue_analysis(str(Path.cwd()))
    resp = client.post(
        "/api/v1/chat",
        json={
            "prompt": "read my entire project and give me summary",
            "task_id": pending.task_id,
            "repo_path": str(Path.cwd()),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "action"
    assert body["recommended_action"] == "analyzeWorkspace"
    assert "fresh workspace analysis" in body["answer"].lower()


def test_chat_can_recommend_llm_connection(client):
    """Chat can direct the user into the simple LLM connection flow."""
    resp = client.post("/api/v1/chat", json={"prompt": "connect MAS to ChatGPT with my API key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "action"
    assert body["recommended_action"] == "configureProvider"
    assert body["follow_up_actions"][0]["action"] == "configureProvider"


def test_chat_can_describe_changes_for_latest_task(client):
    """Chat can explain task progress in a more agent-style shape."""
    submit = client.post("/api/v1/tasks", json={"task_type": "analysis"})
    task_id = submit.json()["task_id"]
    resp = client.post("/api/v1/chat", json={"prompt": "what changed?", "task_id": task_id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_task_id"] == task_id
    assert body["summary"]
    assert "actions_taken" in body
    assert "files_in_focus" in body
    assert "files_changed" in body
    assert "code_changes" in body


def test_chat_can_inspect_a_specific_file(client):
    """Chat can inspect a concrete file path from the workspace."""
    resp = client.post(
        "/api/v1/chat",
        json={
            "prompt": "explain file src/runtime/chat.py",
            "repo_path": str(Path.cwd()),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "src/runtime/chat.py" in body["answer"] or "chat.py" in body["answer"]
    assert body["summary"]
    assert body["files_in_focus"]
    assert body["code_changes"]


def test_chat_can_prepare_an_edit_plan_for_a_file(client):
    """Chat can suggest a safe edit plan for a concrete file."""
    resp = client.post(
        "/api/v1/chat",
        json={
            "prompt": "how should we edit src/runtime/chat.py",
            "repo_path": str(Path.cwd()),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]
    assert body["actions_taken"]
    assert body["files_in_focus"]
    assert body["next_step"]
    assert body["cards"]


def test_chat_can_focus_on_a_symbol_inside_a_file(client):
    """Chat can inspect a specific symbol within a file."""
    resp = client.post(
        "/api/v1/chat",
        json={
            "prompt": "explain function answer in src/runtime/chat.py",
            "repo_path": str(Path.cwd()),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbols_in_focus"]
    assert "answer" in " ".join(body["symbols_in_focus"]).lower()


def test_chat_can_recommend_applying_approved_edits(client):
    """Chat can explicitly switch into the apply-approved-edits action."""
    resp = client.post(
        "/api/v1/chat",
        json={"prompt": "apply approved edits to src/runtime/chat.py"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "action"
    assert body["recommended_action"] == "applyApprovedEdits"


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


def test_auth_create_and_verify_token():
    """Create a token and verify it decodes correctly."""
    auth = AuthMiddleware()
    token = auth.create_token(user_id="user-1", tenant_id="acme", roles=["admin"])
    payload = auth.verify_token(token)
    assert payload is not None
    assert payload.sub == "user-1"
    assert payload.tenant_id == "acme"
    assert "admin" in payload.roles


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def test_rate_limiter():
    """check_rate returns True until the limit is reached, then False."""
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    assert limiter.check_rate("tenant-a") is True
    assert limiter.check_rate("tenant-a") is True
    assert limiter.check_rate("tenant-a") is True
    # Fourth request exceeds the limit
    assert limiter.check_rate("tenant-a") is False
