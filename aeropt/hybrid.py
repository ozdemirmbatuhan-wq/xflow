from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from math import inf
from typing import Any, Callable

import numpy as np

from .airfoil import (
    cst_geometry_is_valid,
    fit_naca_to_cst,
    foil_name,
    make_cst_airfoil,
    polar_point,
)
from .models import AirfoilDesign, AirfoilLike, CSTAirfoilDesign
from .xfoil import point_at_alpha, point_at_cl, run_xfoil_polar


PolarEvaluator = Callable[[AirfoilLike], dict[str, Any]]


@dataclass(frozen=True)
class CandidateResult:
    foil: CSTAirfoilDesign
    score: float
    point: dict[str, float] | None
    polar: dict[str, Any] | None
    error: str | None = None


def compare_internal_to_xfoil(
    *,
    internal_point: dict[str, float],
    xfoil_points: list[dict[str, float]],
    target_cl: float,
    cl_tolerance_percent: float,
    cd_tolerance_percent: float,
) -> dict[str, Any]:
    """Compare lift at equal alpha and drag at equal CL; these are different checks by design."""
    same_alpha = point_at_alpha(xfoil_points, float(internal_point["alpha_deg"]))
    target_point = point_at_cl(xfoil_points, target_cl)
    cl_error = inf
    cd_error = inf
    if same_alpha is not None:
        cl_error = 100.0 * abs(same_alpha["cl"] - internal_point["cl"]) / max(
            abs(internal_point["cl"]), 0.10
        )
    if target_point is not None:
        cd_error = 100.0 * abs(target_point["cd"] - internal_point["cd"]) / max(
            abs(target_point["cd"]), 1e-5
        )
    accepted = bool(
        same_alpha is not None
        and target_point is not None
        and cl_error <= cl_tolerance_percent
        and cd_error <= cd_tolerance_percent
    )
    return {
        "accepted": accepted,
        "cl_error_percent": None if not np.isfinite(cl_error) else float(cl_error),
        "cd_error_percent": None if not np.isfinite(cd_error) else float(cd_error),
        "cl_tolerance_percent": float(cl_tolerance_percent),
        "cd_tolerance_percent": float(cd_tolerance_percent),
        "xfoil_at_internal_alpha": same_alpha,
        "xfoil_at_target_cl": target_point,
        "target_cl_bracketed": target_point is not None,
    }


def _candidate_from_vector(vector: np.ndarray, index: int) -> CSTAirfoilDesign:
    half = len(vector) // 2
    foil = make_cst_airfoil(
        vector[:half],
        vector[half:],
        name=f"AeroOpt-CST3-c{index:04d}",
    )
    return CSTAirfoilDesign(
        foil.upper_weights,
        foil.lower_weights,
        foil.max_camber,
        foil.camber_position,
        foil.thickness,
        foil_name(foil),
        foil.trailing_edge_gap,
    )


def optimize_cst_with_xfoil(
    *,
    initial_foil: AirfoilDesign,
    target_cl: float,
    alpha_bounds: tuple[float, float],
    camber_bounds: tuple[float, float],
    camber_position_bounds: tuple[float, float],
    thickness_bounds: tuple[float, float],
    candidate_budget: int,
    workers: int,
    seed: int,
    evaluator: PolarEvaluator,
) -> tuple[CSTAirfoilDesign, dict[str, Any], dict[str, Any]]:
    """Parallel low-order CST evolution using actual XFOIL CD at the requested CL."""
    budget = max(1, int(candidate_budget))
    detected = os.cpu_count() or 1
    worker_count = max(1, min(int(workers), detected, budget))
    rng = np.random.default_rng(seed + 911)
    base = fit_naca_to_cst(initial_foil, order=3)
    base_vector = np.asarray((*base.upper_weights, *base.lower_weights), dtype=float)
    scored: list[CandidateResult] = []
    attempted = 0

    def evaluate_one(item: tuple[int, np.ndarray]) -> CandidateResult:
        index, vector = item
        foil = _candidate_from_vector(vector, index)
        if not cst_geometry_is_valid(
            foil,
            camber_bounds=camber_bounds,
            camber_position_bounds=camber_position_bounds,
            thickness_bounds=thickness_bounds,
        ):
            return CandidateResult(foil, inf, None, None, "geometri kısıtı")
        try:
            polar = evaluator(foil)
            point = point_at_cl(polar.get("points", []), target_cl)
            if point is None:
                return CandidateResult(foil, inf, None, polar, "hedef CL yakınsamadı")
            alpha = float(point["alpha_deg"])
            alpha_violation = max(alpha_bounds[0] - alpha, 0.0, alpha - alpha_bounds[1])
            positive_cls = [float(row["cl"]) for row in polar.get("points", []) if row["cl"] > 0]
            cl_peak = max(positive_cls, default=target_cl)
            margin_violation = max(0.0, target_cl / max(0.92 * cl_peak, 1e-6) - 1.0)
            # At fixed CL, minimizing CD is exactly maximizing L/D. Soft constraints
            # keep the search away from alpha limits and the edge of the polar.
            score = float(point["cd"] + 0.006 * alpha_violation**2 + 0.08 * margin_violation**2)
            return CandidateResult(foil, score, point, polar)
        except Exception as exc:  # XFOIL non-convergence is a candidate failure, not a run failure.
            return CandidateResult(foil, inf, None, None, str(exc)[-240:])

    next_vectors: list[np.ndarray] = [base_vector]
    candidate_index = 0
    while attempted < budget:
        batch_size = min(worker_count, budget - attempted)
        batch: list[tuple[int, np.ndarray]] = []
        generation_fraction = attempted / max(budget - 1, 1)
        sigma = 0.055 * (1.0 - generation_fraction) + 0.012 * generation_fraction
        elite = sorted((item for item in scored if np.isfinite(item.score)), key=lambda item: item.score)[:8]

        while len(batch) < batch_size:
            if next_vectors:
                vector = next_vectors.pop(0)
            elif elite and rng.random() < 0.82:
                parent = elite[int(rng.integers(0, len(elite)))]
                vector = np.asarray((*parent.foil.upper_weights, *parent.foil.lower_weights))
                vector = vector + rng.normal(0.0, sigma, size=vector.size)
            else:
                vector = base_vector + rng.normal(0.0, 1.7 * sigma, size=base_vector.size)
            vector = np.clip(vector, -0.45, 0.45)
            batch.append((candidate_index, vector))
            candidate_index += 1
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="aeropt-xfoil") as pool:
            scored.extend(pool.map(evaluate_one, batch))
        attempted += len(batch)

    valid = [candidate for candidate in scored if np.isfinite(candidate.score) and candidate.polar]
    if not valid:
        examples = [item.error for item in scored if item.error][:3]
        detail = "; ".join(examples) if examples else "yakınsayan aday yok"
        raise RuntimeError(f"CST/XFOIL optimizasyonunda geçerli profil bulunamadı: {detail}")
    best = min(valid, key=lambda candidate: candidate.score)
    ranked = sorted(scored, key=lambda candidate: candidate.score)
    history = [
        {
            "rank": rank + 1,
            "name": item.foil.name,
            "score": None if not np.isfinite(item.score) else float(item.score),
            "cd": None if item.point is None else float(item.point["cd"]),
            "alpha_deg": None if item.point is None else float(item.point["alpha_deg"]),
            "ld": None if item.point is None else float(item.point["ld"]),
            "thickness": float(item.foil.thickness),
            "max_camber": float(item.foil.max_camber),
            "error": item.error,
        }
        for rank, item in enumerate(ranked[: min(20, len(ranked))])
    ]
    metadata = {
        "candidate_budget": budget,
        "candidates_evaluated": attempted,
        "valid_candidates": len(valid),
        "parallel_workers_requested": int(workers),
        "parallel_workers_used": worker_count,
        "detected_logical_cores": detected,
        "best_score": float(best.score),
        "best_point": best.point,
        "top_candidates": history,
    }
    return best.foil, best.polar or {}, metadata


def run_closed_loop_airfoil(
    *,
    executable: str,
    initial_foil: AirfoilDesign,
    internal_point: dict[str, float],
    reynolds: float,
    mach: float,
    target_cl: float,
    alpha_range: tuple[float, float],
    alpha_bounds: tuple[float, float],
    camber_bounds: tuple[float, float],
    camber_position_bounds: tuple[float, float],
    thickness_bounds: tuple[float, float],
    strategy: str,
    cl_tolerance_percent: float,
    cd_tolerance_percent: float,
    candidate_budget: int,
    workers: int,
    seed: int,
    timeout_seconds: float,
) -> tuple[AirfoilLike, dict[str, Any], dict[str, Any]]:
    initial_polar = run_xfoil_polar(
        executable,
        initial_foil,
        reynolds,
        mach,
        alpha_range[0],
        alpha_range[1],
        alpha_step=0.5,
        timeout_seconds=timeout_seconds,
    )
    initial_check = compare_internal_to_xfoil(
        internal_point=internal_point,
        xfoil_points=initial_polar["points"],
        target_cl=target_cl,
        cl_tolerance_percent=cl_tolerance_percent,
        cd_tolerance_percent=cd_tolerance_percent,
    )
    should_escalate = strategy == "xfoil_cst_always" or not initial_check["accepted"]
    cst_metadata: dict[str, Any] | None = None
    final_foil: AirfoilLike = initial_foil
    final_polar = initial_polar
    if should_escalate:
        def evaluator(candidate: AirfoilLike) -> dict[str, Any]:
            return run_xfoil_polar(
                executable,
                candidate,
                reynolds,
                mach,
                alpha_range[0],
                alpha_range[1],
                alpha_step=1.0,
                timeout_seconds=timeout_seconds,
            )

        final_foil, _, cst_metadata = optimize_cst_with_xfoil(
            initial_foil=initial_foil,
            target_cl=target_cl,
            alpha_bounds=alpha_bounds,
            camber_bounds=camber_bounds,
            camber_position_bounds=camber_position_bounds,
            thickness_bounds=thickness_bounds,
            candidate_budget=candidate_budget,
            workers=workers,
            seed=seed,
            evaluator=evaluator,
        )
        # A finer final polar is the source of the displayed cruise CL/CD and wing section data.
        final_polar = run_xfoil_polar(
            executable,
            final_foil,
            reynolds,
            mach,
            alpha_range[0],
            alpha_range[1],
            alpha_step=0.5,
            timeout_seconds=timeout_seconds,
        )

    final_point = point_at_cl(final_polar["points"], target_cl)
    if final_point is None:
        raise RuntimeError("XFOIL son profilde hedef CL değerini yakınsatamadı")
    metadata = {
        "strategy": strategy,
        "initial_check": initial_check,
        "escalated_to_cst": should_escalate,
        "acceptance_rule": "NACA için aynı alfa CL ve aynı CL CD toleransı; CST için doğrudan XFOIL amaç fonksiyonu",
        "final_family": final_foil.family,
        "final_xfoil_point": final_point,
        "final_xfoil_converged_points": final_polar["converged_points"],
        "cst_optimization": cst_metadata,
    }
    return final_foil, final_polar, metadata


def build_xfoil_polar_mesh(
    *,
    executable: str,
    foil: AirfoilLike,
    reynolds_values: list[float],
    mach: float,
    alpha_range: tuple[float, float],
    timeout_seconds: float,
    workers: int,
    reference_polar: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the small Re-dependent XFOIL section-polar mesh consumed by the LLT solver."""
    unique = sorted({max(20_000.0, float(value)) for value in reynolds_values})
    mesh: list[dict[str, Any]] = []
    pending: list[float] = []
    reference_re = float(reference_polar.get("reynolds", -1.0)) if reference_polar else -1.0
    for reynolds in unique:
        if reference_polar and abs(reynolds - reference_re) / max(reference_re, 1.0) < 0.01:
            mesh.append({"reynolds": reynolds, "points": reference_polar["points"]})
        else:
            pending.append(reynolds)

    def solve(reynolds: float) -> dict[str, Any]:
        polar = run_xfoil_polar(
            executable,
            foil,
            reynolds,
            mach,
            alpha_range[0],
            alpha_range[1],
            alpha_step=0.5,
            timeout_seconds=timeout_seconds,
        )
        return {"reynolds": reynolds, "points": polar["points"]}

    if pending:
        max_workers = max(1, min(int(workers), len(pending), os.cpu_count() or 1))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aeropt-polar") as pool:
            mesh.extend(pool.map(solve, pending))
    return sorted(mesh, key=lambda polar: polar["reynolds"])


def internal_point_for_final_foil(
    foil: AirfoilLike, alpha_deg: float, reynolds: float, mach: float
) -> dict[str, float]:
    """Diagnostic only: lets the UI show why an XFOIL-driven CST design differs."""
    return polar_point(foil, alpha_deg, reynolds, mach).to_dict()
