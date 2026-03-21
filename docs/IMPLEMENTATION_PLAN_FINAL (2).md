# IMPLEMENTATION PLAN — FINAL
## State-Centric Multi-Agent Software Intelligence System
### Version: v3.3 (Base v3.1 + Delta v3.2 + Delta v3.3)
### Date: March 2026 | Classification: Confidential

---

## 1. EXECUTION RULES FOR CODEX AGENT

### 1.1 What the Agent MUST Follow

- The system architecture is defined as **three layered documents**: v3.1 (base), v3.2 (delta), v3.3 (delta). This plan references all three. When a section says "v3.1 base," that is the canonical specification. When it says "v3.2 patch" or "v3.3 patch," those modify the base at a specific point.
- Build **exactly** what each phase specifies. No more, no less.
- Create **stubs** for modules referenced by the current phase but implemented in later phases. Stubs must define the interface (function signatures, type hints, docstrings) but raise `NotImplementedError`.
- Every data structure must match the schema specified. Field names, types, and defaults are not suggestions.
- Every test specified in a phase must pass before proceeding to the next phase.
- Use the exact naming conventions defined in Section 2.
- Commit after every logical unit (one module, one test suite, one schema migration). Atomic commits.

### 1.2 What the Agent MUST NOT Do

- **No architectural deviation.** Do not invent new subsystems, rename core primitives, or restructure module boundaries.
- **No premature implementation.** If a module is listed under "What NOT to Build Yet," do not implement it, even if it seems useful. Create a stub only if another module imports it.
- **No hidden assumptions.** If the plan does not specify a behavior, the agent must create a `TODO(v3.x)` comment citing the missing specification and implement the simplest correct behavior.
- **No external dependencies** beyond those listed in the technology stack. No new frameworks, ORMs, or libraries without explicit approval.
- **No LLM calls** in any module except where explicitly specified (hypothesis_engine strategy 6, repair_planner strategy 5, explainer_agent). All other reasoning is deterministic.
- **No `print()` for logging.** Use `structlog` exclusively.
- **No raw SQL strings.** Use parameterized queries via asyncpg or SQLAlchemy Core.
- **No mutable global state.** All state is owned by explicit objects passed via dependency injection.

### 1.3 Handling Ambiguity

- If the plan says "configurable" without a default, use the first value listed in the v3.3 specification tables. If no table exists, use the most conservative option and add a `TODO(config)` comment.
- If two delta layers (v3.2 and v3.3) modify the same field, v3.3 takes precedence (later patch wins).
- If a data structure field is referenced in pseudocode but not defined in the schema section, add it to the schema with a `# Added: inferred from pseudocode in section X.Y` comment.

### 1.4 Execution Order, Dependency, and Phase Enforcement Rules

#### File Creation Order Rule

All implementation must follow this strict order within each phase:

1. Data models / schemas
2. Storage layer (DB tables, indexes, persistence)
3. Interfaces / APIs (method signatures, contracts)
4. Core logic (algorithms, engines, processing)
5. Integration wiring (connecting modules together)
6. Tests (unit → integration)
7. Observability (metrics, logging)

Do NOT implement integration or orchestration before data models and interfaces are fully defined.

#### Stub Dependency Rule

If a module depends on another module that is still a stub:

- Only rely on explicitly defined interfaces (method signatures, types, docstrings)
- Do NOT assume behavior, side effects, or internal logic
- Do NOT simulate or extend stub behavior beyond raising `NotImplementedError`

All stub interactions must be treated as opaque contracts.

#### Integration Rule

Modules must NOT be integrated prematurely.

Integration between modules is allowed ONLY when:

- Each module independently passes its unit tests
- All interfaces are fully defined
- Phase checklist requirements for those modules are satisfied

Do NOT build end-to-end flows until the required components are fully implemented.

#### Phase Gate Rule

Progression between phases is strictly gated.

The agent MUST:

- Complete all items in the current phase checklist
- Pass all tests for that phase
- Verify all exit criteria

If ANY checklist item fails:

- STOP progression
- Fix all issues within the current phase
- Re-run validation

Only proceed when the phase is fully complete.

---

## 2. REPOSITORY STRUCTURE (Before Phase 0)

```
blueprint-system/
├── README.md
├── pyproject.toml                    # Python project config (hatch/poetry)
├── Cargo.toml                        # Rust workspace root
├── docker-compose.yml                # Local dev: Neo4j, PostgreSQL, Redis, NATS
├── Makefile                          # Common commands: test, lint, build, migrate
│
├── docs/
│   ├── IMPLEMENTATION_PLAN_FINAL.md  # This file
│   ├── ARCHITECTURE_v3.1.pdf         # Base architecture reference
│   ├── DELTA_v3.2.pdf                # Delta 1 reference
│   ├── DELTA_v3.3.pdf                # Delta 2 reference
│   └── ADR/                          # Architecture Decision Records
│       └── 001-split-graph.md
│
├── config/
│   ├── defaults.yaml                 # All configurable parameters with defaults
│   ├── dev.yaml                      # Dev overrides
│   ├── test.yaml                     # Test overrides
│   └── schema_versions.yaml          # Schema version registry
│
├── migrations/
│   ├── pg/                           # PostgreSQL migrations (Alembic)
│   │   ├── env.py
│   │   └── versions/
│   └── neo4j/                        # Neo4j schema migrations (custom)
│       └── versions/
│
├── rust/                             # Rust workspace
│   ├── Cargo.toml
│   ├── reasoning-graph/              # In-memory Reasoning Graph core
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── graph.rs              # ReasoningGraph struct
│   │       ├── node.rs               # RGNode
│   │       ├── edge.rs               # RGEdge
│   │       ├── delta.rs              # Delta application
│   │       ├── fork.rs               # Copy-on-write fork for counterfactual
│   │       ├── temporal_index.rs     # In-memory TemporalIndex (v3.2 + v3.3 Fix 1)
│   │       ├── checkpoint.rs         # MessagePack serialization (v3.3 E1)
│   │       └── attention.rs          # AttentionScore computation
│   ├── wl-hash/                      # Weisfeiler-Lehman hashing
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       └── fingerprint.rs
│   └── py-bindings/                  # PyO3 bindings
│       ├── Cargo.toml
│       └── src/
│           └── lib.rs
│
├── src/
│   ├── __init__.py
│   ├── core/                         # Core primitives (Section III)
│   │   ├── __init__.py
│   │   ├── fact.py                   # Fact, DeltaOp, GraphDelta
│   │   ├── derived.py                # DerivedFact, ExtendedJustification
│   │   ├── contract.py               # Contract, TypeSpec, Predicate
│   │   ├── coordination.py           # WorkItem, Claim, Question, AgentBid
│   │   ├── runtime_event.py          # RuntimeEvent (v3.1 OSG)
│   │   ├── counterfactual.py         # CounterfactualScenario, Intervention
│   │   ├── certificate.py            # DiagnosisCertificate
│   │   └── config.py                 # SystemConfig loader
│   │
│   ├── state_graph/                  # Layer 1: World Model (Section IV)
│   │   ├── __init__.py
│   │   ├── schema.py                 # NodeType, EdgeType, SchemaRegistry
│   │   ├── delta_log.py              # GraphDeltaLog (PostgreSQL)
│   │   ├── delta_entities.py         # delta_entities table (v3.3 Fix 1)
│   │   ├── query_graph.py            # Neo4j materialized view
│   │   ├── reasoning_graph.py        # Python wrapper for Rust core
│   │   ├── delta_materializer.py     # Sync + async delta consumption
│   │   ├── semantic_cache.py         # Analyzer result cache
│   │   ├── temporal_index.py         # Python API over Rust TemporalIndex
│   │   ├── traversal_cache.py        # Redis traversal cache (v3.2 Risk Fix A)
│   │   ├── precomputed_indexes.py    # DependsClosure, BlastRadius, etc. (v3.2)
│   │   └── index_maintainer.py       # Delta consumer for precomputed indexes
│   │
│   ├── analyzers/                    # Layer 0: Sensing
│   │   ├── __init__.py
│   │   ├── harness.py                # Generic tree-sitter harness
│   │   ├── tier1/                    # 7 deep analyzers
│   │   │   ├── python_analyzer.py
│   │   │   ├── typescript_analyzer.py
│   │   │   ├── java_analyzer.py
│   │   │   ├── go_analyzer.py
│   │   │   ├── cpp_analyzer.py
│   │   │   ├── rust_analyzer.py
│   │   │   └── csharp_analyzer.py
│   │   ├── tier2/                    # 14 structural analyzers
│   │   │   └── structural_analyzer.py  # Generic with per-language configs
│   │   ├── tier3/                    # Infrastructure analyzers
│   │   │   ├── docker_analyzer.py
│   │   │   ├── k8s_analyzer.py
│   │   │   ├── terraform_analyzer.py
│   │   │   ├── ansible_analyzer.py
│   │   │   └── ci_analyzer.py
│   │   ├── tier4/                    # Data/API analyzers
│   │   │   ├── sql_analyzer.py
│   │   │   ├── graphql_analyzer.py
│   │   │   ├── protobuf_analyzer.py
│   │   │   └── openapi_analyzer.py
│   │   └── tier5/                    # Runtime evidence
│   │       ├── log_parser.py         # Drain algorithm
│   │       ├── stacktrace_parser.py
│   │       ├── otlp_parser.py
│   │       ├── metrics_parser.py
│   │       └── cloud_audit_parser.py
│   │
│   ├── ingestion/                    # Observation pipeline
│   │   ├── __init__.py
│   │   ├── pipeline.py               # ObservationRouter
│   │   ├── git_ingestion.py
│   │   ├── webhook_receiver.py
│   │   ├── file_watcher.py
│   │   ├── stream_consumer.py
│   │   ├── cloud_poller.py
│   │   └── api_submission.py
│   │
│   ├── dfe/                          # Layer 2: Derived Facts Engine
│   │   ├── __init__.py
│   │   ├── rete.py                   # ReteNetwork, AlphaNode, BetaNode
│   │   ├── compiler.py               # RuleParser, RuleIR, JoinOrderOptimizer
│   │   ├── attention.py              # GraphAttentionLayer (v3.1 + v3.2 + v3.3 C1)
│   │   └── derived_store.py          # DerivedFact storage and event emission
│   │
│   ├── tms/                          # Truth Maintenance System
│   │   ├── __init__.py
│   │   ├── engine.py                 # TMSEngine (v3.1 JTMS + v3.1 extended)
│   │   ├── belief.py                 # BeliefNode (v3.1 + v3.3 A3: tenant_id)
│   │   ├── confidence.py             # Confidence propagation (v3.1 + v3.3 B3: dampening)
│   │   └── index.py                  # TMSIndex
│   │
│   ├── law_engine/                   # Layer 3: Law Engine
│   │   ├── __init__.py
│   │   ├── law.py                    # LawDefinition (v3.1 + v3.2 + v3.3 Fix 3: 4-state)
│   │   ├── library.py                # 100+ law definitions
│   │   ├── evaluator.py              # Orchestrates Rete + Solver evaluation
│   │   └── governance.py             # Law health state machine (v3.3 Fix 3)
│   │
│   ├── hypothesis_engine/            # Layer 3: Hypothesis Engine
│   │   ├── __init__.py
│   │   ├── generator.py              # 6 strategies
│   │   ├── aggregator.py             # Dedup + ranking
│   │   ├── strategies/
│   │   │   ├── law_local.py
│   │   │   ├── graph_backward.py
│   │   │   ├── cross_service.py
│   │   │   ├── temporal.py
│   │   │   ├── memory_assisted.py
│   │   │   └── llm_assisted.py
│   │   └── template_matcher.py       # CausalTemplate matching (v3.2 Primitive 5)
│   │
│   ├── causal/                       # Layer 3: Causal RCA
│   │   ├── __init__.py
│   │   ├── cbn.py                    # CausalBayesianNetwork
│   │   ├── builder.py                # CBN construction from State Graph + OSG
│   │   ├── intervention.py           # Intervention scoring
│   │   └── discriminator.py          # Delta debugging + SBFL
│   │
│   ├── counterfactual/               # Layer 5: Counterfactual Simulation
│   │   ├── __init__.py
│   │   ├── engine.py                 # CounterfactualEngine (v3.1 base)
│   │   ├── boundary.py               # adaptive_simulation_boundary (v3.3 Fix 2)
│   │   └── replay.py                 # DeltaReplayEngine
│   │
│   ├── solver/                       # Layer 3: Constraint Solver
│   │   ├── __init__.py
│   │   ├── layer.py                  # ConstraintSolverLayer
│   │   ├── translator.py             # Z3Translator (SMT-LIB2, v3.3 A5)
│   │   ├── budget.py                 # SolverBudget (v3.2 + v3.3)
│   │   └── fallback.py               # Rete approximation, greedy resolver (v3.2)
│   │
│   ├── memory/                       # Layer 4: Memory System
│   │   ├── __init__.py
│   │   ├── types.py                  # WorkingMemory, Episode, Rule, Procedure, Pattern
│   │   ├── causal_template.py        # CausalTemplate (v3.2 Primitive 5)
│   │   ├── storage.py                # MemoryStore (hybrid backend)
│   │   ├── retrieval.py              # 6 retrieval strategies
│   │   ├── fingerprint.py            # WLFingerprint + MinHashLSH + two-level key (v3.3 A2)
│   │   ├── memoization.py            # Structural memoization cache (v3.2 + v3.3 C3)
│   │   ├── consolidation.py          # Post-episode pipeline
│   │   ├── abstraction.py            # CausalTemplate abstraction algorithm (v3.2)
│   │   └── agent.py                  # MemoryAgent
│   │
│   ├── repair/                       # Layer 5: Repair
│   │   ├── __init__.py
│   │   ├── planner.py                # RepairPlanner (5 strategies)
│   │   ├── verification.py           # VerificationEngine (5 modalities)
│   │   ├── scoring.py                # Multi-objective scoring J()
│   │   └── discriminator.py          # Delta debugging + SBFL
│   │
│   ├── iie/                          # Cross-cutting: Integration Integrity Engine
│   │   ├── __init__.py
│   │   ├── engine.py                 # IIEEngine
│   │   ├── architecture_ir.py        # ArchitectureIR, ComponentSpec, Connection
│   │   ├── passes/
│   │   │   ├── structural.py         # Pass 1
│   │   │   ├── dataflow.py           # Pass 2
│   │   │   ├── contract.py           # Pass 3
│   │   │   ├── determinism.py        # Pass 4
│   │   │   ├── circular_dep.py       # Pass 5
│   │   │   ├── split_graph.py        # Pass 6 (v3.1)
│   │   │   ├── nondeterminism.py     # Pass 7 (v3.1 + v3.3 B2: cardinality)
│   │   │   ├── stale_derived.py      # Pass 8 (v3.1)
│   │   │   ├── cache_lineage.py      # Pass 9 (v3.1)
│   │   │   ├── delta_consumption.py  # Pass 10 (v3.1)
│   │   │   ├── storage_budget.py     # Pass 11 (v3.2)
│   │   │   └── solver_budget.py      # Pass 12 (v3.2)
│   │   └── runtime_monitor.py        # IIERuntimeMonitor
│   │
│   ├── coordination/                 # Layer 6: Multi-Agent
│   │   ├── __init__.py
│   │   ├── blackboard.py             # BlackboardManager
│   │   ├── bidding.py                # BiddingProtocol + slot reservation (v3.3 D1)
│   │   ├── arbitration.py            # ConflictArbitrator
│   │   ├── orchestrator.py           # Orchestrator (governance layer)
│   │   ├── execution_policy.py       # Two-level: floor + ranking (v3.3 Fix 4)
│   │   ├── agents/
│   │   │   ├── base.py               # BaseAgent ABC
│   │   │   ├── repo_mapper.py
│   │   │   ├── law_engine_agent.py
│   │   │   ├── hypothesis_agent.py
│   │   │   ├── causal_rca_agent.py
│   │   │   ├── memory_agent.py
│   │   │   ├── repair_planner_agent.py
│   │   │   ├── verification_agent.py
│   │   │   ├── infra_ops_agent.py
│   │   │   ├── explainer_agent.py
│   │   │   └── executor_agent.py
│   │   ├── bus.py                    # MessageBus (asyncio / NATS)
│   │   ├── reliability.py            # AgentReliability tracking (v3.1)
│   │   └── multitenancy.py           # TenantRouter, NamespaceIsolator
│   │
│   ├── self_improving/               # Cross-cutting (v3.2 Primitive 3 + v3.3 Fix 3)
│   │   ├── __init__.py
│   │   ├── outcome_tracker.py        # OutcomeRecord storage
│   │   ├── law_weight_updater.py     # Law weight feedback
│   │   ├── strategy_prior_updater.py # Hypothesis strategy priors
│   │   └── attention_regressor.py    # GAL weight regression
│   │
│   ├── cost_aware/                   # Cross-cutting (v3.2 Primitive 4 + v3.3 Fix 4)
│   │   ├── __init__.py
│   │   ├── cost_tracker.py           # OperationCostTracker
│   │   └── value_estimator.py        # information_gain / compute_cost
│   │
│   ├── scoring/                      # Error/Energy scoring
│   │   ├── __init__.py
│   │   └── energy.py                 # EnergyScorer, HealthVector
│   │
│   ├── osg/                          # Operational Semantics Graph (v3.1)
│   │   ├── __init__.py
│   │   ├── materializer.py           # OSGMaterializer
│   │   ├── failure_propagation.py    # Failure path inference
│   │   └── temporal_order.py         # Happened-before ordering
│   │
│   ├── certificate/                  # Diagnosis Certificates
│   │   ├── __init__.py
│   │   ├── generator.py              # CertificateGenerator
│   │   └── verifier.py               # Independent verification
│   │
│   ├── executor/                     # Layer 7: Executor
│   │   ├── __init__.py
│   │   ├── agent.py                  # ExecutorAgent
│   │   └── adapters/
│   │       ├── git.py                # GitHub, GitLab, Bitbucket
│   │       ├── container.py          # K8s, Docker, ECS
│   │       ├── ci.py                 # GitHub Actions, GitLab CI, Jenkins
│   │       ├── iac.py                # Terraform, Pulumi, CloudFormation
│   │       ├── database.py           # Alembic, Flyway, Liquibase
│   │       └── alert.py              # PagerDuty, OpsGenie
│   │
│   ├── policy/                       # Policy Engine
│   │   ├── __init__.py
│   │   ├── engine.py                 # PolicyEngine
│   │   ├── yaml_rules.py             # YAML rule evaluator
│   │   └── opa.py                    # OPA/Rego integration
│   │
│   ├── api/                          # REST API
│   │   ├── __init__.py
│   │   ├── app.py                    # FastAPI app
│   │   ├── auth.py                   # OAuth2/JWT
│   │   ├── routes/
│   │   └── middleware/
│   │
│   ├── cli/                          # CLI
│   │   ├── __init__.py
│   │   └── main.py                   # Typer app
│   │
│   └── observability/                # Prometheus metrics, structured logging
│       ├── __init__.py
│       ├── metrics.py                # All Prometheus metrics (v3.3 E4)
│       └── logging.py                # structlog config
│
├── tests/
│   ├── conftest.py                   # Shared fixtures
│   ├── unit/                         # Mirror src/ structure
│   │   ├── core/
│   │   ├── state_graph/
│   │   ├── dfe/
│   │   ├── tms/
│   │   ├── law_engine/
│   │   └── ...
│   ├── integration/                  # Cross-module tests
│   │   ├── test_delta_pipeline.py    # Delta -> RG -> DFE -> TMS flow
│   │   ├── test_iie_bootstrap.py     # IIE load-time passes
│   │   └── ...
│   ├── stress/                       # Performance benchmarks
│   │   ├── test_dfe_stress.py
│   │   ├── test_query_engine_stress.py
│   │   └── ...
│   ├── chaos/                        # Chaos testing (Phase 6, v3.3 F6)
│   │   └── ...
│   └── accuracy/                     # Analyzer accuracy regression
│       └── ...
│
├── infra/
│   ├── helm/                         # Kubernetes Helm charts
│   ├── terraform/
│   │   ├── aws/
│   │   ├── gcp/
│   │   └── azure/
│   └── docker/
│       ├── Dockerfile.api
│       ├── Dockerfile.worker
│       └── Dockerfile.rust-builder
│
└── scripts/
    ├── migrate.py                    # Run all migrations
    ├── seed_laws.py                  # Seed law library
    ├── benchmark.py                  # Run benchmarks
    └── compact.py                    # Manual compaction trigger
```

### 2.1 Module Boundaries and Dependency Rules

- **Rust modules** (`rust/`): Only `reasoning-graph` and `wl-hash`. Python accesses via PyO3 bindings. No Python code calls Rust internals directly.
- **Core primitives** (`src/core/`): Imported by every module. Zero dependencies on other `src/` modules.
- **State graph** (`src/state_graph/`): Depends only on `core/`. All other modules access the graph through this package.
- **DFE** (`src/dfe/`): Depends on `core/`, `state_graph/`. Produces `DerivedFact` objects.
- **TMS** (`src/tms/`): Depends on `core/`. Consumes `DerivedFact` objects from DFE. Does NOT depend on DFE directly (event-driven).
- **Law Engine** (`src/law_engine/`): Depends on `core/`, `dfe/`, `solver/`. Compiles rules and registers them with DFE.
- **No circular imports.** Enforced by `import-linter` in CI.

### 2.2 Initially Stubbed Modules (Phase 0)

These modules exist as `__init__.py` + stub classes only until their implementation phase:

| Module | Stub Until |
|--------|-----------|
| `dfe/` | Phase 2 |
| `tms/` | Phase 2 |
| `law_engine/` | Phase 2 |
| `hypothesis_engine/` | Phase 2 |
| `causal/` | Phase 4 |
| `counterfactual/` | Phase 4 |
| `solver/` | Phase 3 |
| `memory/` | Phase 3 |
| `iie/` | Phase 3 |
| `repair/` | Phase 4 |
| `coordination/` | Phase 5 |
| `self_improving/` | Phase 2 (basic), Phase 5 (full) |
| `cost_aware/` | Phase 2 (basic), Phase 5 (full) |
| `executor/` | Phase 6 |
| `policy/` | Phase 6 |
| `api/` | Phase 6 |
| `cli/` | Phase 6 |
| `osg/` | Phase 4 |

---

## 3. PHASE 0 — PROJECT INITIALIZATION

**Prerequisite:** Complete before Phase 1 begins. No external dependencies.

### What to Do

1. **Repository creation.** Initialize Git repo with the folder structure above. All directories created. All `__init__.py` files created.

2. **Python project setup.**
   - `pyproject.toml` with hatch build system.
   - Python 3.12+ required.
   - Dependencies: `pydantic>=2.0`, `asyncpg`, `structlog`, `neo4j`, `redis`, `nats-py`, `z3-solver`, `tree-sitter`, `fastapi`, `uvicorn`, `typer`, `rich`, `prometheus-client`, `msgpack`, `xxhash`.
   - Dev dependencies: `pytest`, `pytest-asyncio`, `hypothesis`, `ruff`, `mypy`, `import-linter`, `locust`.

3. **Rust workspace setup.**
   - Cargo workspace with `reasoning-graph`, `wl-hash`, `py-bindings` crates.
   - PyO3 for Python bindings.
   - `maturin` for building Python wheels from Rust.

4. **Docker Compose.** Local development stack:
   - Neo4j 5.x (port 7687)
   - PostgreSQL 16 (port 5432)
   - Redis 7 (port 6379)
   - NATS JetStream (port 4222)

5. **CI pipeline (GitHub Actions).**
   - `lint`: ruff + mypy strict mode
   - `test-unit`: pytest unit tests
   - `test-integration`: pytest integration tests (requires Docker Compose)
   - `import-check`: import-linter (no circular imports)
   - `rust-build`: cargo build + cargo test
   - `accuracy-regression`: analyzer accuracy gate (added in Phase 1, v3.3 F1)

6. **Config system.**
   - `config/defaults.yaml` with ALL configurable parameters.
   - Config loaded by `src/core/config.py` using pydantic-settings.
   - Environment overrides via env vars: `BLUEPRINT__{section}__{key}`.

7. **Schema versioning.**
   - `config/schema_versions.yaml`: tracks current schema version for delta_log, delta_entities, neo4j graph, and all PostgreSQL tables.
   - Alembic initialized in `migrations/pg/`.

8. **Logging + observability.**
   - `src/observability/logging.py`: structlog config with JSON output.
   - `src/observability/metrics.py`: all Prometheus metrics defined as module-level objects (counters, histograms, gauges). Initially empty registrations; implementations added per phase.
   - OpenTelemetry trace context propagation configured.

9. **Delta compatibility.**
   - `GraphDelta.schema_version` field (v3.3 A1) implemented in `src/core/fact.py` from day 0.
   - All delta consumers check `schema_version` and reject unknown versions.

### Phase 0 Completion Checklist

- [ ] Repo compiles (`python -m pytest --collect-only` succeeds)
- [ ] Rust workspace builds (`cargo build` succeeds)
- [ ] Docker Compose starts all 4 services
- [ ] CI pipeline runs (all checks pass on empty test suite)
- [ ] Config loads from `defaults.yaml`
- [ ] structlog outputs JSON
- [ ] Prometheus metrics endpoint returns empty metrics
- [ ] All stub modules importable without error
- [ ] import-linter passes

---

## 4. PHASE-BY-PHASE IMPLEMENTATION PLAN

---

### PHASE 1 — FOUNDATION

#### Objective
Build the State Graph (all three tiers), the Universal Analyzer Framework (Tier 1–5), the Observation Ingestion Pipeline, and the Graph Query Engine.

#### Base v3.1 Components

- State Graph schema (88+ node types, 35+ edge types)
- GraphDeltaLog (PostgreSQL append-only)
- Query Graph (Neo4j materialized view)
- Reasoning Graph (Rust in-memory)
- Delta Materializer (sync + async paths)
- Semantic Cache / Normalization Layer
- tree-sitter analyzer harness
- Tier 1 analyzers (Python, TS, Java, Go, C++, Rust, C#)
- Tier 2 analyzers (14 languages)
- Tier 3 infrastructure analyzers
- Tier 4 data/API analyzers
- Tier 5 runtime evidence analyzers
- Observation Ingestion Pipeline
- Graph Query Engine

#### v3.2 Patches Applied in Phase 1

- **Temporal Index Layer** (Primitive 1): In-memory TemporalIndex (Rust), `entity_timeline`, `global_timeline`, `osg_timeline`.
- **Traversal Cache** (Risk Fix A): Redis cache with delta-driven invalidation.
- **Precomputed Structural Indexes** (Risk Fix A): DependsClosure, BlastRadiusIndex, ServiceBoundary, ImportResolution, CallGraph. IndexMaintainer.
- **DeltaLog compaction cron** (Risk Fix C): 30-day checkpoint policy.
- **StorageBudgetMonitor** (Risk Fix C): Prometheus metrics export.

#### v3.3 Patches Applied in Phase 1

- **Fix 1**: `delta_entities` normalized side table. Replace `scope[1]` index entirely. Fan-out on append. Correct entity_state_at/entity_diff SQL.
- **Fix 5**: Three-tier DeltaLog lifecycle (HOT/WARM/COLD). `delta_log_archive` table. Parquet cold export. Archival-first policy.
- **A1**: `GraphDelta.schema_version` field (already in Phase 0).
- **E1**: Reasoning Graph checkpoint (MessagePack, every 5 min) + recovery from checkpoint + delta replay.
- **E2**: Neo4j tenant isolation configuration (Option A for < 10 tenants).
- **E3**: SLO targets documented and measured.
- **E4**: Prometheus metrics for delta_append, RG application, query latency.
- **F1**: Analyzer accuracy regression gate in CI (90% entity, 80% deps).

#### Modules and Files to Create (fully implemented)

| Module | Files | Source |
|--------|-------|--------|
| `src/core/` | All files | v3.1 base |
| `src/state_graph/schema.py` | NodeType, EdgeType, SchemaRegistry | v3.1 |
| `src/state_graph/delta_log.py` | GraphDeltaLog, DeltaLogWriter, DeltaLogReader | v3.1 + v3.3 Fix 1 |
| `src/state_graph/delta_entities.py` | DeltaEntitiesWriter, entity_state_at, entity_diff | v3.3 Fix 1 |
| `src/state_graph/query_graph.py` | QueryGraphMaterializer, Neo4jGraphStore, GraphQueryEngine | v3.1 + v3.2 A |
| `src/state_graph/reasoning_graph.py` | Python wrapper for Rust ReasoningGraph | v3.1 + v3.3 E1 |
| `src/state_graph/delta_materializer.py` | DeltaMaterializer (sync + async) | v3.1 |
| `src/state_graph/semantic_cache.py` | SemanticCache | v3.1 |
| `src/state_graph/temporal_index.py` | Python API for Rust TemporalIndex | v3.2 + v3.3 Fix 1 |
| `src/state_graph/traversal_cache.py` | TraversalCache (Redis) | v3.2 A |
| `src/state_graph/precomputed_indexes.py` | DependsClosure, BlastRadiusIndex, etc. | v3.2 A |
| `src/state_graph/index_maintainer.py` | IndexMaintainer delta consumer | v3.2 A |
| `rust/reasoning-graph/` | All Rust files | v3.1 + v3.2 + v3.3 |
| `rust/wl-hash/` | WL fingerprinting | v3.1 |
| `src/analyzers/` | All analyzer files | v3.1 |
| `src/ingestion/` | All ingestion files | v3.1 |
| `src/observability/metrics.py` | Phase 1 metrics | v3.3 E4 |

#### Data Structures and Schemas

**PostgreSQL migrations (Phase 1):**

```sql
-- Migration 001: delta_log
CREATE TABLE delta_log (
    delta_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    sequence_number BIGSERIAL,
    timestamp TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    operations JSONB NOT NULL,
    scope UUID[] NOT NULL,
    causal_predecessor UUID,
    schema_version INT NOT NULL DEFAULT 1
) PARTITION BY LIST (tenant_id);

-- Migration 002: delta_entities (v3.3 Fix 1)
CREATE TABLE delta_entities (
    tenant_id TEXT NOT NULL,
    entity_id UUID NOT NULL,
    sequence_number BIGINT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    delta_id UUID NOT NULL,
    PRIMARY KEY (tenant_id, entity_id, sequence_number)
) PARTITION BY LIST (tenant_id);
CREATE INDEX idx_de_entity_time ON delta_entities (tenant_id, entity_id, timestamp);
CREATE INDEX idx_de_time ON delta_entities (tenant_id, timestamp, sequence_number);
CREATE INDEX idx_de_delta ON delta_entities (delta_id);

-- Migration 003: delta_log_archive (v3.3 Fix 5)
CREATE TABLE delta_log_archive (
    delta_id UUID PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    sequence_number BIGINT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    operations_compressed BYTEA NOT NULL,
    scope UUID[]
) PARTITION BY LIST (tenant_id);

-- Migration 004: semantic_cache
CREATE TABLE semantic_cache (
    cache_key TEXT PRIMARY KEY,
    file_hash TEXT NOT NULL,
    analyzer_version TEXT NOT NULL,
    toolchain_version TEXT NOT NULL,
    result JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    tenant_id TEXT NOT NULL
);
```

#### Step-by-Step Build Order

1. `src/core/` — all primitives (Step 1)
2. `rust/reasoning-graph/` — Rust core: graph, delta, fork, checkpoint, temporal_index (Step 2)
3. `rust/wl-hash/` — WL fingerprinting (Step 3)
4. `rust/py-bindings/` — PyO3 (Step 4)
5. `src/state_graph/schema.py` — schema registry (Step 5)
6. `src/state_graph/delta_log.py` + `delta_entities.py` — DeltaLog + entities (Step 6)
7. `src/state_graph/reasoning_graph.py` — Python wrapper (Step 7)
8. `src/state_graph/query_graph.py` — Neo4j store + query engine (Step 8)
9. `src/state_graph/delta_materializer.py` — sync + async (Step 9)
10. `src/state_graph/temporal_index.py` — Python API (Step 10)
11. `src/state_graph/traversal_cache.py` + `precomputed_indexes.py` + `index_maintainer.py` (Step 11)
12. `src/state_graph/semantic_cache.py` (Step 12)
13. `src/analyzers/harness.py` — tree-sitter harness (Step 13)
14. `src/analyzers/tier1/` — 7 deep analyzers (Step 14)
15. `src/analyzers/tier2/` — 14 structural (Step 15)
16. `src/analyzers/tier3/` — infrastructure (Step 16)
17. `src/analyzers/tier4/` — data/API (Step 17)
18. `src/analyzers/tier5/` — runtime evidence (Step 18)
19. `src/ingestion/` — full pipeline (Step 19)
20. Integration testing + benchmarking (Step 20)

#### Tests and Validation

- Schema: 500+ unit tests (valid/invalid nodes, edges, attributes).
- DeltaLog: property-based tests (Hypothesis library) for append-only, gap-free sequences.
- delta_entities: verify fan-out correctness for deltas with 1, 10, 100 scope entries.
- Reasoning Graph: fuzz test 10K random deltas, verify state matches DeltaLog replay.
- Query Graph: compare Neo4j state against DeltaLog for 10K deltas.
- Temporal Index: entity_state_at correctness on 10M deltas (< 5ms hot, < 50ms cold).
- Traversal Cache: hit rate > 50% under steady-state workload.
- Analyzer accuracy: 3 repos per Tier 1 language. 95% entity, 85% deps.
- CI gate: accuracy regression test fails build below 90%/80% (v3.3 F1).

#### Failure Modes

- **PostgreSQL down**: delta_log writes buffer to local filesystem, retry on reconnect.
- **Neo4j down**: Query Graph unavailable. DFE/TMS continue via Reasoning Graph. Complex traversals degrade to DeltaLog replay.
- **Reasoning Graph OOM**: eviction triggers. If insufficient, degrade to Neo4j-only.
- **Analyzer crash**: dead-letter queue, retry 3x, then skip + log.

#### What NOT to Build Yet

- DFE / Rete network (Phase 2)
- TMS (Phase 2)
- Law Engine (Phase 2)
- Hypothesis Engine (Phase 2)
- Any coordination / agent logic (Phase 5)
- IIE passes (Phase 3)
- Solver (Phase 3)
- Memory system (Phase 3)
- Keep stubs for all of the above.

#### Phase 1 Completion Checklist

- [ ] All 88+ node types and 35+ edge types defined, 500+ tests passing
- [ ] GraphDeltaLog: append, read, compact ops passing; delta_entities fan-out correct
- [ ] Reasoning Graph: sync delta application < 2ms p99
- [ ] Query Graph: async materialization within 500ms staleness bound
- [ ] Temporal Index: entity_state_at < 5ms hot, < 50ms cold on 10M deltas
- [ ] Traversal cache: hit rate > 50% under steady-state
- [ ] Precomputed indexes: update < 100ms per delta
- [ ] RG checkpoint: serialization + recovery < 10s for 100K nodes
- [ ] All Tier 1–5 analyzers passing accuracy targets
- [ ] Ingestion pipeline handling all source types end-to-end
- [ ] CI accuracy regression gate active
- [ ] SLO metrics being measured and reported
- [ ] Delta lifecycle (HOT/WARM/COLD) transitions tested

#### Exit Criteria

All checklist items pass. No item may be deferred.

---

### PHASE 2 — INCREMENTAL INTELLIGENCE

#### Objective
Build the DFE (Rete network + Rule Compiler), Graph Attention Layer, Extended TMS, Law Engine (100+ compiled laws), Hypothesis Engine (6 strategies), Error/Energy Scoring.

#### Base v3.1 Components
- Derived Facts Engine (Rete-inspired network, RuleIR, Rule Compiler)
- Truth Maintenance System (JTMS)
- Extended TMS (confidence propagation, competing hypotheses)
- Law Engine (100+ laws, 7 categories, Rete-compiled)
- Hypothesis Engine (6 generation strategies)
- Error/Energy Scoring

#### v3.2 Patches Applied in Phase 2
- **Graph Attention Layer** (upgrade): attention score formula, gates DFE, hypothesis budget, RCA scope.
- **Cost-Aware Reasoning** (Primitive 4, basic): OperationCostTracker, value() computation, cost-aware ordering for hypothesis strategies.
- **Self-Improving Layer** (Primitive 3, basic): OutcomeRecord storage, law weight updates, strategy prior updates.

#### v3.3 Patches Applied in Phase 2
- **B2**: BetaMemory per-rule cap (100K). Cardinality explosion warning at compile time.
- **B3**: TMS confidence dampening (epsilon 0.005).
- **A3**: tenant_id on BeliefNode.
- **C1**: New-violation attention boost (+0.3 for 5 min).
- **F2**: DFE stress test gate (200 rules, 100K nodes, p99 < 50ms) BEFORE TMS wiring.

#### Step-by-Step Build Order

1. `src/dfe/rete.py` — Rete network: AlphaNode, AlphaMemory, BetaNode, BetaMemory (Step 1)
2. `src/dfe/compiler.py` — RuleParser, RuleIR, JoinOrderOptimizer (Step 2)
3. `src/dfe/attention.py` — GraphAttentionLayer (v3.1 + v3.2 + v3.3 C1) (Step 3)
4. **GATE: DFE stress test** (v3.3 F2) — 200 rules, 100K nodes, p99 < 50ms. Must pass before Step 5.
5. `src/tms/` — TMSEngine, BeliefNode (with tenant_id), confidence propagation (with dampening), TMSIndex (Step 5)
6. `src/dfe/derived_store.py` — DerivedFact storage + TMS registration (Step 6)
7. `src/law_engine/law.py` — LawDefinition (with weight, health_state stub) (Step 7)
8. `src/law_engine/library.py` — 100+ laws (Step 8)
9. `src/law_engine/evaluator.py` — orchestrate Rete evaluation (Step 9)
10. `src/hypothesis_engine/` — all 6 strategies + aggregator (Step 10)
11. `src/scoring/energy.py` — EnergyScorer (Step 11)
12. `src/self_improving/outcome_tracker.py` + `law_weight_updater.py` + `strategy_prior_updater.py` (Step 12)
13. `src/cost_aware/cost_tracker.py` + `value_estimator.py` (Step 13)
14. Integration testing (Step 14)

#### Files Activated in This Phase

Files transitioning from stub → full implementation:

| File | Key Classes |
|------|-------------|
| `src/dfe/rete.py` | `ReteNetwork`, `AlphaNode`, `AlphaMemory`, `BetaNode`, `BetaMemory`, `PartialMatch` |
| `src/dfe/compiler.py` | `RuleParser`, `RuleAST`, `TypeChecker`, `JoinOrderOptimizer`, `RuleIR`, `RuleRegistry` |
| `src/dfe/attention.py` | `GraphAttentionLayer`, `AttentionScorer`, `AttentionIndex` |
| `src/dfe/derived_store.py` | `DerivedFactStore`, `DerivedFactEmitter` |
| `src/tms/engine.py` | `TMSEngine` |
| `src/tms/belief.py` | `BeliefNode` |
| `src/tms/confidence.py` | `ConfidencePropagator` |
| `src/tms/index.py` | `TMSIndex` |
| `src/law_engine/law.py` | `LawDefinition`, `LawCategory`, `EvalMode` |
| `src/law_engine/library.py` | 100+ law instances |
| `src/law_engine/evaluator.py` | `LawEvaluator` |
| `src/hypothesis_engine/generator.py` | `HypothesisGenerator` |
| `src/hypothesis_engine/aggregator.py` | `HypothesisAggregator`, `StructuralDeduplicator` |
| `src/hypothesis_engine/strategies/*.py` | `LawLocalStrategy`, `GraphBackwardStrategy`, `CrossServiceStrategy`, `TemporalStrategy`, `MemoryAssistedStrategy` (stub body), `LLMAssistedStrategy` |
| `src/scoring/energy.py` | `EnergyScorer`, `HealthVector`, `BlastRadiusComputer` |
| `src/self_improving/outcome_tracker.py` | `OutcomeTracker`, `OutcomeRecord` |
| `src/self_improving/law_weight_updater.py` | `LawWeightUpdater` |
| `src/self_improving/strategy_prior_updater.py` | `StrategyPriorUpdater` |
| `src/cost_aware/cost_tracker.py` | `OperationCostTracker`, `RunningStats` |
| `src/cost_aware/value_estimator.py` | `ValueEstimator` |

#### Interfaces / APIs to Define

```python
# src/dfe/rete.py
class ReteNetwork:
    def register_rule(self, rule_ir: RuleIR) -> None: ...
    def assert_fact(self, fact: Fact) -> list[DerivedFact]: ...
    def retract_fact(self, fact_id: UUID) -> list[UUID]: ...  # returns retracted DerivedFact IDs
    def get_partial_match_count(self, rule_id: str) -> int: ...

# src/dfe/compiler.py
class RuleCompiler:
    def compile(self, rule_text: str, schema: SchemaRegistry) -> RuleIR: ...
    def estimate_selectivity(self, rule_ir: RuleIR) -> float: ...

# src/dfe/attention.py
class GraphAttentionLayer:
    def compute_score(self, node_id: UUID, rg: ReasoningGraph) -> float: ...
    def recompute_affected(self, delta: GraphDelta, rg: ReasoningGraph) -> dict[UUID, float]: ...
    def get_priority_queue(self) -> list[tuple[float, UUID]]: ...

# src/tms/engine.py
class TMSEngine:
    def register_belief(self, derived: DerivedFact, justification: ExtendedJustification) -> BeliefNode: ...
    def retract_support(self, fact_id: UUID) -> list[UUID]: ...  # returns transitioned belief IDs
    def get_belief_status(self, belief_id: UUID) -> tuple[str, float]: ...  # (status, confidence)
    def get_consequences(self, belief_id: UUID) -> set[UUID]: ...

# src/law_engine/evaluator.py
class LawEvaluator:
    def register_laws(self, laws: list[LawDefinition], compiler: RuleCompiler, rete: ReteNetwork) -> None: ...
    def evaluate_delta(self, delta: GraphDelta) -> list[DerivedFact]: ...  # ViolationFacts
    def get_violations(self, tenant_id: str) -> list[DerivedFact]: ...

# src/hypothesis_engine/generator.py
class HypothesisGenerator:
    def generate(self, violations: list[DerivedFact], rg: ReasoningGraph,
                 memory: MemoryAgent, budget: ResourceBudget) -> list[DerivedFact]: ...

# src/hypothesis_engine/aggregator.py
class HypothesisAggregator:
    def aggregate(self, hypotheses: list[DerivedFact]) -> list[DerivedFact]: ...  # deduped, ranked

# src/scoring/energy.py
class EnergyScorer:
    def compute(self, violations: list[DerivedFact], rg: ReasoningGraph) -> HealthVector: ...

# src/self_improving/outcome_tracker.py
class OutcomeTracker:
    def record(self, outcome: OutcomeRecord) -> None: ...
    def get_records(self, target_id: UUID, record_type: str) -> list[OutcomeRecord]: ...

# src/cost_aware/value_estimator.py
class ValueEstimator:
    def estimate_value(self, operation_type: str, scope: set[UUID],
                       tracker: OperationCostTracker) -> float: ...
```

#### Failure Modes and Safeguards

- **BetaMemory explosion:** A rule with high-cardinality joins produces > 100K partial matches. Safeguard: per-rule cap enforced in `BetaMemory`. Rule flagged as `cardinality-explosive`. IIE Pass 7 detects at runtime. Rule Compiler warns at compile time if selectivity estimate > 50K.
- **TMS oscillation:** A belief flips IN/OUT repeatedly due to competing justifications. Safeguard: `BeliefNode.status_change_count` tracked. IIE Pass 7 flags oscillation > 3 flips. Orchestrator freezes belief for review.
- **DFE latency spike on bulk delta:** A delta with 100+ operations causes cascading Rete propagation. Safeguard: bulk-load detection (v3.3 B1 in precomputed indexes). DFE processes large deltas in chunks of 10 operations with yield points.
- **Confidence propagation cascade:** Tiny confidence change at leaf propagates through deep chains. Safeguard: dampening epsilon 0.005 (v3.3 B3). Propagation stops when abs(delta) < epsilon.
- **Law false positive storm:** A poorly written law produces thousands of violations on first evaluation. Safeguard: law weight starts at 1.0 and degrades via self-improving feedback. Per-law violation rate monitoring via Prometheus.
- **Hypothesis generation timeout:** LLM-assisted strategy takes > 30s. Safeguard: per-strategy timeout configurable (default 30s). Cost-aware layer skips LLM if cheaper strategies already produced confidence > 0.3.

#### What NOT to Build Yet
- Solver (Phase 3). Solver-backed laws are stubbed with `eval_mode = Solver` but evaluate as no-op.
- IIE (Phase 3). No validation passes run yet.
- Memory (Phase 3). memory_assisted hypothesis strategy uses stub.
- Causal RCA (Phase 4). causal strategy in hypothesis engine uses stub.
- Counterfactual (Phase 4). No counterfactual validation yet.
- Law governance 4-state model (Phase 3, v3.3 Fix 3). Law health_state is always HEALTHY in Phase 2.

#### Phase 2 Completion Checklist

- [ ] DFE Rete network: 200+ compiled rules, p99 < 50ms per delta on 100K nodes
- [ ] TMS: all DerivedFacts registered with justifications and confidence
- [ ] TMS confidence dampening: propagation stops below epsilon 0.005
- [ ] BeliefNode includes tenant_id
- [ ] BetaMemory per-rule cap enforced (100K)
- [ ] GAL: attention scores computed, new-violation boost active
- [ ] 100+ laws passing against curated repos (BugsInPy, Defects4J, etc.)
- [ ] Hypothesis Engine top-3 accuracy >= 65% on 50-case test suite
- [ ] Energy Scorer directionally correct
- [ ] Self-improving: OutcomeRecords being stored, law weights updating
- [ ] Cost-aware: OperationCostTracker recording costs

---

### PHASE 3 — MEMORY + IIE + SOLVER

#### Objective
Build the Memory System (5 types + CausalTemplate), IIE (12 passes), Constraint Solver (Z3), Structural Fingerprint Index, Memoization Caches, Law Governance.

#### Base v3.1 Components
- Memory (5 types, 6 retrieval strategies, consolidation)
- IIE (10 passes)
- Fingerprint Index (WL-hash + LSH)

#### v3.2 Patches
- Constraint Solver Layer (Z3), structural memoization caches, CausalTemplate (Primitive 5), IIE Pass 11+12, compaction policies.

#### v3.3 Patches
- **Fix 3**: 4-state law governance (HEALTHY/DEGRADED/REVIEW_REQUIRED/QUARANTINED).
- **A2**: Two-level memo key (WL-hash + canonical adjacency verification).
- **A5**: Contract.smt_constraints as SMT-LIB2 strings.
- **C3**: Environment in memo cache key.
- **F3**: IIE bootstrap validation against deliberately broken Architecture IR.

#### Files Activated in This Phase

| File | Key Classes |
|------|-------------|
| `src/memory/types.py` | `WorkingMemory`, `Episode`, `SemanticRule`, `Procedure`, `Pattern`, `RepairTemplate` |
| `src/memory/causal_template.py` | `CausalTemplate`, `AbstractGraph`, `AbstractNode`, `AbstractEdge` |
| `src/memory/storage.py` | `MemoryStore`, `PGMemoryBackend`, `Neo4jMemoryOverlay`, `ObjectStorageBackend` |
| `src/memory/retrieval.py` | `LawBasedRetrieval`, `GraphRegionRetrieval`, `CausalPatternRetrieval`, `EnvironmentFilter`, `RepairTypeRetrieval`, `PatternMatchRetrieval` |
| `src/memory/fingerprint.py` | `FingerprintIndex`, `MinHashLSH`, `TwoLevelMemoKey` |
| `src/memory/memoization.py` | `MemoizationCache`, `CacheLineageTracker` |
| `src/memory/consolidation.py` | `ConsolidationPipeline`, `PatternMatcher`, `RuleExtractor`, `ConfidenceAdjuster` |
| `src/memory/abstraction.py` | `TemplateAbstractor`, `ApproximateMCS` |
| `src/memory/agent.py` | `MemoryAgent` |
| `src/iie/engine.py` | `IIEEngine` |
| `src/iie/architecture_ir.py` | `ArchitectureIR`, `ComponentSpec`, `Connection`, `DataflowSpec` |
| `src/iie/passes/*.py` | `Pass1` through `Pass12` |
| `src/iie/runtime_monitor.py` | `IIERuntimeMonitor` |
| `src/solver/layer.py` | `ConstraintSolverLayer` |
| `src/solver/translator.py` | `Z3Translator` |
| `src/solver/budget.py` | `SolverBudget` |
| `src/solver/fallback.py` | `ReteFallback`, `GreedyVersionResolver` |
| `src/law_engine/governance.py` | `LawGovernance`, `LawHealthState` |

#### Interfaces / APIs to Define

```python
# src/memory/agent.py
class MemoryAgent:
    def query(self, working_memory: WorkingMemory) -> MemoryResult: ...
    def store_episode(self, episode: Episode) -> UUID: ...
    def get_similar_incidents(self, violations: list[DerivedFact], region: set[UUID]) -> list[Episode]: ...
    def get_known_rules(self, region: set[UUID], environment: str) -> list[SemanticRule]: ...
    def get_diagnostic_procedure(self, violation_pattern: bytes) -> Procedure | None: ...
    def get_pattern_match(self, signature: bytes) -> Pattern | None: ...
    def get_template_match(self, fingerprint: bytes, law_categories: set[str]) -> CausalTemplate | None: ...

# src/iie/engine.py
class IIEEngine:
    def run_load_time_passes(self, ir: ArchitectureIR) -> list[IntegrityViolation]: ...
    def start_runtime_monitor(self) -> None: ...
    def run_pass(self, pass_id: int, state: dict) -> list[IntegrityViolation]: ...

# src/solver/layer.py
class ConstraintSolverLayer:
    def check_satisfiability(self, constraints: list[str], budget: SolverBudget) -> SolverResult: ...
    def check_feasibility(self, target_laws: list[str], subgraph: SubgraphView) -> bool: ...

# src/memory/fingerprint.py
class FingerprintIndex:
    def insert(self, fingerprint: bytes, object_id: UUID) -> None: ...
    def query_exact(self, fingerprint: bytes) -> list[UUID]: ...
    def query_approximate(self, fingerprint: bytes, threshold: float) -> list[tuple[UUID, float]]: ...
    def verify_collision(self, fp: bytes, candidate_ids: list[UUID], rg: ReasoningGraph) -> list[UUID]: ...

# src/law_engine/governance.py
class LawGovernance:
    def evaluate_health(self, law: LawDefinition) -> LawHealthState: ...
    def request_review(self, law_id: str, reason: str) -> None: ...
    def approve_quarantine(self, law_id: str, reviewer: str) -> bool: ...
    def restore_from_quarantine(self, law_id: str, reviewer: str) -> bool: ...
```

#### Step-by-Step Build Order

1. `src/memory/types.py` — all memory object schemas (Step 1)
2. `src/memory/storage.py` — MemoryStore with PostgreSQL + Neo4j + S3 backends (Step 2)
3. `src/memory/fingerprint.py` — WL-hash index + two-level key verification (Step 3)
4. `src/memory/retrieval.py` — 6 retrieval strategies (Step 4)
5. `src/memory/memoization.py` — structural memoization cache (Step 5)
6. `src/memory/consolidation.py` — post-episode pipeline (Step 6)
7. `src/memory/causal_template.py` + `abstraction.py` — CausalTemplate type + abstraction (Step 7)
8. `src/memory/agent.py` — MemoryAgent (Step 8)
9. `src/solver/translator.py` — Z3Translator with SMT-LIB2 (Step 9)
10. `src/solver/budget.py` — SolverBudget enforcement (Step 10)
11. `src/solver/fallback.py` — Rete approximation + greedy resolver (Step 11)
12. `src/solver/layer.py` — ConstraintSolverLayer (Step 12)
13. `src/law_engine/governance.py` — 4-state law health model (Step 13)
14. `src/iie/architecture_ir.py` — ArchitectureIR data model (Step 14)
15. `src/iie/passes/*.py` — all 12 passes (Step 15)
16. `src/iie/engine.py` + `runtime_monitor.py` — IIE engine + runtime (Step 16)
17. **GATE: IIE bootstrap test** (v3.3 F3) — 100% defect detection on broken IR
18. Integration testing (Step 18)

#### Failure Modes and Safeguards

- **Memory retrieval returns stale results:** Memoized hypothesis from a previous investigation is no longer valid because the graph changed. Safeguard: delta-driven cache invalidation. IIE Pass 9 (cache lineage) spot-checks 10% of hits.
- **WL-hash collision in memo cache:** Two non-isomorphic subgraphs produce the same fingerprint. Safeguard: two-level key (v3.3 A2). Canonical adjacency matrix hash verified on every cache hit.
- **Solver timeout on complex constraints:** Z3 exceeds 200ms per call. Safeguard: SolverBudget hard limit. Complexity gating (SIMPLE/MODERATE/COMPLEX). Fallback to heuristic check with `confidence=0.5`.
- **IIE load-time pass blocks startup indefinitely:** A broken Architecture IR causes Pass 1 to loop. Safeguard: per-pass timeout (30s). If any pass exceeds timeout, emit CRITICAL alert and start in degraded mode.
- **Episode consolidation overwhelms database:** 100+ episodes close simultaneously after a large incident. Safeguard: consolidation is async (background job queue). Rate-limited to 10 concurrent consolidations per tenant.
- **CausalTemplate abstraction produces overly general template:** MCS algorithm merges dissimilar incidents. Safeguard: MCS similarity threshold 0.6. Templates with confidence < 0.3 after 10 matches are archived.

#### What NOT to Build Yet

- Repair Planner (Phase 4). Stub `repair.planner.generate()` returns empty list.
- Verification Engine (Phase 4). Stub `repair.verification.verify()` returns inconclusive.
- Causal RCA Engine (Phase 4). Stub `causal.cbn.build()` returns empty network.
- Counterfactual Engine (Phase 4). Stub `counterfactual.engine.validate()` returns Inconclusive.
- OSG Materializer (Phase 4). Stub `osg.materializer.process()` is no-op.
- Coordination / agents (Phase 5). All agent stubs remain.
- Executor / Policy (Phase 6). All stubs remain.

#### Phase 3 Completion Checklist

- [ ] All 5 memory types operational with full CRUD
- [ ] CausalTemplate (6th type) operational, abstraction algorithm producing valid templates from 5+ episodes
- [ ] WL-hash fingerprint index: precision > 80%, recall > 70%
- [ ] Two-level memo key: collision verification working
- [ ] Memoization cache: hit rate > 30% on repeated patterns
- [ ] IIE: all 12 passes operational. Load-time blocking confirmed. Runtime triggers verified.
- [ ] IIE bootstrap test: 100% defect detection on injected broken IR (v3.3 F3)
- [ ] Solver: Z3 integration passing. Budget enforced. Fallbacks working.
- [ ] Law governance: 4-state model active. No auto-disable. Quarantine requires human approval.
- [ ] Compaction: episode archival reduces storage > 40% for families with 15+ episodes

---

### PHASE 4 — ACTION + CAUSAL + SIMULATION

#### Objective
Build the Repair Planner, Verification Engine, Causal RCA Engine, OSG, Counterfactual Simulation Engine, Diagnosis Certificate Generator.

#### Base v3.1 Components
- Repair Planner, Verification Engine, Causal RCA Engine, OSG, Diagnosis Certificates.

#### v3.2 Patches
- Counterfactual Simulation Engine (selective subgraph).

#### v3.3 Patches
- **Fix 2**: Adaptive simulation boundary (replaces fixed cap).
- **B4**: Causal chain pinning for OSG events.
- **F4**: Counterfactual ground-truth validation (>= 7/10 on known cases).

#### Files Activated in This Phase

| File | Key Classes |
|------|-------------|
| `src/repair/planner.py` | `RepairPlanner`, `RepairTrajectory`, `RepairAction`, `GraphDeltaGen` |
| `src/repair/verification.py` | `VerificationEngine`, `StaticVerifier`, `GraphLawVerifier`, `DynamicVerifier`, `RegressionChecker`, `SecurityVerifier` |
| `src/repair/scoring.py` | `RepairScorer` |
| `src/repair/discriminator.py` | `DeltaDebugger`, `SBFLRanker` |
| `src/causal/cbn.py` | `CausalBayesianNetwork` |
| `src/causal/builder.py` | `CBNBuilder` |
| `src/causal/intervention.py` | `InterventionScorer` |
| `src/causal/discriminator.py` | `CausalDiscriminator` |
| `src/counterfactual/engine.py` | `CounterfactualEngine` |
| `src/counterfactual/boundary.py` | `AdaptiveSimulationBoundary` |
| `src/counterfactual/replay.py` | `DeltaReplayEngine` |
| `src/osg/materializer.py` | `OSGMaterializer` |
| `src/osg/failure_propagation.py` | `FailurePropagationInferrer` |
| `src/osg/temporal_order.py` | `TemporalOrderer` |
| `src/certificate/generator.py` | `CertificateGenerator` |
| `src/certificate/verifier.py` | `CertificateVerifier` |

#### Interfaces / APIs to Define

```python
# src/repair/planner.py
class RepairPlanner:
    def generate_candidates(self, violations: list[DerivedFact], rg: ReasoningGraph,
                            memory: MemoryAgent, solver: ConstraintSolverLayer) -> list[RepairTrajectory]: ...
    def score_candidates(self, candidates: list[RepairTrajectory]) -> list[RepairTrajectory]: ...  # sorted by J()

# src/repair/verification.py
class VerificationEngine:
    def verify(self, trajectory: RepairTrajectory, rg: ReasoningGraph,
               law_evaluator: LawEvaluator) -> VerificationResult: ...

# src/causal/builder.py
class CBNBuilder:
    def build_from_graph(self, rg: ReasoningGraph, osg_events: list[RuntimeEvent],
                         scope: set[UUID]) -> CausalBayesianNetwork: ...

# src/causal/intervention.py
class InterventionScorer:
    def score_candidates(self, cbn: CausalBayesianNetwork,
                         candidates: list[UUID]) -> list[tuple[UUID, float]]: ...  # (node_id, score)

# src/counterfactual/engine.py
class CounterfactualEngine:
    def validate_hypothesis(self, hypothesis: DerivedFact, rg: ReasoningGraph,
                            delta_log: GraphDeltaLog, temporal_index: TemporalIndex,
                            dfe: ReteNetwork, budget_ms: int) -> CounterfactualScenario: ...

# src/counterfactual/boundary.py
class AdaptiveSimulationBoundary:
    def compute(self, hypothesis: DerivedFact, rg: ReasoningGraph,
                budget_ms: int) -> tuple[set[UUID], int]: ...  # (boundary, expansion_count)

# src/osg/materializer.py
class OSGMaterializer:
    def process_event(self, event: RuntimeEvent, rg: ReasoningGraph) -> None: ...
    def infer_failure_propagation(self, window_start: datetime, window_end: datetime) -> list[RuntimeEvent]: ...

# src/certificate/generator.py
class CertificateGenerator:
    def generate(self, investigation_id: UUID, violations: list[DerivedFact],
                 root_cause: DerivedFact, repair: RepairTrajectory | None,
                 verification: VerificationResult | None,
                 tms: TMSEngine, counterfactuals: list[CounterfactualScenario]) -> DiagnosisCertificate: ...
```

#### Step-by-Step Build Order

1. `src/osg/` — OSGMaterializer, failure propagation, temporal ordering (Step 1)
2. `src/causal/cbn.py` + `builder.py` — CBN data model + construction (Step 2)
3. `src/causal/intervention.py` — intervention scoring (Step 3)
4. `src/causal/discriminator.py` — delta debugging + SBFL (Step 4)
5. `src/counterfactual/boundary.py` — adaptive simulation boundary (Step 5)
6. `src/counterfactual/replay.py` — delta replay engine (Step 6)
7. `src/counterfactual/engine.py` — counterfactual engine (Step 7)
8. **GATE: Counterfactual ground-truth** (v3.3 F4) — >= 7/10 on 10 known-cause scenarios
9. `src/repair/discriminator.py` — delta debugging + SBFL for repair (Step 9)
10. `src/repair/planner.py` — 5 generation strategies (Step 10)
11. `src/repair/scoring.py` — multi-objective J() function (Step 11)
12. `src/repair/verification.py` — 5 verification modalities (Step 12)
13. `src/certificate/` — generator + verifier (Step 13)
14. Integration testing (Step 14)

#### Failure Modes and Safeguards

- **Counterfactual boundary too small:** Adaptive expansion misses the true root cause. Safeguard: 3 expansion rounds with progressively lower threshold (v3.3 Fix 2). Hard cap 750 nodes. If all 3 expansions produce Inconclusive, result is explicitly tagged as such in the certificate.
- **CBN inference on large network:** CBN with > 200 nodes causes slow inference. Safeguard: CBN scope limited by attention threshold (default 0.2). Cost-aware layer gates invocation.
- **OSG window evicts active evidence:** Causal chain spans > 1 hour. Safeguard: causal chain pinning (v3.3 B4). Pinned events exempt from eviction.
- **Dynamic verification sandbox escape:** Malicious code in test repo executes outside Docker. Safeguard: rootless Docker, no host network access, resource limits (CPU: 2 cores, RAM: 2GB, timeout: 120s per sandbox).
- **Repair planner generates infinite candidates:** Combinatorial explosion of repair actions. Safeguard: per-strategy candidate cap (20). Total candidates before scoring: max 100.
- **Certificate verification fails on valid certificate:** Serialization mismatch between generator and verifier. Safeguard: round-trip test in CI: generate → serialize → deserialize → verify must pass for every test scenario.

#### What NOT to Build Yet

- Coordination / agents (Phase 5). All agent stubs remain. Repair planner and verification are invoked directly in tests, not through agent wrappers.
- Execution floors / two-level policy (Phase 5, v3.3 Fix 4).
- Executor / Policy Engine (Phase 6).
- REST API / CLI / Dashboard (Phase 6).

#### Phase 4 Completion Checklist

- [ ] Repair candidates for all 100+ violation types
- [ ] Verification: static catches 90%+ invalid repairs; dynamic validates in Docker for all Tier 1
- [ ] Causal RCA: CBN from State Graph + OSG operational. Top-1 accuracy > 50%
- [ ] Counterfactual: adaptive boundary working. < 5s per hypothesis. >= 7/10 ground-truth (v3.3 F4)
- [ ] OSG: runtime events processing, failure propagation inference
- [ ] Causal chain pinning: pinned events survive window eviction
- [ ] Certificates: machine-checkable, independently verifiable

---

### PHASE 5 — COORDINATION

#### Objective
Build the Blackboard + Contract-Net coordination model, all 10 specialist agents, Orchestrator, multi-tenancy, execution floors, and full self-improving/cost-aware integration.

#### Base v3.1 Components
- Blackboard + Contract-Net, BDI agents, Orchestrator, Multi-tenancy.

#### v3.2 Patches
- Utility-based bidding, conflict arbitration, anti-thrashing, reliability tracking, coordination limits.

#### v3.3 Patches
- **Fix 4**: Mandatory execution floors + two-level policy.
- **Fix 6**: Exact coordination limits, fast-path vs bidding criteria, claim dedup, triage mode with hysteresis.
- **D1**: Bid slot reservation.
- **D2**: ABANDONED state + escalation timeout.
- **D3**: Agent heartbeat + 60s timeout.
- **D4**: HypothesisAgent / CausalRCAAgent responsibility split.
- **C2**: Stalemate breaker for competing hypotheses.
- **A4**: incident_id on WorkItem.
- **F5**: Coordination overhead < 5% of task time.

#### Files Activated in This Phase

| File | Key Classes |
|------|-------------|
| `src/coordination/blackboard.py` | `BlackboardManager`, `WorkItemStore`, `ClaimStore`, `QuestionStore` |
| `src/coordination/bidding.py` | `BiddingProtocol`, `BidEvaluator`, `BidSlotReservation` |
| `src/coordination/arbitration.py` | `ConflictArbitrator`, `StalemateBreaker` |
| `src/coordination/orchestrator.py` | `Orchestrator`, `TaskLifecycleEngine`, `BudgetManager`, `TerminationChecker` |
| `src/coordination/execution_policy.py` | `ExecutionPolicy`, `FloorPolicy`, `RankingPolicy` |
| `src/coordination/agents/base.py` | `BaseAgent` |
| `src/coordination/agents/*.py` | All 10 specialist agents |
| `src/coordination/bus.py` | `MessageBus`, `TypedMessage` |
| `src/coordination/reliability.py` | `AgentReliabilityTracker`, `AgentReliability` |
| `src/coordination/multitenancy.py` | `TenantRouter`, `NamespaceIsolator`, `QuotaManager` |
| `src/self_improving/attention_regressor.py` | `AttentionRegressor` |

#### Interfaces / APIs to Define

```python
# src/coordination/blackboard.py
class BlackboardManager:
    def post_work_item(self, item: WorkItem) -> UUID: ...
    def post_claim(self, claim: Claim) -> UUID | None: ...  # None if deduped
    def post_question(self, question: Question) -> UUID: ...
    def claim_work_item(self, item_id: UUID, agent_id: str) -> bool: ...
    def complete_work_item(self, item_id: UUID, result: Any) -> None: ...
    def get_open_items(self, capabilities: set[str]) -> list[WorkItem]: ...
    def cleanup_stale(self) -> int: ...  # returns count of cleaned items

# src/coordination/bidding.py
class BiddingProtocol:
    def reserve_slot(self, work_item_id: UUID) -> bool: ...  # atomic Redis INCR
    def submit_bid(self, bid: AgentBid) -> None: ...
    def evaluate_bids(self, work_item_id: UUID) -> AgentBid | None: ...  # winner
    def should_fast_path(self, work_item: WorkItem) -> str | None: ...  # agent_id or None

# src/coordination/orchestrator.py
class Orchestrator:
    def submit_task(self, trigger: Any, tenant_id: str) -> UUID: ...  # returns task_id
    def run_task(self, task_id: UUID) -> None: ...
    def check_termination(self, task_id: UUID) -> bool: ...
    def enter_triage_mode(self) -> None: ...
    def exit_triage_mode(self) -> None: ...
    def is_triage_mode(self) -> bool: ...

# src/coordination/execution_policy.py
class ExecutionPolicy:
    def classify_operations(self, task: Any, operations: list) -> tuple[list, list]: ...  # (mandatory, ranked)
    def execute_with_floors(self, task: Any, operations: list, budget: ResourceBudget) -> list: ...

# src/coordination/agents/base.py
class BaseAgent(ABC):
    def accept_task(self, context: Any, budget: ResourceBudget) -> Any: ...
    def get_status(self) -> str: ...
    def abort(self) -> None: ...
    def get_capabilities(self) -> set[str]: ...
    def heartbeat(self) -> None: ...

# src/coordination/reliability.py
class AgentReliabilityTracker:
    def record_success(self, agent_id: str) -> None: ...
    def record_failure(self, agent_id: str) -> None: ...
    def record_crash(self, agent_id: str) -> None: ...  # harsher penalty
    def get_reliability(self, agent_id: str) -> float: ...
```

#### Step-by-Step Build Order

1. `src/coordination/agents/base.py` — BaseAgent ABC (Step 1)
2. `src/coordination/bus.py` — MessageBus (asyncio transport) (Step 2)
3. `src/coordination/blackboard.py` — BlackboardManager with limits + dedup + cleanup (Step 3)
4. `src/coordination/bidding.py` — BiddingProtocol with slot reservation + fast-path (Step 4)
5. `src/coordination/reliability.py` — AgentReliabilityTracker (Step 5)
6. `src/coordination/arbitration.py` — ConflictArbitrator + StalemateBreaker (Step 6)
7. `src/coordination/execution_policy.py` — two-level floor + ranking policy (Step 7)
8. `src/coordination/orchestrator.py` — Orchestrator with triage mode (Step 8)
9. `src/coordination/agents/*.py` — wrap all Phase 1–4 components as agents (Step 9)
10. `src/coordination/multitenancy.py` — TenantRouter + NamespaceIsolator (Step 10)
11. `src/self_improving/attention_regressor.py` — GAL weight regression (Step 11)
12. **GATE: Coordination overhead** (v3.3 F5) — < 5% of task time under 100 WorkItems
13. Integration + multi-tenant testing (Step 13)

#### Failure Modes and Safeguards

- **Bidding storm:** 10+ agents bid on a single high-value WorkItem simultaneously. Safeguard: atomic bid slot reservation (Redis INCR, cap=5). Agents check slot availability before preparing bid.
- **Agent crash mid-WorkItem:** Agent stops sending heartbeats. Safeguard: orchestrator polls heartbeats every 15s. 60s timeout → WorkItem released to OPEN, reliability decremented by 0.1.
- **Escalation black hole:** WorkItem escalated to human, no response. Safeguard: 30-minute escalation timeout → ABANDONED state. Episode closed as `unresolved_escalation_timeout`.
- **Blackboard bloat:** Claims and Questions accumulate faster than cleanup. Safeguard: stale item cleanup every 60s. Hard limits on pending claims (200) and questions (100). Excess evicted by age.
- **Triage mode flapping:** System oscillates between triage and normal mode. Safeguard: hysteresis gap — entry at 90% capacity, exit at 60%. Minimum 2 minutes in triage mode before exit evaluation.
- **Coordination overhead dominates task time:** Bid evaluation + assignment consumes > 5% of total. Safeguard: fast-path covers ~60% of tasks (zero bidding overhead). Prometheus metric tracks overhead percentage. Phase gate enforces < 5%.

#### What NOT to Build Yet

- Executor platform adapters (Phase 6). `executor_agent.py` wraps a stub executor.
- Policy Engine (Phase 6). Orchestrator uses a simple allow-all policy stub.
- REST API / CLI / Dashboard (Phase 6).
- Helm charts / Terraform / deployment (Phase 6).

#### Phase 5 Completion Checklist

- [ ] Blackboard primitives operational. Limits enforced.
- [ ] Fast-path covers > 50% of WorkItems
- [ ] Bid slot reservation: no wasted bid preparation
- [ ] Agent heartbeat: crashed agents detected within 60s
- [ ] ABANDONED state: escalation timeout -> ABANDONED -> episode stored
- [ ] Execution floors: mandatory ops always execute. Floor budget <= 40%
- [ ] Triage mode: entry at 90% capacity, exit at 60%. No flapping.
- [ ] Coordination overhead < 5% of task time under 100 WorkItems (v3.3 F5)
- [ ] Diagnostic accuracy > 70% on 30-scenario test suite
- [ ] Multi-tenancy: data isolation verified between 3+ tenants

---

### PHASE 6 — PRODUCTION

#### Objective
Build the Executor, Policy Engine, REST API, CLI, Web Dashboard, Platform Integrations, enterprise deployment, security hardening, chaos testing, production burn-in.

#### Base v3.1 Components
- Executor, Policy Engine, REST API, CLI, Dashboard, Platform Integrations, Deployment.

#### v3.2 Patches
- Compaction crons deployed. Storage budget alerts.

#### v3.3 Patches
- **Fix 5**: Full 3-tier retention lifecycle deployed.
- **E3/E4**: SLO monitoring dashboard. All Prometheus metrics active.
- **F6**: Chaos test suite (Neo4j down, Redis down, agent crash, PG down, RG OOM).

#### Files Activated in This Phase

| File | Key Classes |
|------|-------------|
| `src/executor/agent.py` | `ExecutorAgent` |
| `src/executor/adapters/git.py` | `GitAdapter` |
| `src/executor/adapters/container.py` | `ContainerAdapter` |
| `src/executor/adapters/ci.py` | `CIAdapter` |
| `src/executor/adapters/iac.py` | `IaCAdapter` |
| `src/executor/adapters/database.py` | `DatabaseAdapter` |
| `src/executor/adapters/alert.py` | `AlertAdapter` |
| `src/policy/engine.py` | `PolicyEngine` |
| `src/policy/yaml_rules.py` | `YAMLRuleEngine` |
| `src/policy/opa.py` | `OPAIntegration` |
| `src/api/app.py` | FastAPI application |
| `src/api/auth.py` | `AuthMiddleware` |
| `src/api/routes/*.py` | All API route handlers |
| `src/cli/main.py` | Typer CLI application |
| `tests/chaos/*.py` | All chaos test scenarios |

#### Interfaces / APIs to Define

```python
# src/executor/agent.py
class ExecutorAgent:
    def execute(self, trajectory: RepairTrajectory, policy_decision: PolicyDecision) -> ExecutionResult: ...
    def rollback(self, execution_id: UUID) -> bool: ...

# src/executor/adapters/git.py (pattern for all adapters)
class GitAdapter:
    def prepare(self, action: RepairAction) -> None: ...
    def validate_preconditions(self) -> bool: ...
    def execute(self) -> ExecutionResult: ...
    def verify_result(self) -> bool: ...
    def rollback(self) -> bool: ...

# src/policy/engine.py
class PolicyEngine:
    def evaluate(self, action: RepairAction, context: dict) -> PolicyDecision: ...
    def simulate(self, action: RepairAction, context: dict) -> PolicyDecision: ...  # no side effects

# src/api/app.py — key endpoints
# POST /api/v1/tasks — submit task
# GET  /api/v1/tasks/{id} — task status
# GET  /api/v1/violations — list violations
# GET  /api/v1/hypotheses/{task_id} — hypotheses for task
# GET  /api/v1/repairs/{task_id} — repair candidates
# POST /api/v1/repairs/{id}/approve — approve repair
# GET  /api/v1/memory/episodes — search episodes
# GET  /api/v1/graph/subgraph/{node_id} — subgraph extraction
# GET  /api/v1/health — system health
```

#### Step-by-Step Build Order

1. `src/policy/yaml_rules.py` — YAML rule evaluator (Step 1)
2. `src/policy/opa.py` — OPA/Rego integration (Step 2)
3. `src/policy/engine.py` — PolicyEngine with dual mode (Step 3)
4. `src/executor/adapters/*.py` — all 6 platform adapters (Step 4)
5. `src/executor/agent.py` — ExecutorAgent (Step 5)
6. `src/api/` — FastAPI app, auth, routes, middleware (Step 6)
7. `src/cli/main.py` — Typer CLI (Step 7)
8. Deploy compaction crons + StorageBudget alerts to PagerDuty (Step 8)
9. Deploy full 3-tier retention lifecycle (Step 9)
10. `infra/helm/` — Kubernetes Helm charts (Step 10)
11. `infra/terraform/` — AWS/GCP/Azure modules (Step 11)
12. Security hardening: mTLS, network policies, audit logging, secret rotation (Step 12)
13. `tests/chaos/` — chaos test suite (Step 13)
14. **GATE: Chaos tests** (v3.3 F6) — all scenarios pass, recovery < 120s
15. Production burn-in: 50+ scenarios, 2-week period (Step 15)

#### Failure Modes and Safeguards

- **Executor applies repair to wrong environment:** Policy Engine rejects actions where `action.environment != task.environment`. Safeguard: environment validation in every adapter's `validate_preconditions()`.
- **API rate limit bypass:** Authenticated user sends > allowed requests. Safeguard: per-tenant rate limiting via Redis sliding window. 429 response with Retry-After header.
- **Chaos test: Neo4j down:** Query Graph unavailable. Safeguard: system degrades to Reasoning Graph + DeltaLog replay for complex traversals. DFE/TMS continue normally.
- **Chaos test: Redis down:** All caches lost (traversal, memo, traversal). Safeguard: cache miss → fall through to database queries. Performance degrades but correctness maintained.
- **Chaos test: Agent crash:** Mid-WorkItem heartbeat timeout. Safeguard: WorkItem released, reliability decremented, task continues with re-assignment.
- **Chaos test: PostgreSQL down:** Delta writes buffer to local filesystem. Safeguard: circuit breaker with filesystem buffer. Replay on reconnect. Sequence numbers assigned on successful PG write only.
- **Chaos test: Reasoning Graph OOM:** Eviction triggered. Safeguard: attention-based eviction. If capacity still exceeded, degrade to Neo4j-only mode.

#### What NOT to Build Yet

Nothing. Phase 6 is the final phase. All stubs must be fully implemented by end of Phase 6.

#### Phase 6 Completion Checklist

- [ ] Executor: PRs, restarts, pipelines across GitHub, GitLab, K8s
- [ ] Policy Engine: all dimensions enforced
- [ ] API: 500+ concurrent, full OpenAPI, OAuth2, RBAC
- [ ] CLI: full workflow analyze->apply
- [ ] Dashboard: real-time graph, violations, hypotheses
- [ ] Deployable via Terraform to AWS/GCP/Azure
- [ ] Security: mTLS, audit logging, pen test passed
- [ ] Chaos tests: all pass, recovery < 120s, no data loss (v3.3 F6)
- [ ] 2-week burn-in: > 75% accuracy, < 5 min MTTR, zero data leaks
- [ ] Storage < 5GB/month/tenant, self-improvement converging

---

## 5. CROSS-PHASE DEPENDENCY MAP

```
Phase 0: Project init
  └─> Phase 1: Foundation (State Graph, Analyzers, Ingestion)
       └─> Phase 2: Intelligence (DFE, TMS, Laws, Hypotheses)
            ├─> Phase 3: Memory + IIE + Solver
            │    └─> Phase 4: Action + Causal + Simulation
            │         └─> Phase 5: Coordination
            │              └─> Phase 6: Production
            └─ (Phase 3 also depends on Phase 1 delta_log for compaction)
```

**Forbidden early implementations:**
- No Rete network before Phase 2
- No TMS before DFE stress test passes (v3.3 F2)
- No Solver before Phase 3
- No Counterfactual before Phase 4
- No Agent coordination before Phase 5
- No Executor/Policy before Phase 6

**Stubs that become real:**
- `memory.agent.query()` → stub in Phase 2 (returns empty), real in Phase 3
- `solver.layer.check()` → stub in Phase 2 (returns SAT), real in Phase 3
- `counterfactual.engine.validate()` → stub in Phase 2-3 (returns Inconclusive), real in Phase 4
- `coordination.orchestrator.run()` → stub in Phase 4 (sequential execution), real in Phase 5

---

## 6. CHECKLISTS

### After-Phase Checklists (summary — details in each phase above)

| Phase | Critical Gate |
|-------|--------------|
| 0 | Repo compiles, CI passes, Docker services up |
| 1 | Split graph working, analyzers accurate, delta lifecycle tested |
| 2 | DFE < 50ms p99, TMS correct, 100+ laws, hypotheses top-3 >= 65% |
| 3 | Memory retrieval P>80%/R>70%, IIE 12 passes, solver bounded |
| 4 | Counterfactual >= 7/10 ground truth, RCA top-1 > 50% |
| 5 | Coordination overhead < 5%, accuracy > 70%, triage mode stable |
| 6 | Chaos tests pass, 2-week burn-in clean |

### Final System Checklist

- [ ] **Graph correctness**: Reasoning Graph state matches DeltaLog replay for random 1000-delta sequences
- [ ] **Delta integrity**: All consumers (DFE, TMS, IIE, OSG, QG) at same cursor. Verified by IIE Pass 10.
- [ ] **Incremental computation**: No hidden full-recomputation paths. DFE delta cost proportional to delta size, not graph size.
- [ ] **TMS correctness**: Property test: no belief is IN with all justifications invalid. Confidence dampening active.
- [ ] **Solver correctness**: Budget never exceeded in production. Fallbacks produce tagged approximate results.
- [ ] **Counterfactual validation**: Adaptive boundary working. Ground-truth >= 7/10.
- [ ] **Memory + memoization**: Fingerprint index precision > 80%. Two-level key collision check active. Cache invalidation delta-driven.
- [ ] **Coordination stability**: No bidding storms (slot reservation). No retry loops (ABANDONED state). No stale items (cleanup sweep). Heartbeat timeout active.
- [ ] **IIE**: All 12 passes active. Load-time blocking. Runtime incremental. Bootstrap test 100%.
- [ ] **Law governance**: 4-state model. No auto-disable. Quarantine audit trail complete.
- [ ] **Execution floors**: Mandatory ops always execute. Floor budget <= 40%.
- [ ] **Retention**: 3-tier lifecycle. No data deleted without compliance approval.
- [ ] **Disaster recovery**: RG checkpoint + restore < 10s. All chaos tests pass.
- [ ] **SLOs**: All targets met for 2-week burn-in.
- [ ] **Observability**: All Prometheus metrics active. Structured logging with trace context.
- [ ] **Security**: mTLS, RBAC, audit log, pen test, no data leaks.

---

## 7. CODEX AGENT EXECUTION GUIDE

### How to Execute Phases

1. Read the phase section completely before writing any code.
2. Create all files listed in the "Modules and files to create" section as empty files first.
3. Implement in the step-by-step build order specified. Do not reorder.
4. After each step, run the relevant unit tests. Do not proceed if tests fail.
5. After all steps, run the full phase test suite.
6. Check every item in the phase completion checklist.
7. Only then proceed to the next phase.

### How to Avoid Scope Creep

- If you think a feature from a later phase would be "easy to add now," do not add it. Create a stub instead.
- If a test requires a later-phase module, mock it. Do not implement the real module.
- If you find a bug in the architecture specification, add a `TODO(architecture)` comment. Do not fix the architecture.

### How to Treat Ambiguity

- If a default value is not specified, use the most conservative option (lowest throughput, strictest limit, shortest timeout).
- If a data structure field type is unclear, prefer the narrowest explicit type first (e.g., `str` over `Any`, `UUID` over `str`, `list[UUID]` over `list`). Use `Any` only as a last resort and mark with `# TODO(type): clarify in section X.Y — using Any as fallback`.
- If two specification sections contradict, the later version number wins (v3.3 > v3.2 > v3.1).

### When to Create Stubs vs Full Systems

- **Create a stub** when: another module in the current phase imports it, but the module is not scheduled until a later phase.
- **Create a full system** when: the module is listed in the current phase's "Modules and files to create" table.
- **Stubs must**: define all public function signatures with correct type hints, have docstrings explaining what the function will do, raise `NotImplementedError("Implemented in Phase N")`.

### How to Maintain Architecture Fidelity

- Every class name must match the blueprint specification exactly.
- Every field name must match the blueprint specification exactly.
- Every module path must match the repository structure exactly.
- If the blueprint says "Rete-inspired network," implement a Rete network. Do not substitute with a different algorithm.
- If the blueprint says "JTMS," implement JTMS. Do not substitute with ATMS.
- If the blueprint says "Z3 via z3-solver," use z3-solver. Do not substitute with a different solver.

### File Creation Order Rule

Within each step of each phase, implement files in this order:

1. **Data models / schemas** — Pydantic models, dataclasses, enums, type definitions. These define what exists.
2. **Storage layer** — Database tables, migrations, PostgreSQL queries, Redis key schemes. These define where data lives.
3. **Interfaces** — Abstract base classes, protocol classes, public method signatures with type hints. These define contracts.
4. **Core logic** — The actual algorithms, computation, and business rules. These implement behavior.
5. **Integration wiring** — Event subscriptions, delta consumers, inter-module calls, dependency injection setup. These connect components.
6. **Tests** — Unit tests for core logic, integration tests for wiring, property-based tests for invariants.
7. **Observability** — Prometheus metrics instrumentation, structlog calls, trace context propagation for this module.

**Critical rule:** Do not implement integration wiring (step 5) before data models (step 1) and interfaces (step 3) exist. Wiring code that references undefined types will produce import errors and architectural drift.

---

*End of Implementation Plan. Begin with Phase 0.*
