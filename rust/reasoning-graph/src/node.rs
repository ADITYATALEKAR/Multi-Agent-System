//! RGNode — Reasoning Graph node.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

/// A node in the Reasoning Graph.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RGNode {
    pub node_id: Uuid,
    pub node_type: String,
    pub attributes: HashMap<String, serde_json::Value>,
    pub attention_score: f64,
    pub last_accessed: u64,
    pub derived_facts: Vec<Uuid>,
}

impl RGNode {
    /// Create a new RGNode.
    pub fn new(node_id: Uuid, node_type: String) -> Self {
        Self {
            node_id,
            node_type,
            attributes: HashMap::new(),
            attention_score: 0.0,
            last_accessed: 0,
            derived_facts: Vec::new(),
        }
    }
}
