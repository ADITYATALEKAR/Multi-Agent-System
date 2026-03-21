"""Shared test fixtures for the Blueprint system."""

from __future__ import annotations

import pytest

from src.core.config import BlueprintConfig, load_config


@pytest.fixture(scope="session")
def config() -> BlueprintConfig:
    """Load test configuration."""
    from pathlib import Path

    config_dir = Path(__file__).parent.parent / "config"
    return load_config(config_dir=config_dir, environment="test")


@pytest.fixture
def sample_graph_delta():
    """Create a minimal GraphDelta for testing."""
    from uuid import uuid4

    from src.core.fact import AddNode, GraphDelta

    node_id = uuid4()
    return GraphDelta(
        sequence_number=0,
        source="test",
        operations=[
            AddNode(node_id=node_id, node_type="service", attributes={"name": "test-svc"})
        ],
        scope={node_id},
    )
