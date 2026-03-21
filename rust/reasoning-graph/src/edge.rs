//! RGEdge — Reasoning Graph edge.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

/// An edge in the Reasoning Graph.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RGEdge {
    pub edge_id: Uuid,
    pub src_id: Uuid,
    pub tgt_id: Uuid,
    pub edge_type: String,
    pub attributes: HashMap<String, serde_json::Value>,
}

impl RGEdge {
    /// Create a new RGEdge.
    pub fn new(edge_id: Uuid, src_id: Uuid, tgt_id: Uuid, edge_type: String) -> Self {
        Self {
            edge_id,
            src_id,
            tgt_id,
            edge_type,
            attributes: HashMap::new(),
        }
    }
}
