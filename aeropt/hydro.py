from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .models import Fluid, WingGeometry


@dataclass(frozen=True)
class HydroSettings:
    """Preliminary cavitation and free-surface screening inputs."""

    enabled: bool = False
    submergence_depth_m: float = 1.0
    ambient_pressure_pa: float = 101325.0
    vapor_pressure_pa: float = 1705.0
    gravity_m_s2: float = 9.80665
    cavitation_safety_factor: float = 1.20
    minimum_submergence_chords: float = 2.0
    free_surface_screen_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def analyze_hydro(
    *,
    geometry: WingGeometry,
    fluid: Fluid,
    conditions: list[dict[str, Any]],
    settings: HydroSettings,
) -> dict[str, Any]:
    if not settings.enabled:
        return {
            "enabled": False,
            "performed": False,
            "passed": True,
            "penalty": 0.0,
            "model": "disabled",
        }

    static_pressure = (
        settings.ambient_pressure_pa
        + fluid.density * settings.gravity_m_s2 * settings.submergence_depth_m
    )
    rows: list[dict[str, Any]] = []
    missing_cp = 0
    for condition in conditions:
        speed = float(condition["speed_m_s"])
        point = condition.get("point", {})
        q = fluid.dynamic_pressure(speed)
        cp_min_raw = point.get("cp_min")
        if cp_min_raw is None:
            missing_cp += 1
            continue
        cp_min = float(cp_min_raw)
        sigma = (static_pressure - settings.vapor_pressure_pa) / max(q, 1e-12)
        required_sigma = settings.cavitation_safety_factor * max(-cp_min, 0.0)
        cavitation_utilization = required_sigma / max(sigma, 1e-12)
        minimum_pressure = static_pressure + q * cp_min
        chord = geometry.mean_aerodynamic_chord
        froude_chord = speed / max((settings.gravity_m_s2 * chord) ** 0.5, 1e-12)
        submergence_ratio = settings.submergence_depth_m / max(chord, 1e-12)
        free_surface_risk = bool(
            settings.free_surface_screen_enabled
            and submergence_ratio < settings.minimum_submergence_chords
            and froude_chord > 0.40
        )
        rows.append(
            {
                "speed_m_s": speed,
                "cp_min_flow5": cp_min,
                "static_pressure_pa": static_pressure,
                "minimum_pressure_pa": minimum_pressure,
                "vapor_pressure_pa": settings.vapor_pressure_pa,
                "cavitation_number_sigma": sigma,
                "required_sigma_with_safety": required_sigma,
                "cavitation_utilization": cavitation_utilization,
                "cavitation_margin_ratio": 1.0 / max(cavitation_utilization, 1e-12),
                "froude_number_chord": froude_chord,
                "submergence_over_mac": submergence_ratio,
                "free_surface_risk": free_surface_risk,
            }
        )

    if not rows or missing_cp:
        return {
            "enabled": True,
            "performed": False,
            "passed": False,
            "penalty": 1.5,
            "model": "Bernoulli + flow5 panel Cp_min",
            "reason": "Tüm çalışma noktalarında flow5 Cp_min telemetrisi bulunamadı",
            "settings": settings.to_dict(),
            "conditions": rows,
        }

    worst_utilization = max(row["cavitation_utilization"] for row in rows)
    free_surface_risk = any(row["free_surface_risk"] for row in rows)
    penalty = max(worst_utilization - 1.0, 0.0) ** 2
    return {
        "enabled": True,
        "performed": True,
        "passed": worst_utilization <= 1.0,
        "penalty": float(penalty),
        "model": "Bernoulli hydrostatics + flow5 panel Cp_min",
        "fidelity": "cavitation/free-surface screening; not multiphase CFD",
        "cavitation_utilization": float(worst_utilization),
        "minimum_cavitation_margin_ratio": min(
            row["cavitation_margin_ratio"] for row in rows
        ),
        "free_surface_risk": free_surface_risk,
        "settings": settings.to_dict(),
        "conditions": rows,
    }
