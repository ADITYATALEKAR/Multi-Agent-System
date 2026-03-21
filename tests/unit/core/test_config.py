"""Unit tests for BlueprintConfig."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.config import BlueprintConfig, load_config


class TestBlueprintConfig:
    def test_load_defaults(self) -> None:
        config_dir = Path(__file__).parent.parent.parent.parent / "config"
        cfg = load_config(config_dir=config_dir, environment="test")
        assert isinstance(cfg, BlueprintConfig)

    def test_default_values(self) -> None:
        cfg = BlueprintConfig()
        assert cfg.system is not None
        assert cfg.postgres is not None
        assert cfg.observability is not None

    def test_nested_config(self) -> None:
        cfg = BlueprintConfig()
        assert cfg.state_graph is not None

    def test_extra_fields_ignored(self) -> None:
        # v3.3: extra="ignore" should allow unknown fields
        cfg = BlueprintConfig.model_validate({"unknown_field": "value"})
        assert cfg.system is not None
