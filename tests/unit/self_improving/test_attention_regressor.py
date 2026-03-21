from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from src.self_improving.attention_regressor import AttentionRegressor, AttentionSample


# ── helpers ──────────────────────────────────────────────────────────────

def _make_sample(features: dict[str, float] | None = None, outcome: float = 0.5) -> AttentionSample:
    return AttentionSample(
        node_id=uuid4(),
        features=features or {"f1": 1.0, "f2": 0.5},
        attention_weight=0.5,
        outcome_score=outcome,
        timestamp=datetime.utcnow(),
    )


# ── tests ────────────────────────────────────────────────────────────────

def test_add_sample_and_count():
    reg = AttentionRegressor(min_samples=5, max_samples=100)
    assert reg.sample_count == 0
    reg.add_sample(_make_sample())
    assert reg.sample_count == 1


def test_fit_returns_none_below_min_samples():
    """fit() returns None when fewer than min_samples are present."""
    reg = AttentionRegressor(min_samples=10, max_samples=100)
    for _ in range(9):
        reg.add_sample(_make_sample())
    assert reg.fit() is None


def test_fit_returns_result_above_min():
    """fit() returns a RegressionResult when enough samples exist."""
    reg = AttentionRegressor(min_samples=5, max_samples=100)
    for i in range(10):
        reg.add_sample(_make_sample(
            features={"f1": float(i) / 10},
            outcome=float(i) / 10,
        ))
    result = reg.fit()
    assert result is not None
    assert result.samples_used == 10
    assert "f1" in result.updated_weights


def test_predict_clamped_to_0_1():
    """Predictions are clamped to [0, 1]."""
    reg = AttentionRegressor(min_samples=2, max_samples=100)
    # Manually set extreme weights
    reg._weights = {"f1": 100.0}
    assert reg.predict({"f1": 1.0}) == 1.0

    reg._weights = {"f1": -100.0}
    assert reg.predict({"f1": 1.0}) == 0.0


def test_eviction_at_max_samples():
    """Adding beyond max_samples evicts the oldest sample."""
    reg = AttentionRegressor(min_samples=1, max_samples=5)
    for i in range(7):
        reg.add_sample(_make_sample(features={"f1": float(i)}))
    assert reg.sample_count == 5


def test_reset_clears_all():
    """reset() clears samples and weights."""
    reg = AttentionRegressor(min_samples=2, max_samples=100)
    for _ in range(5):
        reg.add_sample(_make_sample())
    reg.fit()
    assert reg.sample_count > 0
    assert reg.is_fitted is True

    reg.reset()
    assert reg.sample_count == 0
    assert reg.is_fitted is False


def test_is_fitted_property():
    """is_fitted is False before fit, True after."""
    reg = AttentionRegressor(min_samples=2, max_samples=100)
    assert reg.is_fitted is False

    for i in range(5):
        reg.add_sample(_make_sample(
            features={"f1": float(i)},
            outcome=float(i) / 5,
        ))
    reg.fit()
    assert reg.is_fitted is True
