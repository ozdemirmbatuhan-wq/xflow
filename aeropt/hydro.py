from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .models import Fluid, WingGeometry


@dataclass(frozen=True)
class HydroSettings:
    """Preliminary cavitation and free-surface screening inputs."""

    enabled: bool = False
    constraint_mode: str = "hard"
    submergence_depth_m: float = 1.0
    ambient_pressure_pa: float = 101325.0
    vapor_pressure_pa: float = 1705.0
    gravity_m_s2: float = 9.80665
    cavitation_safety_factor: float = 1.20
    near_risk_utilization: float = 0.80
    minimum_submergence_chords: float = 2.0
    free_surface_screen_enabled: bool = True

    def __post_init__(self) -> None:
        if self.constraint_mode not in {"hard", "report_only"}:
            raise ValueError("Kavitasyon modu 'hard' veya 'report_only' olmalı")
        if not 0.0 < self.near_risk_utilization < 1.0:
            raise ValueError("Kavitasyon yakın-risk eşiği 0 ile 1 arasında olmalı")

    @property
    def optimization_active(self) -> bool:
        return bool(self.enabled and self.constraint_mode == "hard")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "optimization_active": self.optimization_active}


def _static_pressure(
    fluid: Fluid, settings: HydroSettings, depth_m: float | None = None
) -> float:
    depth = settings.submergence_depth_m if depth_m is None else float(depth_m)
    return (
        settings.ambient_pressure_pa
        + fluid.density * settings.gravity_m_s2 * depth
    )


def _group_summary(
    panels: list[dict[str, Any]], key: str
) -> dict[str, dict[str, float]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for panel in panels:
        groups.setdefault(str(panel.get(key, "unknown")), []).append(panel)
    output: dict[str, dict[str, float]] = {}
    for name, rows in groups.items():
        area = sum(float(row["area_m2"]) for row in rows)
        risk_area = sum(
            float(row["area_m2"])
            for row in rows
            if float(row["cavitation_utilization"]) >= 1.0
        )
        onset_area = sum(
            float(row["area_m2"])
            for row in rows
            if float(row["physical_cavitation_utilization"]) >= 1.0
        )
        output[name] = {
            "area_m2": float(area),
            "risk_area_m2": float(risk_area),
            "risk_area_percent": float(100.0 * risk_area / max(area, 1.0e-12)),
            "physical_onset_area_percent": float(
                100.0 * onset_area / max(area, 1.0e-12)
            ),
            "maximum_utilization": float(
                max(float(row["cavitation_utilization"]) for row in rows)
            ),
        }
    return output


def _union_length(intervals: list[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted((min(a, b), max(a, b)) for a, b in intervals)
    start, end = ordered[0]
    total = 0.0
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            total += end - start
            start, end = next_start, next_end
    return total + end - start


def _panel_risk_summary(
    *,
    raw_panels: list[dict[str, Any]],
    geometry: WingGeometry,
    fluid: Fluid,
    speed_m_s: float,
    depth_m: float,
    settings: HydroSettings,
    include_panels: bool,
) -> dict[str, Any]:
    q = fluid.dynamic_pressure(speed_m_s)
    static_pressure = _static_pressure(fluid, settings, depth_m)
    sigma = (static_pressure - settings.vapor_pressure_pa) / max(q, 1.0e-12)
    panels: list[dict[str, Any]] = []
    for raw in raw_panels:
        try:
            cp = float(raw["cp"])
            area = float(raw["area_m2"])
            x_m = float(raw["x_m"])
            y_m = float(raw["y_m"])
            z_m = float(raw["z_m"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (cp, area, x_m, y_m, z_m)):
            continue
        if area <= 0.0:
            continue
        physical_utilization = max(-cp, 0.0) / max(sigma, 1.0e-12)
        utilization = settings.cavitation_safety_factor * physical_utilization
        minimum_pressure = static_pressure + q * cp
        state = (
            "risk"
            if utilization >= 1.0
            else "near"
            if utilization >= settings.near_risk_utilization
            else "safe"
        )
        panel = {
            "panel_index": int(raw.get("panel_index", len(panels))),
            "wing_index": int(raw.get("wing_index", 0)),
            "surface_index": int(raw.get("surface_index", -1)),
            "surface": str(raw.get("surface", "unknown")),
            "component": str(raw.get("component", "main_wing")),
            "side": str(raw.get("side", "unknown")),
            "x_m": x_m,
            "y_m": y_m,
            "z_m": z_m,
            "area_m2": area,
            "cp": cp,
            "minimum_pressure_pa": float(minimum_pressure),
            "physical_cavitation_utilization": float(physical_utilization),
            "cavitation_utilization": float(utilization),
            "risk_state": state,
            "leading_edge_panel": bool(raw.get("leading_edge_panel", False)),
            "trailing_edge_panel": bool(raw.get("trailing_edge_panel", False)),
        }
        vertices = raw.get("vertices")
        if isinstance(vertices, list) and len(vertices) >= 3:
            panel["vertices"] = vertices
        panels.append(panel)

    if not panels:
        return {"available": False, "panel_count": 0}

    total_area = sum(float(panel["area_m2"]) for panel in panels)
    risk_area = sum(
        float(panel["area_m2"])
        for panel in panels
        if float(panel["cavitation_utilization"]) >= 1.0
    )
    near_area = sum(
        float(panel["area_m2"])
        for panel in panels
        if settings.near_risk_utilization
        <= float(panel["cavitation_utilization"])
        < 1.0
    )
    onset_area = sum(
        float(panel["area_m2"])
        for panel in panels
        if float(panel["physical_cavitation_utilization"]) >= 1.0
    )
    pressure_deficit_integral = sum(
        max(settings.vapor_pressure_pa - float(panel["minimum_pressure_pa"]), 0.0)
        * float(panel["area_m2"])
        for panel in panels
    )
    severity_index = sum(
        max(float(panel["cavitation_utilization"]) - 1.0, 0.0)
        * float(panel["area_m2"])
        for panel in panels
    ) / max(total_area, 1.0e-12)
    critical = max(panels, key=lambda panel: float(panel["cavitation_utilization"]))
    risk_panels = [
        panel
        for panel in panels
        if float(panel["cavitation_utilization"]) >= 1.0
    ]
    first_risk = min(
        risk_panels,
        key=lambda panel: abs(float(panel["y_m"])),
        default=None,
    )

    bin_count = 24
    span_bins: list[dict[str, Any]] = [
        {
            "area": 0.0,
            "weighted_utilization": 0.0,
            "maximum_utilization": 0.0,
            "risk_area": 0.0,
            "cp_min": math.inf,
        }
        for _ in range(bin_count)
    ]
    half_span = max(0.5 * geometry.span, 1.0e-12)
    for panel in panels:
        eta = min(abs(float(panel["y_m"])) / half_span, 1.0)
        index = min(int(eta * bin_count), bin_count - 1)
        bucket = span_bins[index]
        area = float(panel["area_m2"])
        utilization = float(panel["cavitation_utilization"])
        bucket["area"] += area
        bucket["weighted_utilization"] += area * utilization
        bucket["maximum_utilization"] = max(
            float(bucket["maximum_utilization"]), utilization
        )
        if utilization >= 1.0:
            bucket["risk_area"] += area
        bucket["cp_min"] = min(float(bucket["cp_min"]), float(panel["cp"]))
    spanwise_distribution: list[dict[str, float]] = []
    affected_bins = 0
    for index, bucket in enumerate(span_bins):
        area = float(bucket["area"])
        if area <= 0.0:
            continue
        if float(bucket["maximum_utilization"]) >= 1.0:
            affected_bins += 1
        eta = (index + 0.5) / bin_count
        spanwise_distribution.append(
            {
                "span_fraction": float(eta),
                "y_m": float(eta * half_span),
                "mean_utilization": float(bucket["weighted_utilization"] / area),
                "maximum_utilization": float(bucket["maximum_utilization"]),
                "risk_area_percent": float(100.0 * bucket["risk_area"] / area),
                "cp_min": float(bucket["cp_min"]),
            }
        )

    risk_intervals: list[tuple[float, float]] = []
    for panel in risk_panels:
        vertices = panel.get("vertices")
        if isinstance(vertices, list) and vertices:
            y_values = [float(vertex[1]) for vertex in vertices if len(vertex) == 3]
            if y_values:
                risk_intervals.append((min(y_values), max(y_values)))
                continue
        y_m = float(panel["y_m"])
        risk_intervals.append((y_m, y_m))

    def location(panel: dict[str, Any] | None) -> dict[str, Any] | None:
        if panel is None:
            return None
        return {
            "x_m": float(panel["x_m"]),
            "y_m": float(panel["y_m"]),
            "z_m": float(panel["z_m"]),
            "span_fraction": float(min(abs(float(panel["y_m"])) / half_span, 1.0)),
            "component": str(panel["component"]),
            "surface": str(panel["surface"]),
            "cp": float(panel["cp"]),
            "cavitation_utilization": float(panel["cavitation_utilization"]),
        }

    result: dict[str, Any] = {
        "available": True,
        "speed_m_s": float(speed_m_s),
        "submergence_depth_m": float(depth_m),
        "static_pressure_pa": float(static_pressure),
        "cavitation_number_sigma": float(sigma),
        "panel_count": len(panels),
        "panel_area_sum_m2": float(total_area),
        "risk_area_m2": float(risk_area),
        "risk_area_percent": float(100.0 * risk_area / max(total_area, 1.0e-12)),
        "near_risk_area_percent": float(
            100.0 * near_area / max(total_area, 1.0e-12)
        ),
        "safe_area_percent": float(
            100.0 * max(total_area - risk_area - near_area, 0.0)
            / max(total_area, 1.0e-12)
        ),
        "physical_onset_area_m2": float(onset_area),
        "physical_onset_area_percent": float(
            100.0 * onset_area / max(total_area, 1.0e-12)
        ),
        "maximum_utilization": float(critical["cavitation_utilization"]),
        "cavitation_severity_index": float(severity_index),
        "pressure_deficit_area_integral_n": float(pressure_deficit_integral),
        "affected_span_bins_percent": float(
            100.0 * affected_bins / max(len(spanwise_distribution), 1)
        ),
        "affected_projected_span_percent": float(
            100.0 * _union_length(risk_intervals) / max(geometry.span, 1.0e-12)
        ),
        "critical_location": location(critical),
        "first_risk_location": location(first_risk),
        "component_summary": _group_summary(panels, "component"),
        "surface_summary": _group_summary(panels, "surface"),
        "spanwise_distribution": spanwise_distribution,
    }
    if include_panels:
        result["panels"] = panels
    return result


def _sensitivity_axis(
    *,
    raw_panels: list[dict[str, Any]],
    geometry: WingGeometry,
    fluid: Fluid,
    settings: HydroSettings,
    base_speed_m_s: float,
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    speeds = sorted(
        {
            round(max(base_speed_m_s * factor, 0.05), 6)
            for factor in (0.75, 0.875, 1.0, 1.125, 1.25)
        }
    )
    depths = sorted(
        {
            round(max(settings.submergence_depth_m * factor, 0.001), 6)
            for factor in (0.25, 0.5, 1.0, 1.5, 2.0)
        }
    )

    def compact(summary: dict[str, Any]) -> dict[str, float]:
        return {
            "risk_area_percent": float(summary.get("risk_area_percent", 0.0)),
            "physical_onset_area_percent": float(
                summary.get("physical_onset_area_percent", 0.0)
            ),
            "maximum_utilization": float(summary.get("maximum_utilization", 0.0)),
            "cavitation_severity_index": float(
                summary.get("cavitation_severity_index", 0.0)
            ),
        }

    speed_rows: list[dict[str, float]] = []
    for speed in speeds:
        summary = _panel_risk_summary(
            raw_panels=raw_panels,
            geometry=geometry,
            fluid=fluid,
            speed_m_s=speed,
            depth_m=settings.submergence_depth_m,
            settings=settings,
            include_panels=False,
        )
        speed_rows.append({"speed_m_s": float(speed), **compact(summary)})
    depth_rows: list[dict[str, float]] = []
    for depth in depths:
        summary = _panel_risk_summary(
            raw_panels=raw_panels,
            geometry=geometry,
            fluid=fluid,
            speed_m_s=base_speed_m_s,
            depth_m=depth,
            settings=settings,
            include_panels=False,
        )
        depth_rows.append({"submergence_depth_m": float(depth), **compact(summary)})
    return speed_rows, depth_rows


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
            "constraint_passed": True,
            "constraint_mode": settings.constraint_mode,
            "penalty": 0.0,
            "model": "disabled",
        }

    static_pressure = _static_pressure(fluid, settings)
    rows: list[dict[str, Any]] = []
    detailed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    missing_cp = 0
    for condition in conditions:
        speed = float(condition["speed_m_s"])
        point = condition.get("point", {})
        telemetry = condition.get("panel_telemetry")
        raw_panels = (
            telemetry.get("panels", []) if isinstance(telemetry, dict) else []
        )
        q = fluid.dynamic_pressure(speed)
        cp_min_raw = point.get("cp_min")
        if cp_min_raw is None and raw_panels:
            cp_min_raw = min(float(panel["cp"]) for panel in raw_panels)
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
        row: dict[str, Any] = {
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
            "panel_map_available": bool(raw_panels),
        }
        if raw_panels:
            panel_summary = _panel_risk_summary(
                raw_panels=raw_panels,
                geometry=geometry,
                fluid=fluid,
                speed_m_s=speed,
                depth_m=settings.submergence_depth_m,
                settings=settings,
                include_panels=True,
            )
            row["panel_risk_summary"] = {
                key: value
                for key, value in panel_summary.items()
                if key not in {"panels", "spanwise_distribution"}
            }
            detailed.append((telemetry, panel_summary))
        rows.append(row)

    constraint_passed_when_missing = settings.constraint_mode == "report_only"
    if not rows or missing_cp:
        return {
            "enabled": True,
            "performed": False,
            "passed": False,
            "constraint_passed": constraint_passed_when_missing,
            "constraint_mode": settings.constraint_mode,
            "penalty": 0.0 if constraint_passed_when_missing else 1.5,
            "raw_penalty": 1.5,
            "model": "Bernoulli + flow5 panel Cp_min",
            "reason": "Tüm çalışma noktalarında flow5 Cp_min telemetrisi bulunamadı",
            "settings": settings.to_dict(),
            "conditions": rows,
            "panel_map_available": False,
        }

    worst_utilization = max(float(row["cavitation_utilization"]) for row in rows)
    free_surface_risk = any(bool(row["free_surface_risk"]) for row in rows)
    raw_penalty = max(worst_utilization - 1.0, 0.0) ** 2
    passed = worst_utilization <= 1.0
    constraint_passed = bool(passed or settings.constraint_mode == "report_only")
    result: dict[str, Any] = {
        "enabled": True,
        "performed": True,
        "passed": passed,
        "constraint_passed": constraint_passed,
        "constraint_mode": settings.constraint_mode,
        "penalty": float(raw_penalty if settings.constraint_mode == "hard" else 0.0),
        "raw_penalty": float(raw_penalty),
        "model": "Bernoulli hydrostatics + flow5 panel Cp",
        "fidelity": (
            "single-phase flow5 Cp cavitation-onset screening; frozen-Cp speed/depth "
            "sensitivity, not multiphase cavity CFD"
        ),
        "cavitation_utilization": float(worst_utilization),
        "minimum_cavitation_margin_ratio": min(
            float(row["cavitation_margin_ratio"]) for row in rows
        ),
        "free_surface_risk": free_surface_risk,
        "settings": settings.to_dict(),
        "conditions": rows,
        "panel_map_available": bool(detailed),
    }
    if detailed:
        telemetry, panel_map = max(
            detailed,
            key=lambda item: float(item[1].get("maximum_utilization", 0.0)),
        )
        panel_map["cp_definition"] = str(
            telemetry.get("cp_definition", "flow5 panel pressure coefficient")
        )
        panel_map["thin_surfaces"] = bool(telemetry.get("thin_surfaces", True))
        panel_map["upper_lower_resolved"] = bool(
            telemetry.get("upper_lower_resolved", False)
        )
        result["panel_map"] = panel_map
        for key in (
            "risk_area_m2",
            "risk_area_percent",
            "near_risk_area_percent",
            "safe_area_percent",
            "physical_onset_area_m2",
            "physical_onset_area_percent",
            "cavitation_severity_index",
            "pressure_deficit_area_integral_n",
            "affected_span_bins_percent",
            "affected_projected_span_percent",
        ):
            result[key] = panel_map[key]
        speed_rows, depth_rows = _sensitivity_axis(
            raw_panels=telemetry["panels"],
            geometry=geometry,
            fluid=fluid,
            settings=settings,
            base_speed_m_s=float(panel_map["speed_m_s"]),
        )
        result["speed_sensitivity"] = speed_rows
        result["depth_sensitivity"] = depth_rows
    return result
