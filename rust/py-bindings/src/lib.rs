//! PyO3 bindings for the Blueprint Rust crates.
//!
//! Built with maturin. Exposes ReasoningGraph and WL-hash to Python.
//! Stub: full bindings in Phase 1.

use pyo3::prelude::*;

/// Blueprint Rust bindings module.
#[pymodule]
fn blueprint_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", "0.1.0")?;
    Ok(())
}
