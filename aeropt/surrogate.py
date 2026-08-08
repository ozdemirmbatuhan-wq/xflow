from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np
from scipy.interpolate import RBFInterpolator


@dataclass(frozen=True)
class SurrogateSettings:
    enabled: bool = True
    proposals_per_real_evaluation: int = 6
    minimum_real_fraction: float = 0.65
    maximum_validation_error_percent: float = 8.0
    early_stop_improvement_percent: float = 0.25

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RBFSurrogateAdvisor:
    """RBF ranker which only pre-screens; returned objective values remain solver values."""

    def __init__(self, bounds: np.ndarray, settings: SurrogateSettings):
        self.bounds = np.asarray(bounds, dtype=float)
        self.settings = settings
        self.vectors: list[list[float]] = []
        self.scores: list[float] = []
        self._sample_keys: set[tuple[float, ...]] = set()
        self.proposals_screened = 0
        self.models_fitted = 0
        self.validation_error_percent: float | None = None
        self.early_stopped = False

    @property
    def dimension(self) -> int:
        return int(self.bounds.shape[0])

    @property
    def ready(self) -> bool:
        return self.settings.enabled and len(self.scores) >= max(8, self.dimension + 2)

    def record(self, vector: Iterable[float], score: float) -> None:
        values = np.asarray(list(vector), dtype=float)
        if values.shape != (self.dimension,) or not np.all(np.isfinite(values)):
            return
        if not np.isfinite(score):
            return
        key = tuple(float(value) for value in np.round(values, 11))
        if key in self._sample_keys:
            return
        self._sample_keys.add(key)
        self.vectors.append([float(value) for value in values])
        self.scores.append(float(score))

    def restore(self, vectors: list[list[float]], scores: list[float]) -> None:
        for vector, score in zip(vectors, scores):
            self.record(vector, score)

    def _normalized_unique_samples(self) -> tuple[np.ndarray, np.ndarray]:
        x = np.asarray(self.vectors, dtype=float)
        y = np.asarray(self.scores, dtype=float)
        scale = np.maximum(self.bounds[:, 1] - self.bounds[:, 0], 1e-12)
        x = (x - self.bounds[:, 0]) / scale
        rounded = np.round(x, 11)
        _, unique_indices = np.unique(rounded, axis=0, return_index=True)
        unique_indices.sort()
        return x[unique_indices], y[unique_indices]

    @staticmethod
    def _fit(x: np.ndarray, y: np.ndarray) -> tuple[RBFInterpolator, float, float]:
        center = float(np.median(y))
        spread = float(max(np.std(y), abs(center) * 0.02, 1e-9))
        normalized_y = (y - center) / spread
        model = RBFInterpolator(
            x,
            normalized_y,
            kernel="thin_plate_spline",
            smoothing=0.015,
            neighbors=min(32, len(x)),
        )
        return model, center, spread

    def _model(self) -> tuple[RBFInterpolator, float, float] | None:
        if not self.ready:
            return None
        x, y = self._normalized_unique_samples()
        if len(y) < max(8, self.dimension + 2):
            return None
        try:
            model = self._fit(x, y)
        except (ValueError, np.linalg.LinAlgError):
            return None
        self.models_fitted += 1
        if len(y) >= max(12, self.dimension + 5):
            holdout_count = max(2, min(5, len(y) // 5))
            holdout_indices = np.linspace(0, len(y) - 1, holdout_count, dtype=int)
            train_mask = np.ones(len(y), dtype=bool)
            train_mask[holdout_indices] = False
            try:
                check_model, center, spread = self._fit(x[train_mask], y[train_mask])
                prediction = check_model(x[holdout_indices]).reshape(-1) * spread + center
                scale = max(float(np.mean(np.abs(y[holdout_indices]))), 1e-9)
                self.validation_error_percent = float(
                    100.0 * np.mean(np.abs(prediction - y[holdout_indices])) / scale
                )
            except (ValueError, np.linalg.LinAlgError):
                self.validation_error_percent = None
        return model

    def choose(self, proposals: list[np.ndarray]) -> int:
        if len(proposals) <= 1 or not self.settings.enabled:
            return 0
        fitted = self._model()
        if fitted is None:
            return 0
        model, center, spread = fitted
        values = np.asarray(proposals, dtype=float)
        scale = np.maximum(self.bounds[:, 1] - self.bounds[:, 0], 1e-12)
        normalized = (values - self.bounds[:, 0]) / scale
        try:
            prediction = model(normalized).reshape(-1) * spread + center
        except ValueError:
            return 0
        self.proposals_screened += len(proposals) - 1
        return int(np.argmin(prediction))

    def may_stop_early(
        self,
        *,
        evaluations: int,
        budget: int,
        previous_best: float,
        current_best: float,
    ) -> bool:
        if (
            not self.ready
            or evaluations >= budget
            or evaluations < int(np.ceil(budget * self.settings.minimum_real_fraction))
        ):
            return False
        if self.validation_error_percent is None:
            self._model()
        if (
            self.validation_error_percent is None
            or self.validation_error_percent > self.settings.maximum_validation_error_percent
        ):
            return False
        improvement = 100.0 * max(previous_best - current_best, 0.0) / max(abs(previous_best), 1e-12)
        self.early_stopped = improvement <= self.settings.early_stop_improvement_percent
        return self.early_stopped

    def state(self) -> dict[str, Any]:
        return {"vectors": self.vectors, "scores": self.scores}

    def report(self, *, real_evaluations: int, budget: int) -> dict[str, Any]:
        return {
            "enabled": self.settings.enabled,
            "trained": self.models_fitted > 0,
            "training_samples": len(self.scores),
            "proposals_per_real_evaluation": self.settings.proposals_per_real_evaluation,
            "proposals_screened": self.proposals_screened,
            "real_solver_evaluations": int(real_evaluations),
            "maximum_solver_budget": int(budget),
            "solver_evaluations_saved": max(int(budget) - int(real_evaluations), 0),
            "validation_error_percent": self.validation_error_percent,
            "early_stopped": self.early_stopped,
            "finalists_always_solver_verified": True,
        }
