from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from .models import AirfoilLike


def _parse_polar(path: Path) -> list[dict[str, float]]:
    points: list[dict[str, float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        try:
            alpha, cl, cd, cdp, cm = map(float, fields[:5])
        except ValueError:
            continue
        if -90.0 <= alpha <= 90.0 and 0.0 <= cd < 10.0:
            points.append({"alpha_deg": alpha, "cl": cl, "cd": cd, "cdp": cdp, "cm_c4": cm})
    return points


def run_xfoil_polar(
    executable: str,
    foil: AirfoilLike,
    reynolds: float,
    mach: float,
    alpha_min: float,
    alpha_max: float,
    alpha_step: float = 1.0,
    timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    # Local import avoids a module cycle: exporters also serializes WingResult.
    from .exporters import airfoil_dat

    exe = Path(executable).expanduser().resolve()
    if not exe.is_file():
        raise ValueError(f"XFOIL çalıştırılabilir dosyası bulunamadı: {exe}")
    if os.name != "nt" and not os.access(exe, os.X_OK):
        raise ValueError(f"XFOIL dosyası çalıştırılabilir değil: {exe}")
    with tempfile.TemporaryDirectory(prefix="aeropt-xfoil-") as tmp:
        work = Path(tmp)
        foil_path = work / "foil.dat"
        polar_path = work / "polar.txt"
        foil_path.write_text(airfoil_dat(foil), encoding="utf-8")
        script = "\n".join(
            [
                "PLOP",
                "G F",
                "",
                f"LOAD {foil_path.name}",
                "NORM",
                "PANE",
                "OPER",
                f"VISC {reynolds:.8g}",
                f"MACH {mach:.6g}",
                "ITER 180",
                "PACC",
                polar_path.name,
                "",
                f"ASEQ {alpha_min:.6g} {alpha_max:.6g} {alpha_step:.6g}",
                "PACC",
                "",
                "QUIT",
                "",
            ]
        )
        completed = subprocess.run(
            [str(exe)],
            input=script,
            cwd=work,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        points = _parse_polar(polar_path) if polar_path.exists() else []
        if not points:
            tail = completed.stdout[-1200:] if completed.stdout else "çıktı yok"
            raise RuntimeError(f"XFOIL geçerli polar üretmedi (kod {completed.returncode}). Son çıktı:\n{tail}")
        return {
            "solver": "XFOIL",
            "return_code": completed.returncode,
            "reynolds": float(reynolds),
            "mach": float(mach),
            "alpha_min_deg": float(alpha_min),
            "alpha_max_deg": float(alpha_max),
            "alpha_step_deg": float(alpha_step),
            "points": points,
            "converged_points": len(points),
        }


def _interpolate_between(
    first: dict[str, float], second: dict[str, float], fraction: float
) -> dict[str, float]:
    keys = {"alpha_deg", "cl", "cd", "cdp", "cm_c4"}
    result: dict[str, float] = {}
    for key in keys:
        if key in first and key in second:
            result[key] = float(first[key] + fraction * (second[key] - first[key]))
    if "cl" in result and "cd" in result:
        result["ld"] = float(result["cl"] / max(result["cd"], 1e-12))
    return result


def point_at_alpha(points: list[dict[str, float]], alpha_deg: float) -> dict[str, float] | None:
    """Interpolate a converged XFOIL polar at angle of attack."""
    ordered = sorted(points, key=lambda point: point["alpha_deg"])
    for first, second in zip(ordered, ordered[1:]):
        a0, a1 = first["alpha_deg"], second["alpha_deg"]
        if a0 <= alpha_deg <= a1 and a1 > a0:
            return _interpolate_between(first, second, (alpha_deg - a0) / (a1 - a0))
    for point in ordered:
        if abs(point["alpha_deg"] - alpha_deg) < 1e-9:
            return {**point, "ld": point["cl"] / max(point["cd"], 1e-12)}
    return None


def point_at_cl(points: list[dict[str, float]], target_cl: float) -> dict[str, float] | None:
    """Find the lowest-|alpha| converged branch crossing a requested lift coefficient."""
    ordered = sorted(points, key=lambda point: point["alpha_deg"])
    crossings: list[dict[str, float]] = []
    for first, second in zip(ordered, ordered[1:]):
        cl0, cl1 = first["cl"], second["cl"]
        if (cl0 - target_cl) * (cl1 - target_cl) <= 0.0 and abs(cl1 - cl0) > 1e-10:
            crossings.append(
                _interpolate_between(first, second, (target_cl - cl0) / (cl1 - cl0))
            )
    if crossings:
        return min(crossings, key=lambda point: abs(point.get("alpha_deg", 0.0)))
    exact = [point for point in ordered if abs(point["cl"] - target_cl) < 1e-9]
    if exact:
        point = min(exact, key=lambda item: abs(item["alpha_deg"]))
        return {**point, "ld": point["cl"] / max(point["cd"], 1e-12)}
    return None


def _point_at_cl_clamped(
    points: list[dict[str, float]], target_cl: float
) -> tuple[dict[str, float] | None, float]:
    point = point_at_cl(points, target_cl)
    if point is not None:
        return point, 0.0
    if not points:
        return None, float("inf")
    nearest = min(points, key=lambda item: abs(item["cl"] - target_cl))
    distance = abs(nearest["cl"] - target_cl)
    return {**nearest, "ld": nearest["cl"] / max(nearest["cd"], 1e-12)}, distance


def polar_mesh_cd(polar_mesh: list[dict[str, Any]], cl: float, reynolds: float) -> float:
    """Interpolate section CD in CL and log(Re), with a soft out-of-polar penalty."""
    available: list[tuple[float, float]] = []
    for polar in polar_mesh:
        point, outside = _point_at_cl_clamped(polar.get("points", []), cl)
        if point is not None:
            available.append(
                (float(polar["reynolds"]), float(point["cd"] + 0.05 * outside**2))
            )
    if not available:
        raise ValueError("XFOIL polar ağı boş")
    available.sort(key=lambda item: item[0])
    re = max(float(reynolds), 1.0)
    if re <= available[0][0]:
        return available[0][1]
    if re >= available[-1][0]:
        return available[-1][1]
    log_re = np.log(re)
    for (re0, cd0), (re1, cd1) in zip(available, available[1:]):
        if re0 <= re <= re1:
            fraction = (log_re - np.log(re0)) / max(np.log(re1) - np.log(re0), 1e-12)
            return float(cd0 + fraction * (cd1 - cd0))
    return available[-1][1]


def polar_mesh_cl_limit(
    polar_mesh: list[dict[str, Any]], reynolds: float, *, positive: bool = True
) -> float:
    """Interpolate the largest converged positive/negative section CL magnitude."""
    limits: list[tuple[float, float]] = []
    for polar in polar_mesh:
        values = [float(point["cl"]) for point in polar.get("points", [])]
        if not values:
            continue
        limit = max(values) if positive else abs(min(values))
        limits.append((float(polar["reynolds"]), max(float(limit), 0.05)))
    if not limits:
        raise ValueError("XFOIL polar ağı boş")
    limits.sort(key=lambda item: item[0])
    re = max(float(reynolds), 1.0)
    if re <= limits[0][0]:
        return limits[0][1]
    if re >= limits[-1][0]:
        return limits[-1][1]
    for (re0, value0), (re1, value1) in zip(limits, limits[1:]):
        if re0 <= re <= re1:
            fraction = (np.log(re) - np.log(re0)) / max(np.log(re1) - np.log(re0), 1e-12)
            return float(value0 + fraction * (value1 - value0))
    return limits[-1][1]


def polar_mesh_lift_properties(
    polar_mesh: list[dict[str, Any]], reynolds: float
) -> tuple[float, float]:
    """Return (dCL/dalpha per rad, zero-lift alpha rad) from the nearest XFOIL polar."""
    available = [polar for polar in polar_mesh if len(polar.get("points", [])) >= 4]
    if not available:
        raise ValueError("XFOIL lift eğimi için yeterli polar noktası yok")
    polar = min(available, key=lambda item: abs(np.log(max(item["reynolds"], 1.0) / max(reynolds, 1.0))))
    points = sorted(polar["points"], key=lambda point: point["alpha_deg"])
    positive_peak = max(float(point["cl"]) for point in points)
    selected = [
        point
        for point in points
        if -6.0 <= point["alpha_deg"] <= 8.0 and abs(point["cl"]) <= 0.72 * max(positive_peak, 0.4)
    ]
    if len(selected) < 4:
        selected = points[: max(4, min(len(points), 10))]
    alpha_rad = np.radians([point["alpha_deg"] for point in selected])
    cl_values = np.asarray([point["cl"] for point in selected], dtype=float)
    slope, intercept = np.polyfit(alpha_rad, cl_values, 1)
    slope = float(np.clip(slope, 3.0, 8.5))
    alpha_zero = float(-intercept / slope)
    return slope, alpha_zero
