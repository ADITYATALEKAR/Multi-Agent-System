"""Prometheus metrics definitions.

All metrics are defined as module-level objects. Implementations added per phase.
v3.3 E4: Required metric names and types.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── Ingestion metrics ────────────────────────────────────────────────────────

blueprint_ingestion_files_total = Counter(
    "blueprint_ingestion_files_total",
    "Total files successfully ingested",
)

blueprint_ingestion_errors_total = Counter(
    "blueprint_ingestion_errors_total",
    "Total ingestion errors",
)

blueprint_ingestion_duration_seconds = Histogram(
    "blueprint_ingestion_duration_seconds",
    "Duration of single-file ingestion (analysis step)",
    buckets=(0.005, 0.010, 0.025, 0.050, 0.100, 0.250, 0.500, 1.0),
)

# ── Delta / State Graph metrics ──────────────────────────────────────────────

blueprint_delta_append_duration_seconds = Histogram(
    "blueprint_delta_append_duration_seconds",
    "Duration of delta append operations (SLO: p99 < 10ms)",
    buckets=(0.001, 0.002, 0.005, 0.010, 0.020, 0.050, 0.100),
)

# ── DFE metrics ──────────────────────────────────────────────────────────────

blueprint_dfe_evaluation_duration_seconds = Histogram(
    "blueprint_dfe_evaluation_duration_seconds",
    "Duration of DFE incremental evaluation per delta (SLO: p99 < 50ms)",
    labelnames=("rule_id",),
    buckets=(0.005, 0.010, 0.020, 0.050, 0.100, 0.200),
)

# ── TMS metrics ──────────────────────────────────────────────────────────────

blueprint_tms_revision_duration_seconds = Histogram(
    "blueprint_tms_revision_duration_seconds",
    "Duration of TMS belief revision per retraction (SLO: p99 < 20ms)",
    buckets=(0.002, 0.005, 0.010, 0.020, 0.050),
)

# ── Solver metrics ───────────────────────────────────────────────────────────

blueprint_solver_invocation_total = Counter(
    "blueprint_solver_invocation_total",
    "Total solver invocations",
    labelnames=("complexity_class",),
)

blueprint_solver_budget_exhausted_total = Counter(
    "blueprint_solver_budget_exhausted_total",
    "Total solver budget exhaustion events",
)

# ── Counterfactual metrics ───────────────────────────────────────────────────

blueprint_counterfactual_expansions_total = Counter(
    "blueprint_counterfactual_expansions_total",
    "Total counterfactual boundary expansions",
)

# ── Law Engine metrics ───────────────────────────────────────────────────────

blueprint_law_quarantine_total = Counter(
    "blueprint_law_quarantine_total",
    "Total laws quarantined due to repeated failures",
)

# ── Coordination metrics ─────────────────────────────────────────────────────

blueprint_agent_heartbeat_timeout_total = Counter(
    "blueprint_agent_heartbeat_timeout_total",
    "Total agent heartbeat timeouts",
)

blueprint_triage_mode_active = Gauge(
    "blueprint_triage_mode_active",
    "Whether triage mode is active (0/1)",
)

# ── Memory / Memoization metrics ─────────────────────────────────────────────

blueprint_memo_cache_hit_ratio = Gauge(
    "blueprint_memo_cache_hit_ratio",
    "Memoization cache hit ratio",
    labelnames=("cache_type",),
)

# ── Phase 6: Executor metrics ───────────────────────────────────────────────

blueprint_executor_actions_total = Counter(
    "blueprint_executor_actions_total",
    "Total repair actions executed",
    labelnames=("adapter_type", "status"),
)

blueprint_executor_duration_seconds = Histogram(
    "blueprint_executor_duration_seconds",
    "Duration of repair action execution",
    labelnames=("adapter_type",),
    buckets=(0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 120.0),
)

blueprint_executor_rollbacks_total = Counter(
    "blueprint_executor_rollbacks_total",
    "Total repair rollbacks executed",
    labelnames=("adapter_type",),
)

# ── Phase 6: Policy metrics ────────────────────────────────────────────────

blueprint_policy_decisions_total = Counter(
    "blueprint_policy_decisions_total",
    "Total policy decisions made",
    labelnames=("decision", "environment"),
)

blueprint_policy_violations_total = Counter(
    "blueprint_policy_violations_total",
    "Total policy violations detected",
)

# ── Phase 6: API metrics ───────────────────────────────────────────────────

blueprint_api_requests_total = Counter(
    "blueprint_api_requests_total",
    "Total API requests",
    labelnames=("method", "endpoint", "status_code"),
)

blueprint_api_request_duration_seconds = Histogram(
    "blueprint_api_request_duration_seconds",
    "Duration of API requests",
    labelnames=("method", "endpoint"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

blueprint_api_rate_limited_total = Counter(
    "blueprint_api_rate_limited_total",
    "Total API requests rate-limited (429)",
    labelnames=("tenant_id",),
)

# ── Phase 6: Chaos / Recovery metrics ──────────────────────────────────────

blueprint_chaos_recovery_seconds = Histogram(
    "blueprint_chaos_recovery_seconds",
    "Recovery time after chaos event (SLO: < 120s)",
    labelnames=("failure_type",),
    buckets=(1.0, 5.0, 10.0, 30.0, 60.0, 90.0, 120.0, 180.0),
)

blueprint_system_health = Gauge(
    "blueprint_system_health",
    "Overall system health score (0-1)",
)
