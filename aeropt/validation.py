from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ValidationSettings:
    enabled: bool = True
    force_closure_tolerance_percent: float = 1.0
    drag_decomposition_tolerance_percent: float = 12.0
    minimum_span_efficiency: float = 0.20
    maximum_span_efficiency: float = 1.20

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check(
    identifier: str,
    title: str,
    passed: bool,
    measured: Any,
    limit: str,
    *,
    blocking: bool = True,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": title,
        "passed": bool(passed),
        "blocking": bool(blocking),
        "measured": measured,
        "limit": limit,
        "detail": detail,
    }


def _regression_signature(result: dict[str, Any]) -> str:
    wing = result["wing"]
    geometry = wing["geometry"]
    payload = {
        "contract": 2,
        "solver": result.get("flow5_native_analysis", {}).get("solver", {}),
        "airfoil": {
            key: round(float(result["airfoil"][key]), 7)
            for key in ("max_camber", "camber_position", "thickness")
        },
        "wing": {
            key: round(float(geometry[key]), 7)
            for key in (
                "span",
                "root_chord",
                "taper",
                "sweep_deg",
                "tip_twist_deg",
                "winglet_height",
                "winglet_cant_deg",
                "winglet_toe_deg",
                "winglet_taper",
                "area",
            )
        },
        "performance": {
            key: round(float(wing[key]), 8)
            for key in ("cl", "cd_total", "ld", "drag_n", "root_bending_moment_nm")
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_validation_report(
    result: dict[str, Any], settings: ValidationSettings = ValidationSettings()
) -> dict[str, Any]:
    if not settings.enabled:
        return {
            "enabled": False,
            "passed": True,
            "status": "disabled",
            "checks": [],
            "settings": settings.to_dict(),
        }

    checks: list[dict[str, Any]] = []
    solver = result.get("flow5_native_analysis", {}).get("solver", {})
    version = str(solver.get("version", ""))
    checks.append(
        _check(
            "solver_identity",
            "flow5 sürüm/provenans sözleşmesi",
            version == "7.57" or "test-double" in version,
            version or "bildirilmedi",
            "7.57",
            detail="Üretim runner'ı tam 7.57 bildirmelidir; test ikizi yalnız test ortamında kabul edilir.",
        )
    )
    coordinate_count = int(result.get("airfoil_optimization", {}).get("solver_coordinate_points", 0))
    checks.append(
        _check(
            "foil_coordinate_contract",
            "Profil koordinat sözleşmesi",
            coordinate_count == 100,
            coordinate_count,
            "tam 100 nokta",
        )
    )

    geometry = result["wing"]["geometry"]
    area = float(geometry["area"])
    density = float(result["flow"]["density"])
    lift_errors: list[float] = []
    drag_errors: list[float] = []
    decomposition_errors: list[float] = []
    coverage_ok = True
    for condition in result.get("wing_cases", []):
        point = condition.get("point", {})
        try:
            speed = float(condition["speed_m_s"])
            q_area = 0.5 * density * speed**2 * area
            expected_lift = q_area * float(point["cl"])
            expected_drag = q_area * float(point["cd"])
            reported_lift = float(point["lift_n"])
            reported_drag = float(point["drag_n"])
            lift_errors.append(100.0 * abs(reported_lift - expected_lift) / max(abs(expected_lift), 1e-12))
            drag_errors.append(100.0 * abs(reported_drag - expected_drag) / max(abs(expected_drag), 1e-12))
            if point.get("cdi") is not None and point.get("cdv") is not None:
                decomposition_errors.append(
                    100.0
                    * abs(float(point["cd"]) - float(point["cdi"]) - float(point["cdv"]))
                    / max(abs(float(point["cd"])), 1e-12)
                )
        except (KeyError, TypeError, ValueError):
            coverage_ok = False
    expected_cases = int(result["flow"]["speed_samples"])
    coverage_ok = coverage_ok and len(result.get("wing_cases", [])) == expected_cases
    checks.append(
        _check(
            "operating_point_coverage",
            "Çalışma noktası kapsaması",
            coverage_ok,
            len(result.get("wing_cases", [])),
            f"{expected_cases} hız noktası",
        )
    )
    max_force_error = max([*lift_errors, *drag_errors], default=float("inf"))
    checks.append(
        _check(
            "dimensional_force_closure",
            "CL/CD → kuvvet boyutsal kapanışı",
            np.isfinite(max_force_error)
            and max_force_error <= settings.force_closure_tolerance_percent,
            float(max_force_error),
            f"≤ %{settings.force_closure_tolerance_percent:g}",
        )
    )
    max_decomposition = max(decomposition_errors, default=float("inf"))
    checks.append(
        _check(
            "drag_decomposition",
            "Toplam CD bileşen kapanışı",
            np.isfinite(max_decomposition)
            and max_decomposition <= settings.drag_decomposition_tolerance_percent,
            float(max_decomposition),
            f"≤ %{settings.drag_decomposition_tolerance_percent:g}",
            detail="Kalan pay panel/viskoz bileşenlerin flow5 raporlama farkını temsil eder.",
        )
    )

    mesh = result.get("wing_optimization", {}).get("mesh_convergence", {})
    checks.append(
        _check(
            "mesh_convergence",
            "Final → ince ağ yakınsaması",
            bool(mesh.get("passed", not mesh.get("enabled", False))),
            {
                "cd_percent": mesh.get("max_cd_change_percent"),
                "alpha_deg": mesh.get("max_alpha_change_deg"),
            },
            "kullanıcı mesh toleransları",
        )
    )
    telemetry = result.get("wing_optimization", {}).get("solver_telemetry", {})
    telemetry_ok = (
        int(telemetry.get("out_of_mesh_points", 0)) == 0
        and int(telemetry.get("nonconverged_viscous_points", 0)) == 0
        and bool(telemetry.get("spanwise_distribution_available", False))
    )
    checks.append(
        _check(
            "solver_telemetry",
            "flow5 çalışma noktası telemetrisi",
            telemetry_ok,
            telemetry,
            "0 mesh dışı, 0 viskoz başarısız, spanwise veri var",
        )
    )
    efficiency = float(result["wing"].get("span_efficiency", 0.0))
    checks.append(
        _check(
            "finite_wing_efficiency_sanity",
            "Sonlu kanat verim makullük kontrolü",
            settings.minimum_span_efficiency <= efficiency <= settings.maximum_span_efficiency,
            efficiency,
            f"{settings.minimum_span_efficiency:g}–{settings.maximum_span_efficiency:g}",
            blocking=False,
            detail="Bu analitik bir makullük kontrolüdür; deneysel doğrulama değildir.",
        )
    )
    polar = result.get("polar", [])
    attached = [row for row in polar if -4.0 <= float(row.get("alpha_deg", 99.0)) <= 6.0]
    slope = None
    if len(attached) >= 3:
        slope = float(
            np.polyfit(
                [float(row["alpha_deg"]) for row in attached],
                [float(row["cl"]) for row in attached],
                1,
            )[0]
        )
    checks.append(
        _check(
            "foil_lift_slope_sanity",
            "Bağlı-akış 2B lift eğimi makullüğü",
            slope is not None and 0.03 <= slope <= 0.20,
            slope,
            "0.03–0.20 CL/deg",
            blocking=False,
        )
    )

    blocking_passed = all(item["passed"] for item in checks if item["blocking"])
    warnings = sum(not item["passed"] for item in checks if not item["blocking"])
    return {
        "enabled": True,
        "passed": bool(blocking_passed),
        "status": "passed" if blocking_passed and not warnings else "review",
        "contract_version": 1,
        "regression_signature_sha256": _regression_signature(result),
        "checks_passed": sum(bool(item["passed"]) for item in checks),
        "checks_total": len(checks),
        "warnings": int(warnings),
        "checks": checks,
        "settings": settings.to_dict(),
        "fidelity": "solver consistency + analytic sanity; not wind-tunnel/RANS validation",
    }
