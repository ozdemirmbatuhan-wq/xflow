from __future__ import annotations

from dataclasses import dataclass
from math import cos, pi, radians
from typing import Any

import numpy as np

from .airfoil import cl_max, profile_cd, thin_airfoil_properties
from .models import AirfoilLike, Fluid, WingGeometry
from .xfoil import polar_mesh_cd, polar_mesh_cl_limit, polar_mesh_lift_properties


@dataclass
class WingResult:
    geometry: WingGeometry
    cl: float
    cd_profile: float
    cd_induced: float
    cd_total: float
    lift_n: float
    drag_n: float
    ld: float
    span_efficiency: float
    root_bending_moment_nm: float
    stall_ratio: float
    reynolds_root: float
    reynolds_tip: float
    section_polar_source: str
    y: np.ndarray
    chord: np.ndarray
    local_cl: np.ndarray
    local_lift_n_per_m: np.ndarray

    def to_dict(self, include_distribution: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "geometry": self.geometry.to_dict(),
            "cl": float(self.cl),
            "cd_profile": float(self.cd_profile),
            "cd_induced": float(self.cd_induced),
            "cd_total": float(self.cd_total),
            "lift_n": float(self.lift_n),
            "drag_n": float(self.drag_n),
            "ld": float(self.ld),
            "span_efficiency": float(self.span_efficiency),
            "root_bending_moment_nm": float(self.root_bending_moment_nm),
            "stall_ratio": float(self.stall_ratio),
            "reynolds_root": float(self.reynolds_root),
            "reynolds_tip": float(self.reynolds_tip),
            "section_polar_source": self.section_polar_source,
        }
        if include_distribution:
            result["distribution"] = [
                {
                    "y_m": float(y),
                    "chord_m": float(chord),
                    "local_cl": float(local_cl),
                    "lift_n_per_m": float(lift),
                }
                for y, chord, local_cl, lift in zip(
                    self.y, self.chord, self.local_cl, self.local_lift_n_per_m
                )
            ]
        return result


def _solve_fourier_coefficients(
    foil: AirfoilLike,
    geometry: WingGeometry,
    mach: float,
    modes: int,
    polar_mesh: list[dict[str, Any]] | None = None,
    reference_reynolds: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    n = np.arange(1, 2 * modes, 2, dtype=float)
    theta = np.linspace(pi / (2.0 * modes), pi / 2.0, modes)
    y_fraction = np.cos(theta)  # 1 at tip, 0 at root
    chord = np.asarray([geometry.chord_at(value) for value in y_fraction], dtype=float)
    twist = np.radians(
        np.asarray([geometry.twist_at(value) for value in y_fraction], dtype=float)
    )
    if polar_mesh:
        a0, alpha0 = polar_mesh_lift_properties(
            polar_mesh, reference_reynolds or polar_mesh[len(polar_mesh) // 2]["reynolds"]
        )
    else:
        a0, alpha0, _ = thin_airfoil_properties(foil, mach)
    a0 *= max(cos(radians(geometry.sweep_deg)), 0.35)
    mu = a0 * chord / (4.0 * geometry.span)
    matrix = np.sin(np.outer(theta, n)) * (np.sin(theta)[:, None] + mu[:, None] * n[None, :])
    rhs = mu * (radians(geometry.alpha_deg) + twist - alpha0) * np.sin(theta)
    coefficients = np.linalg.solve(matrix, rhs)
    return n, coefficients


def evaluate_wing(
    foil: AirfoilLike,
    geometry: WingGeometry,
    fluid: Fluid,
    speed: float,
    modes: int = 10,
    distribution_points: int = 81,
    polar_mesh: list[dict[str, Any]] | None = None,
) -> WingResult:
    if geometry.span <= 0.0 or geometry.root_chord <= 0.0 or geometry.taper <= 0.0:
        raise ValueError("Kanat boyutları pozitif olmalı")
    mach = fluid.mach(speed)
    reference_re = fluid.reynolds(speed, geometry.mean_aerodynamic_chord)
    n, coefficients = _solve_fourier_coefficients(
        foil, geometry, mach, modes, polar_mesh=polar_mesh, reference_reynolds=reference_re
    )
    ar = geometry.aspect_ratio
    cl = pi * ar * coefficients[0]
    sweep_cos = max(cos(radians(geometry.sweep_deg)), 0.35)
    # Classical LLT is unswept. This effective-AR correction prevents the
    # low-order model from assigning an artificial induced-drag benefit to sweep.
    cd_induced = pi * ar * float(np.sum(n * coefficients**2)) / sweep_cos**2
    efficiency = cl**2 / (pi * ar * cd_induced) if cd_induced > 1e-12 else 1.0

    # Root -> tip distribution. The exact tip circulation is zero.
    y = np.linspace(0.0, 0.5 * geometry.span, distribution_points)
    theta = np.arccos(np.clip(2.0 * y / geometry.span, 0.0, 1.0))
    chord = np.asarray(
        [geometry.chord_at(2.0 * value / geometry.span) for value in y], dtype=float
    )
    circulation_sum = np.sum(
        coefficients[:, None] * np.sin(n[:, None] * theta[None, :]), axis=0
    )
    local_cl = 4.0 * geometry.span * circulation_sum / chord
    local_re = fluid.density * speed * chord / fluid.dynamic_viscosity
    if polar_mesh:
        local_cd = np.array(
            [polar_mesh_cd(polar_mesh, float(cli), float(rei)) for cli, rei in zip(local_cl, local_re)],
            dtype=float,
        )
    else:
        local_cd = np.array(
            [profile_cd(foil, cli, rei, mach) for cli, rei in zip(local_cl, local_re)],
            dtype=float,
        )
    # Small low-order sweep penalty for cross-flow/profile effects.
    local_cd += 0.0015 * (1.0 / sweep_cos - 1.0)
    cd_profile = 2.0 * float(np.trapezoid(local_cd * chord, y)) / geometry.area
    cd_total = cd_profile + cd_induced
    q = fluid.dynamic_pressure(speed)
    lift = q * geometry.area * cl
    drag = q * geometry.area * cd_total
    lift_per_span = q * chord * local_cl
    bending = float(np.trapezoid(lift_per_span * y, y))
    if polar_mesh:
        limits = np.array(
            [
                polar_mesh_cl_limit(polar_mesh, float(rei), positive=bool(cli >= 0.0))
                for cli, rei in zip(local_cl, local_re)
            ]
        )
    else:
        limits = np.array([cl_max(foil, rei) for rei in local_re])
    # Ignore the last point only if numerical noise creates a meaningless 0/0-like tip value.
    stall_ratio = float(np.max(np.abs(local_cl[:-1]) / np.maximum(limits[:-1], 1e-9)))
    return WingResult(
        geometry=geometry,
        cl=float(cl),
        cd_profile=float(cd_profile),
        cd_induced=float(cd_induced),
        cd_total=float(cd_total),
        lift_n=float(lift),
        drag_n=float(drag),
        ld=float(lift / drag) if drag > 0.0 else float("inf"),
        span_efficiency=float(np.clip(efficiency, 0.0, 1.2)),
        root_bending_moment_nm=bending,
        stall_ratio=stall_ratio,
        reynolds_root=float(local_re[0]),
        reynolds_tip=float(local_re[-1]),
        section_polar_source="XFOIL polar mesh" if polar_mesh else "AeroOpt empirical correlation",
        y=y,
        chord=chord,
        local_cl=local_cl,
        local_lift_n_per_m=lift_per_span,
    )


def geometry_for_target_lift(
    foil: AirfoilLike,
    geometry_without_alpha: WingGeometry,
    fluid: Fluid,
    speed: float,
    target_lift: float,
    alpha_min_deg: float,
    alpha_max_deg: float,
    modes: int = 10,
    distribution_points: int = 81,
    polar_mesh: list[dict[str, Any]] | None = None,
) -> WingResult:
    """Use LLT linearity to select incidence, then perform one full evaluation."""
    g0 = WingGeometry(
        geometry_without_alpha.span,
        geometry_without_alpha.root_chord,
        geometry_without_alpha.taper,
        geometry_without_alpha.sweep_deg,
        geometry_without_alpha.tip_twist_deg,
        0.0,
    )
    g1 = WingGeometry(
        g0.span, g0.root_chord, g0.taper, g0.sweep_deg, g0.tip_twist_deg, 1.0
    )
    r0 = evaluate_wing(
        foil, g0, fluid, speed, modes=modes, distribution_points=21, polar_mesh=polar_mesh
    )
    r1 = evaluate_wing(
        foil, g1, fluid, speed, modes=modes, distribution_points=21, polar_mesh=polar_mesh
    )
    lift_per_degree = r1.lift_n - r0.lift_n
    if abs(lift_per_degree) < 1e-9:
        alpha = alpha_max_deg
    else:
        alpha = (target_lift - r0.lift_n) / lift_per_degree
    alpha = float(np.clip(alpha, alpha_min_deg, alpha_max_deg))
    geometry = WingGeometry(
        g0.span, g0.root_chord, g0.taper, g0.sweep_deg, g0.tip_twist_deg, alpha
    )
    return evaluate_wing(
        foil,
        geometry,
        fluid,
        speed,
        modes=modes,
        distribution_points=distribution_points,
        polar_mesh=polar_mesh,
    )
