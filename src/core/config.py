"""System configuration loader using pydantic-settings.

Loads from config/defaults.yaml with environment-specific overlays.
Environment variable overrides: BLUEPRINT__{section}__{key}
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Deep merge overlay into base, returning a new dict."""
    result = base.copy()
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ── Config Sections ──────────────────────────────────────────────────────────


class SystemConfig(BaseSettings):
    name: str = "blueprint-system"
    version: str = "0.1.0"
    environment: str = "production"
    log_level: str = "INFO"
    log_format: str = "json"


class PostgresConfig(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    database: str = "blueprint"
    user: str = "blueprint"
    password: str = "blueprint_dev"
    pool_min_size: int = 5
    pool_max_size: int = 20

    @property
    def dsn(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


class Neo4jConfig(BaseSettings):
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = "blueprint_dev"
    max_connection_pool_size: int = 50


class RedisConfig(BaseSettings):
    url: str = "redis://localhost:6379/0"
    max_connections: int = 20


class NatsConfig(BaseSettings):
    url: str = "nats://localhost:4222"
    jetstream_enabled: bool = True


class StateGraphConfig(BaseSettings):
    class DeltaLogConfig(BaseSettings):
        batch_size: int = 100
        flush_interval_ms: int = 500
        model_config = {"env_prefix": "BLUEPRINT__STATE_GRAPH__DELTA_LOG__"}

    class QueryGraphConfig(BaseSettings):
        cache_ttl_seconds: int = 300
        model_config = {"env_prefix": "BLUEPRINT__STATE_GRAPH__QUERY_GRAPH__"}

    class ReasoningGraphConfig(BaseSettings):
        capacity: int = 100000
        eviction_policy: str = "attention_weighted_lru"
        checkpoint_interval_seconds: int = 300
        checkpoint_recovery_target_seconds: int = 10
        model_config = {"env_prefix": "BLUEPRINT__STATE_GRAPH__REASONING_GRAPH__"}

    delta_log: DeltaLogConfig = Field(default_factory=DeltaLogConfig)
    query_graph: QueryGraphConfig = Field(default_factory=QueryGraphConfig)
    reasoning_graph: ReasoningGraphConfig = Field(default_factory=ReasoningGraphConfig)


class ObservabilityConfig(BaseSettings):
    metrics_enabled: bool = True
    metrics_port: int = 9090
    tracing_enabled: bool = True
    tracing_sample_rate: float = 1.0
    tracing_exporter: str = "otlp"


class ApiConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    read_timeout_ms: int = 500
    diagnostic_timeout_ms: int = 2000


class CoordinationConfig(BaseSettings):
    heartbeat_interval_seconds: int = 30
    heartbeat_timeout_seconds: int = 90
    max_agents: int = 20
    bidding_timeout_seconds: int = 5
    triage_mode_enabled: bool = False


# ── Root Config ──────────────────────────────────────────────────────────────


class BlueprintConfig(BaseSettings):
    """Root configuration for the Blueprint system.

    Load order:
    1. config/defaults.yaml
    2. config/{environment}.yaml overlay
    3. Environment variables (BLUEPRINT__{section}__{key})
    """

    model_config = {"env_prefix": "BLUEPRINT__", "env_nested_delimiter": "__", "extra": "ignore"}

    system: SystemConfig = Field(default_factory=SystemConfig)
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    nats: NatsConfig = Field(default_factory=NatsConfig)
    state_graph: StateGraphConfig = Field(default_factory=StateGraphConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    coordination: CoordinationConfig = Field(default_factory=CoordinationConfig)


def load_config(
    config_dir: Optional[Path] = None,
    environment: Optional[str] = None,
) -> BlueprintConfig:
    """Load configuration from YAML files with environment overlay.

    Args:
        config_dir: Path to config directory. Defaults to project config/.
        environment: Override environment name (default: from defaults.yaml).

    Returns:
        Fully resolved BlueprintConfig.
    """
    if config_dir is None:
        config_dir = Path(__file__).parent.parent.parent / "config"

    # Load defaults
    data = _load_yaml(config_dir / "defaults.yaml")

    # Determine environment
    env = environment or data.get("system", {}).get("environment", "production")

    # Load environment overlay
    env_data = _load_yaml(config_dir / f"{env}.yaml")
    if env_data:
        data = _deep_merge(data, env_data)

    return BlueprintConfig(**data)
