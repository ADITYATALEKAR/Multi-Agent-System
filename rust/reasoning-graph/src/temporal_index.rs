//! In-memory TemporalIndex (v3.2 Primitive 1 + v3.3 Fix 1).
//!
//! Stub: full implementation in Phase 1.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

/// An entry in the temporal index.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TemporalEntry {
    pub timestamp: u64,
    pub entity_id: Uuid,
}

/// In-memory temporal index for time-range queries.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TemporalIndex {
    entries: Vec<TemporalEntry>,
}

impl TemporalIndex {
    /// Create a new empty TemporalIndex.
    pub fn new() -> Self {
        Self {
            entries: Vec::new(),
        }
    }

    /// Insert a timestamp-entity pair.
    ///
    /// # Stub
    /// Full sorted insertion in Phase 1.
    pub fn insert(&mut self, timestamp: u64, entity_id: Uuid) {
        self.entries.push(TemporalEntry {
            timestamp,
            entity_id,
        });
    }

    /// Query entities in a time range [start, end].
    ///
    /// # Stub
    /// Full binary-search implementation in Phase 1.
    pub fn query_range(&self, start: u64, end: u64) -> Vec<Uuid> {
        self.entries
            .iter()
            .filter(|e| e.timestamp >= start && e.timestamp <= end)
            .map(|e| e.entity_id)
            .collect()
    }
}
