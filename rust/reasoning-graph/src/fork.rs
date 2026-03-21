//! Copy-on-write fork for counterfactual simulation.
//!
//! Stub: full implementation in Phase 4.

use crate::graph::ReasoningGraph;

/// Fork a ReasoningGraph for counterfactual simulation (copy-on-write).
///
/// # Stub
/// Full implementation in Phase 4.
pub fn fork(graph: &ReasoningGraph) -> Result<ReasoningGraph, String> {
    // Phase 0: basic clone. Phase 4 will implement true CoW.
    Ok(graph.clone())
}
