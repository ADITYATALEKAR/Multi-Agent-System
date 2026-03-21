//! Reasoning Graph — In-memory graph core for the Blueprint system.
//!
//! This crate provides the high-performance, in-memory Reasoning Graph
//! with copy-on-write forking, attention-weighted eviction, temporal indexing,
//! and MessagePack checkpoint serialization.

pub mod attention;
pub mod checkpoint;
pub mod delta;
pub mod edge;
pub mod fork;
pub mod graph;
pub mod node;
pub mod temporal_index;
