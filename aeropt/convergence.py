from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class BudgetEscalationSettings:
    """Rules for growing a real-solver evaluation budget only when needed."""

    enabled: bool = True
    growth_factor: float = 2.0
    maximum_multiplier: float = 4.0
    convergence_tolerance_percent: float = 3.0
    stable_checkpoints_required: int = 1

    def __post_init__(self) -> None:
        if self.growth_factor <= 1.0:
            raise ValueError("Bütçe büyüme katsayısı 1'den büyük olmalı")
        if self.maximum_multiplier < 1.0:
            raise ValueError("Azami bütçe çarpanı en az 1 olmalı")
        if self.convergence_tolerance_percent < 0.0:
            raise ValueError("Bütçe yakınsama toleransı negatif olamaz")
        if self.stable_checkpoints_required < 1:
            raise ValueError("En az bir kararlı bütçe kontrolü gerekli")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "growth_factor": float(self.growth_factor),
            "maximum_multiplier": float(self.maximum_multiplier),
            "convergence_tolerance_percent": float(
                self.convergence_tolerance_percent
            ),
            "stable_checkpoints_required": int(self.stable_checkpoints_required),
        }


def _finite_best(values: Iterable[float]) -> float:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return min(finite, default=math.inf)


def _finite_ideal(rows: Sequence[Sequence[float]]) -> list[float] | None:
    if not rows:
        return None
    matrix = np.asarray(rows, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] == 0:
        return None
    matrix[~np.isfinite(matrix)] = np.nan
    if np.all(np.isnan(matrix)):
        return None
    with np.errstate(all="ignore"):
        ideal = np.nanmin(matrix, axis=0)
    if np.any(~np.isfinite(ideal)):
        return None
    return [float(value) for value in ideal]


def _relative_improvement_percent(previous: float, current: float) -> float:
    if not math.isfinite(previous) or not math.isfinite(current):
        return math.inf
    return float(max(0.0, 100.0 * (previous - current) / max(abs(previous), 1e-12)))


def _ideal_movement_percent(
    previous: Sequence[float] | None,
    current: Sequence[float] | None,
) -> float:
    if previous is None or current is None or len(previous) != len(current):
        return math.inf
    if not previous:
        return 0.0
    changes = [
        100.0 * abs(float(new) - float(old)) / max(abs(float(old)), 1e-12)
        for old, new in zip(previous, current)
    ]
    return float(max(changes, default=0.0))


class BudgetEscalationController:
    """Stateful milestone controller for 48 -> 96 -> 192 style searches."""

    def __init__(
        self,
        base_budget: int,
        settings: BudgetEscalationSettings,
        *,
        hard_limit: int,
    ) -> None:
        self.settings = settings
        self.base_budget = max(1, int(base_budget))
        requested_maximum = int(math.ceil(self.base_budget * settings.maximum_multiplier))
        self.maximum_budget = min(max(self.base_budget, requested_maximum), int(hard_limit))
        if not settings.enabled:
            self.maximum_budget = self.base_budget
        self.milestones = self._build_milestones()
        self.milestone_index = 0
        self.current_target = self.milestones[0]
        self.checkpoints: list[dict[str, Any]] = []
        self.stable_count = 0
        self.stopped = False
        self.converged: bool | None = None
        self.stopped_reason = "running"

    def _build_milestones(self) -> list[int]:
        milestones = [self.base_budget]
        while milestones[-1] < self.maximum_budget:
            next_value = max(
                milestones[-1] + 1,
                int(math.ceil(milestones[-1] * self.settings.growth_factor)),
            )
            milestones.append(min(next_value, self.maximum_budget))
        return milestones

    @property
    def progress_total(self) -> int:
        return self.maximum_budget if self.settings.enabled else self.base_budget

    def should_evaluate(self, evaluations: int) -> bool:
        return not self.stopped and int(evaluations) < self.current_target

    def mark_converged(self, reason: str) -> None:
        """Record a trusted external convergence signal, such as validated DE early stop."""
        self.stopped = True
        self.converged = True
        self.stopped_reason = str(reason)

    def observe(
        self,
        *,
        scores: Sequence[float],
        objectives: Sequence[Sequence[float]] | None = None,
        frontier_size: int | None = None,
    ) -> dict[str, Any] | None:
        evaluations = len(scores)
        if self.stopped or evaluations < self.current_target:
            return None

        current_scores = scores[: self.current_target]
        current_objectives = (
            list(objectives[: self.current_target]) if objectives is not None else None
        )
        current_best = _finite_best(current_scores)
        current_ideal = _finite_ideal(current_objectives or [])

        if self.checkpoints:
            previous_best = float(self.checkpoints[-1]["best_score"])
            previous_ideal = self.checkpoints[-1].get("ideal_objectives")
            comparison_budget = int(self.checkpoints[-1]["budget"])
        else:
            split = max(1, min(self.current_target - 1, self.current_target // 2))
            previous_best = _finite_best(current_scores[:split])
            previous_ideal = _finite_ideal(
                (current_objectives or [])[:split]
            )
            comparison_budget = split

        score_improvement = _relative_improvement_percent(previous_best, current_best)
        ideal_movement = (
            _ideal_movement_percent(previous_ideal, current_ideal)
            if objectives is not None
            else score_improvement
        )
        controlling_change = max(score_improvement, ideal_movement)
        stable = bool(
            math.isfinite(controlling_change)
            and controlling_change <= self.settings.convergence_tolerance_percent
        )
        self.stable_count = self.stable_count + 1 if stable else 0

        decision = "continue"
        next_budget: int | None = None
        if not self.settings.enabled:
            decision = "fixed_budget_complete"
            self.stopped = True
            self.converged = None
            self.stopped_reason = "fixed_budget_complete"
        elif self.stable_count >= self.settings.stable_checkpoints_required:
            decision = "converged"
            self.stopped = True
            self.converged = True
            self.stopped_reason = "convergence_tolerance_met"
        elif self.current_target >= self.maximum_budget:
            decision = "maximum_budget_reached"
            self.stopped = True
            self.converged = False
            self.stopped_reason = "maximum_budget_reached_before_convergence"
        else:
            self.milestone_index += 1
            self.current_target = self.milestones[self.milestone_index]
            next_budget = self.current_target
            decision = "escalated"

        checkpoint = {
            "budget": int(evaluations),
            "milestone_budget": int(self.milestones[self.milestone_index - (1 if decision == "escalated" else 0)]),
            "compared_with_budget": int(comparison_budget),
            "best_score": float(current_best),
            "score_improvement_percent": float(score_improvement),
            "ideal_objectives": current_ideal,
            "pareto_ideal_movement_percent": float(ideal_movement),
            "controlling_change_percent": float(controlling_change),
            "frontier_size": None if frontier_size is None else int(frontier_size),
            "stable": stable,
            "stable_checkpoints": int(self.stable_count),
            "decision": decision,
            "next_budget": next_budget,
        }
        self.checkpoints.append(checkpoint)
        return checkpoint

    def state(self) -> dict[str, Any]:
        return {
            "milestone_index": int(self.milestone_index),
            "current_target": int(self.current_target),
            "checkpoints": self.checkpoints,
            "stable_count": int(self.stable_count),
            "stopped": bool(self.stopped),
            "converged": self.converged,
            "stopped_reason": self.stopped_reason,
        }

    def restore(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        milestone_index = int(state.get("milestone_index", 0))
        if not 0 <= milestone_index < len(self.milestones):
            return
        target = int(state.get("current_target", self.milestones[milestone_index]))
        if target != self.milestones[milestone_index]:
            return
        self.milestone_index = milestone_index
        self.current_target = target
        self.checkpoints = list(state.get("checkpoints", []))
        self.stable_count = int(state.get("stable_count", 0))
        self.stopped = bool(state.get("stopped", False))
        self.converged = state.get("converged")
        self.stopped_reason = str(state.get("stopped_reason", "running"))

    def report(self, *, evaluations: int) -> dict[str, Any]:
        if self.settings.enabled and not self.stopped:
            status = "running"
        elif self.converged is True:
            status = "converged"
        elif self.converged is False:
            status = "budget_exhausted"
        else:
            status = "fixed_budget"
        return {
            **self.settings.to_dict(),
            "status": status,
            "base_budget": int(self.base_budget),
            "maximum_budget": int(self.maximum_budget),
            "milestones": [int(value) for value in self.milestones],
            "evaluations_completed": int(evaluations),
            "escalations_performed": sum(
                checkpoint.get("decision") == "escalated"
                for checkpoint in self.checkpoints
            ),
            "converged": self.converged,
            "stopped_reason": self.stopped_reason,
            "checkpoints": _json_safe(self.checkpoints),
            "recommendation": (
                "Bütçe yeterli; izlenen amaçlar tolerans içinde kararlı."
                if self.converged is True
                else (
                    "Azami bütçede hâlâ hareket var; daha yüksek bütçe veya çoklu seed önerilir."
                    if self.converged is False
                    else "Sabit kullanıcı bütçesi tamamlandı; otomatik yeterlilik kararı verilmedi."
                )
            ),
        }
