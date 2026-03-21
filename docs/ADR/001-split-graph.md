# ADR-001: Split Graph Architecture

## Status
Accepted

## Context
The system needs a world model that can handle high-throughput delta ingestion,
complex graph queries, and low-latency in-memory reasoning simultaneously.
A single graph store cannot satisfy all three requirements.

## Decision
Split the graph into three tiers:

1. **GraphDeltaLog** (PostgreSQL) — Append-only, immutable log of all deltas.
   Source of truth. Gap-free monotonic sequence numbers per tenant.

2. **Query Graph** (Neo4j) — Materialized view for complex graph traversals.
   Eventually consistent with the DeltaLog.

3. **Reasoning Graph** (Rust in-memory via PyO3) — Hot working set for
   real-time reasoning. Attention-weighted LRU eviction. Copy-on-write
   forking for counterfactual simulation.

## Consequences
- **Positive**: Each tier is optimized for its access pattern.
- **Positive**: DeltaLog provides complete audit trail and replay capability.
- **Negative**: Consistency between tiers requires careful delta materialization.
- **Negative**: Three stores to operate and monitor.

## Schema Versioning
All tiers share a `schema_version` field (v3.3 A1). Consumers reject unknown
versions with explicit errors.
