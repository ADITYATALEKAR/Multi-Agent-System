"""GAL attention-weight regression from diagnostic outcomes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from pydantic import BaseModel

log = structlog.get_logger(__name__)


# ── Data models ────────────────────────────────────────────────────────────


class AttentionSample(BaseModel):
    """Single training sample for attention-weight regression."""

    node_id: UUID
    features: dict[str, float]
    attention_weight: float
    outcome_score: float  # actual diagnostic outcome (0-1)
    timestamp: datetime


class RegressionResult(BaseModel):
    """Result of a regression fitting pass."""

    updated_weights: dict[str, float]  # feature_name -> new weight
    r_squared: float
    samples_used: int
    converged: bool


# ── Regressor ──────────────────────────────────────────────────────────────


class AttentionRegressor:
    """Regresses GAL attention weights based on diagnostic outcomes.

    Maintains a sliding window of ``AttentionSample`` instances and performs
    simple gradient-descent linear regression so that predicted attention
    weights converge toward observed outcome scores.
    """

    def __init__(
        self,
        learning_rate: float = 0.01,
        max_samples: int = 1000,
        min_samples: int = 10,
    ) -> None:
        self._samples: list[AttentionSample] = []
        self._weights: dict[str, float] = {}
        self._learning_rate: float = learning_rate
        self._max_samples: int = max_samples
        self._min_samples: int = min_samples

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def sample_count(self) -> int:
        """Number of samples currently stored."""
        return len(self._samples)

    @property
    def is_fitted(self) -> bool:
        """True when at least one fitting pass has produced weights."""
        return len(self._weights) > 0

    # ── Sample management ──────────────────────────────────────────────

    def add_sample(self, sample: AttentionSample) -> None:
        """Append a training sample, evicting the oldest if at capacity."""
        if len(self._samples) >= self._max_samples:
            self._samples.pop(0)
        self._samples.append(sample)
        log.debug(
            "attention_regressor.sample_added",
            node_id=str(sample.node_id),
            sample_count=len(self._samples),
        )

    # ── Fitting ────────────────────────────────────────────────────────

    def fit(self) -> RegressionResult | None:
        """Run one pass of gradient-descent linear regression.

        Returns ``None`` when there are fewer than *min_samples*.
        """
        if len(self._samples) < self._min_samples:
            log.debug(
                "attention_regressor.fit_skipped",
                reason="insufficient_samples",
                have=len(self._samples),
                need=self._min_samples,
            )
            return None

        # Collect all feature names across samples.
        all_features: set[str] = set()
        for s in self._samples:
            all_features.update(s.features.keys())

        # Initialise unseen features to 0.0.
        for feat in all_features:
            if feat not in self._weights:
                self._weights[feat] = 0.0

        # Compute gradients.
        gradients: dict[str, float] = {f: 0.0 for f in all_features}
        residuals_sq: float = 0.0
        actuals: list[float] = []

        for s in self._samples:
            predicted = self.predict(s.features)
            error = predicted - s.outcome_score
            residuals_sq += error * error
            actuals.append(s.outcome_score)
            for feat in all_features:
                feat_val = s.features.get(feat, 0.0)
                gradients[feat] += error * feat_val

        n = len(self._samples)
        # Average gradients.
        for feat in gradients:
            gradients[feat] /= n

        # Update weights.
        for feat in all_features:
            self._weights[feat] -= self._learning_rate * gradients[feat]

        # R-squared.
        mean_actual = sum(actuals) / n
        ss_tot = sum((y - mean_actual) ** 2 for y in actuals)
        r_squared = 1.0 - (residuals_sq / ss_tot) if ss_tot > 0.0 else 0.0

        converged = all(abs(g) < 0.001 for g in gradients.values())

        log.info(
            "attention_regressor.fit",
            samples=n,
            r_squared=round(r_squared, 4),
            converged=converged,
        )

        return RegressionResult(
            updated_weights=dict(self._weights),
            r_squared=r_squared,
            samples_used=n,
            converged=converged,
        )

    # ── Prediction ─────────────────────────────────────────────────────

    def predict(self, features: dict[str, float]) -> float:
        """Predict an attention weight from a feature vector.

        The result is clamped to [0, 1].
        """
        total = sum(
            self._weights.get(f, 0.0) * v for f, v in features.items()
        )
        return max(0.0, min(1.0, total))

    # ── Accessors / reset ──────────────────────────────────────────────

    def get_weights(self) -> dict[str, float]:
        """Return a copy of the current weight vector."""
        return dict(self._weights)

    def reset(self) -> None:
        """Clear all samples and learned weights."""
        self._samples.clear()
        self._weights.clear()
        log.info("attention_regressor.reset")
