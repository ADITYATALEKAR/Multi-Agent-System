//! MessagePack serialization for ReasoningGraph checkpoints (v3.3 E1).
//!
//! Target: every 5 min, recovery < 10s for 100K nodes.
//! Stub: full implementation in Phase 1.

use crate::graph::ReasoningGraph;

/// Serialize a ReasoningGraph to MessagePack bytes.
///
/// # Stub
/// Full implementation in Phase 1.
pub fn serialize(graph: &ReasoningGraph) -> Result<Vec<u8>, String> {
    rmp_serde::to_vec(graph).map_err(|e| format!("Checkpoint serialize error: {}", e))
}

/// Deserialize a ReasoningGraph from MessagePack bytes.
///
/// # Stub
/// Full implementation in Phase 1.
pub fn deserialize(data: &[u8]) -> Result<ReasoningGraph, String> {
    rmp_serde::from_slice(data).map_err(|e| format!("Checkpoint deserialize error: {}", e))
}
