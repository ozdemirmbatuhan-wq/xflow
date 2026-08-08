from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .models import Fluid, WingGeometry


@dataclass(frozen=True)
class StructuralSettings:
    """Inputs for a preliminary half-wing beam and torsion-box screening model."""

    enabled: bool = False
    youngs_modulus_pa: float = 70.0e9
    material_density_kg_m3: float = 1600.0
    allowable_stress_pa: float = 300.0e6
    safety_factor: float = 1.5
    spar_height_fraction_of_foil: float = 0.75
    spar_cap_width_fraction_chord: float = 0.08
    spar_cap_thickness_m: float = 0.002
    skin_thickness_m: float = 0.001
    torsion_box_width_fraction_chord: float = 0.45
    poisson_ratio: float = 0.30
    max_tip_deflection_fraction_semispan: float = 0.08
    max_elastic_twist_deg: float = 2.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cumulative_trapezoid(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    result = np.zeros_like(y, dtype=float)
    if y.size > 1:
        result[1:] = np.cumsum(0.5 * (y[1:] + y[:-1]) * np.diff(x))
    return result


def _positive_semispan_distribution(
    distribution: list[dict[str, Any]], semispan: float
) -> tuple[np.ndarray, np.ndarray]:
    stations: list[tuple[float, float]] = []
    for item in distribution:
        try:
            y = float(item["y_m"])
            load = float(item["lift_n_per_m"])
        except (KeyError, TypeError, ValueError):
            continue
        if np.isfinite(y) and np.isfinite(load) and y >= -1e-9:
            stations.append((max(y, 0.0), load))
    stations.sort(key=lambda row: row[0])
    if len(stations) < 3:
        return np.asarray([], dtype=float), np.asarray([], dtype=float)
    y = np.asarray([row[0] for row in stations], dtype=float)
    load = np.asarray([row[1] for row in stations], dtype=float)
    unique_y, inverse = np.unique(y, return_inverse=True)
    averaged_load = np.zeros_like(unique_y)
    counts = np.zeros_like(unique_y)
    for index, target in enumerate(inverse):
        averaged_load[target] += load[index]
        counts[target] += 1.0
    load = averaged_load / np.maximum(counts, 1.0)
    y = unique_y
    if y[0] > 1e-8:
        y = np.insert(y, 0, 0.0)
        load = np.insert(load, 0, load[0])
    if y[-1] < semispan - 1e-8:
        y = np.append(y, semispan)
        load = np.append(load, 0.0)
    mask = y <= semispan * (1.0 + 1e-6)
    return y[mask], load[mask]


def _internal_resultant(y: np.ndarray, distributed: np.ndarray, *, moment: bool) -> np.ndarray:
    result = np.zeros_like(y)
    for index, station in enumerate(y[:-1]):
        arm = y[index:] - station if moment else np.ones_like(y[index:])
        result[index] = float(np.trapezoid(distributed[index:] * arm, y[index:]))
    return result


def _section_properties(
    geometry: WingGeometry,
    y: np.ndarray,
    foil_thickness_ratio: float,
    settings: StructuralSettings,
) -> dict[str, np.ndarray]:
    eta = np.clip(y / max(0.5 * geometry.span, 1e-12), 0.0, 1.0)
    chord = np.asarray([geometry.chord_at(value) for value in eta], dtype=float)
    height = np.maximum(
        foil_thickness_ratio * chord * settings.spar_height_fraction_of_foil,
        4.0 * settings.skin_thickness_m,
    )
    cap_width = settings.spar_cap_width_fraction_chord * chord
    cap_area = cap_width * settings.spar_cap_thickness_m
    skin_width = settings.torsion_box_width_fraction_chord * chord
    skin_area_each = skin_width * settings.skin_thickness_m
    inertia = (
        2.0
        * (
            cap_area * (0.5 * height) ** 2
            + cap_width * settings.spar_cap_thickness_m**3 / 12.0
        )
        + 2.0 * skin_area_each * (0.5 * height) ** 2
    )
    enclosed_area = skin_width * height
    perimeter_over_t = 2.0 * skin_width / settings.skin_thickness_m + 2.0 * height / max(
        settings.spar_cap_thickness_m, 1e-9
    )
    torsion_constant = np.maximum(4.0 * enclosed_area**2 / perimeter_over_t, 1e-12)
    material_area = (
        2.0 * cap_area
        + 2.0 * skin_area_each
        + 2.0 * height * settings.skin_thickness_m
    )
    return {
        "chord": chord,
        "height": height,
        "inertia": np.maximum(inertia, 1e-14),
        "torsion_constant": torsion_constant,
        "material_area": material_area,
    }


def analyze_structure(
    *,
    geometry: WingGeometry,
    foil_thickness_ratio: float,
    fluid: Fluid,
    conditions: list[dict[str, Any]],
    settings: StructuralSettings,
) -> dict[str, Any]:
    """Screen stress, static deflection, torsional twist and idealized material mass."""
    if not settings.enabled:
        return {
            "enabled": False,
            "performed": False,
            "passed": True,
            "penalty": 0.0,
            "model": "disabled",
        }

    semispan = 0.5 * geometry.span
    cases: list[dict[str, Any]] = []
    for condition in conditions:
        point = condition.get("point", {})
        y, load = _positive_semispan_distribution(
            list(point.get("distribution", [])), semispan
        )
        if y.size < 3:
            continue
        properties = _section_properties(
            geometry, y, foil_thickness_ratio, settings
        )
        bending_moment = _internal_resultant(y, load, moment=True)
        section_modulus = properties["inertia"] / np.maximum(
            0.5 * properties["height"], 1e-12
        )
        stress = np.abs(bending_moment) / section_modulus
        curvature = bending_moment / (
            settings.youngs_modulus_pa * properties["inertia"]
        )
        slope = _cumulative_trapezoid(curvature, y)
        deflection = _cumulative_trapezoid(slope, y)

        speed = float(condition["speed_m_s"])
        q = fluid.dynamic_pressure(speed)
        cm = abs(float(point.get("cm", 0.04)))
        torque_per_length = cm * q * properties["chord"] ** 2
        internal_torque = _internal_resultant(y, torque_per_length, moment=False)
        shear_modulus = settings.youngs_modulus_pa / (
            2.0 * (1.0 + settings.poisson_ratio)
        )
        twist_rate = internal_torque / (
            shear_modulus * properties["torsion_constant"]
        )
        twist_rad = _cumulative_trapezoid(twist_rate, y)
        half_mass = float(
            np.trapezoid(
                settings.material_density_kg_m3 * properties["material_area"], y
            )
        )
        stress_utilization = float(
            settings.safety_factor
            * np.max(stress)
            / max(settings.allowable_stress_pa, 1e-12)
        )
        deflection_fraction = float(abs(deflection[-1]) / max(semispan, 1e-12))
        twist_deg = float(abs(np.degrees(twist_rad[-1])))
        cases.append(
            {
                "speed_m_s": speed,
                "root_bending_moment_nm_integrated": float(abs(bending_moment[0])),
                "max_stress_pa": float(np.max(stress)),
                "stress_utilization": stress_utilization,
                "tip_deflection_m": float(abs(deflection[-1])),
                "tip_deflection_fraction_semispan": deflection_fraction,
                "elastic_twist_tip_deg": twist_deg,
                "estimated_wing_material_mass_kg": 2.0 * half_mass,
            }
        )

    if not cases:
        return {
            "enabled": True,
            "performed": False,
            "passed": False,
            "penalty": 2.0,
            "model": "Euler-Bernoulli beam + thin-wall torsion box",
            "reason": "flow5 spanwise yük dağılımı bulunamadı",
            "settings": settings.to_dict(),
            "conditions": [],
        }

    worst_stress = max(item["stress_utilization"] for item in cases)
    worst_deflection = max(
        item["tip_deflection_fraction_semispan"] for item in cases
    )
    worst_twist = max(item["elastic_twist_tip_deg"] for item in cases)
    deflection_utilization = worst_deflection / max(
        settings.max_tip_deflection_fraction_semispan, 1e-12
    )
    twist_utilization = worst_twist / max(settings.max_elastic_twist_deg, 1e-12)
    utilizations = (worst_stress, deflection_utilization, twist_utilization)
    penalty = float(sum(max(value - 1.0, 0.0) ** 2 for value in utilizations))
    return {
        "enabled": True,
        "performed": True,
        "passed": all(value <= 1.0 for value in utilizations),
        "penalty": penalty,
        "model": "Euler-Bernoulli beam + thin-wall closed-box torsion",
        "fidelity": "preliminary screening; not FEA or aeroelastic re-solution",
        "stress_utilization": float(worst_stress),
        "deflection_utilization": float(deflection_utilization),
        "twist_utilization": float(twist_utilization),
        "max_tip_deflection_m": max(item["tip_deflection_m"] for item in cases),
        "max_elastic_twist_tip_deg": float(worst_twist),
        "estimated_wing_material_mass_kg": max(
            item["estimated_wing_material_mass_kg"] for item in cases
        ),
        "settings": settings.to_dict(),
        "conditions": cases,
    }
