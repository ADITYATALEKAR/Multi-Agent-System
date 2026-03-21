//! ReasoningGraph — Core graph structure.

use crate::edge::RGEdge;
use crate::node::RGNode;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use uuid::Uuid;

/// Eviction policy for the Reasoning Graph.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EvictionPolicy {
    AttentionWeightedLRU,
    LRU,
    None,
}

/// The in-memory Reasoning Graph.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReasoningGraph {
    pub nodes: HashMap<Uuid, RGNode>,
    pub edges: HashMap<Uuid, RGEdge>,
    pub adjacency: HashMap<Uuid, HashSet<Uuid>>,
    pub type_index: HashMap<String, HashSet<Uuid>>,
    pub attribute_index: HashMap<String, HashMap<String, HashSet<Uuid>>>,
    pub hot_set: HashSet<Uuid>,
    pub cursor: u64,
    pub capacity: usize,
    pub eviction_policy: EvictionPolicy,
}

impl ReasoningGraph {
    /// Create a new empty ReasoningGraph.
    pub fn new(capacity: usize, eviction_policy: EvictionPolicy) -> Self {
        Self {
            nodes: HashMap::new(),
            edges: HashMap::new(),
            adjacency: HashMap::new(),
            type_index: HashMap::new(),
            attribute_index: HashMap::new(),
            hot_set: HashSet::new(),
            cursor: 0,
            capacity,
            eviction_policy,
        }
    }

    /// Get a node by ID.
    pub fn get_node(&self, node_id: &Uuid) -> Option<&RGNode> {
        self.nodes.get(node_id)
    }

    /// Get the number of nodes.
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    /// Get the number of edges.
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }
}
