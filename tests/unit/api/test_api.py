"""Unit tests for the Phase 6 API layer: routes, auth, and rate limiter."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.auth import AuthMiddleware
from src.api.middleware import RateLimiter

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


def test_chat_can_recommend_action(client):
    """Chat can return a backend-selected action recommendation."""
    resp = client.post("/api/v1/chat", json={"prompt": "analyze this workspace"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "action"
    assert body["recommended_action"] == "analyzeWorkspace"
    assert body["follow_up_actions"][0]["action"] == "analyzeWorkspace"


def test_chat_can_recommend_llm_connection(client):
    """Chat can direct the user into the simple LLM connection flow."""
    resp = client.post("/api/v1/chat", json={"prompt": "connect MAS to ChatGPT with my API key"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["intent"] == "action"
    assert body["recommended_action"] == "configureProvider"
    assert body["follow_up_actions"][0]["action"] == "configureProvider"


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
