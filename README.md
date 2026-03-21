# Blueprint System

State-Centric Multi-Agent Software Intelligence System (MASI)

## Quick Start

```bash
# Start infrastructure
make docker-up

# Install dependencies
pip install -e ".[dev]"

# Run migrations
make migrate

# Run tests
make test

# Lint
make lint
```

## Architecture

8-layer deterministic intelligence engine:

1. **Sensing** — Analyzers, ingestion, file watchers
2. **World Model** — Split Graph (DeltaLog + QueryGraph + ReasoningGraph)
3. **Incremental Computation** — DFE (Rete-based), TMS, attention
4. **Reasoning** — Law Engine, hypothesis generation, causal inference
5. **Memory** — Episodic, causal templates, memoization
6. **Action** — Repair planning, verification, execution
7. **Coordination** — Blackboard, bidding, multi-agent orchestration
8. **Production** — Certificates, policy, API, CLI
