#!/usr/bin/env python3
"""Deterministic protocol double for tests; it is not an aerodynamic solver."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET


PROTOCOL = "aeropt-flow5-v1"


def alphas(request: dict) -> list[float]:
    settings = request["alpha"]
    minimum = float(settings["min_deg"])
    maximum = float(settings["max_deg"])
    step = float(settings["step_deg"])
    count = int(math.floor((maximum - minimum) / step + 1e-9))
    values = [minimum + index * step for index in range(count + 1)]
    if not values or values[-1] < maximum - 1e-8:
        values.append(maximum)
    return values


def foil_metrics(path: Path) -> tuple[float, float]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 2:
            rows.append((float(fields[0]), float(fields[1])))
    thickness = max(y for _, y in rows) - min(y for _, y in rows)
    relevant = [y for x, y in rows if 0.15 <= x <= 0.85]
    mean_y = sum(relevant) / max(1, len(relevant))
    return thickness, mean_y


def assert_coordinate_count(request: dict, path: Path) -> None:
    expected = int(request.get("foil_coordinate_points", 100))
    rows = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()[1:]
        if len(line.split()) >= 2
    ]
    if len(rows) != expected:
        raise ValueError(f"expected {expected} foil coordinates, found {len(rows)}")


def run_foil(request: dict) -> dict:
    foil_path = Path(request["paths"]["foil.dat"])
    assert_coordinate_count(request, foil_path)
    thickness, mean_y = foil_metrics(foil_path)
    shape_penalty = 0.020 * (thickness - 0.125) ** 2 + 0.004 * (mean_y - 0.018) ** 2
    polars = []
    for case in request["cases"]:
        reynolds = float(case["reynolds"])
        reynolds_penalty = 0.0020 * (450_000.0 / max(reynolds, 40_000.0)) ** 0.22
        points = []
        for alpha in alphas(request):
            cl = 0.112 * (alpha + 1.25 + 9.0 * mean_y)
            cd = 0.0062 + reynolds_penalty + shape_penalty + 0.0068 * (cl - 0.62) ** 2
            points.append(
                {
                    "alpha_deg": alpha,
                    "cl": cl,
                    "cd": cd,
                    "cdp": 0.72 * cd,
                    "cm_c4": -0.035 - 0.45 * mean_y,
                }
            )
        polars.append({**case, "points": points})
    return {"polars": polars}


def plane_geometry(path: Path) -> tuple[float, float, float, float, float, float, float]:
    text = path.read_text(encoding="utf-8").replace("<!DOCTYPE flow5>", "")
    root = ET.fromstring(text)
    sections = root.findall(".//Section")
    root_chord = float(sections[0].findtext("Chord"))
    tip_chord = float(sections[-1].findtext("Chord"))
    mid_chord = float(sections[len(sections) // 2].findtext("Chord"))
    half_span = float(sections[-1].findtext("y_position"))
    tip_offset = float(sections[-1].findtext("xOffset"))
    twist = float(sections[-1].findtext("Twist"))
    mid_twist = float(sections[len(sections) // 2].findtext("Twist"))
    span = 2.0 * half_span
    taper = tip_chord / root_chord
    sweep = math.degrees(math.atan2(tip_offset - 0.25 * (root_chord - tip_chord), half_span))
    linear_mid = 0.5 * (root_chord + tip_chord)
    mid_factor = mid_chord / max(linear_mid, 1e-12)
    return span, root_chord, taper, sweep, twist, mid_factor, mid_twist


def run_wing(request: dict) -> dict:
    section_foils = request.get("section_foils", [])
    if section_foils:
        for section in section_foils:
            assert_coordinate_count(request, Path(request["paths"][section["path_key"]]))
    else:
        assert_coordinate_count(request, Path(request["paths"]["foil.dat"]))
    span, root_chord, taper, sweep, twist, mid_factor, mid_twist = plane_geometry(
        Path(request["paths"]["plane.xml"])
    )
    tip_chord = root_chord * taper
    mid_chord = 0.5 * (root_chord + tip_chord) * mid_factor
    area = 0.25 * span * (root_chord + 2.0 * mid_chord + tip_chord)
    aspect_ratio = span * span / area
    density = float(request["fluid"]["density_kg_m3"])
    method = request["method"]
    mesh = request.get("mesh", {})
    chordwise_panels = int(mesh.get("chordwise_panels", 14))
    half_span_panels = int(mesh.get("half_span_panels", 18))
    nominal_panels = 2 * chordwise_panels * half_span_panels
    mesh_delta = 0.012 / math.sqrt(max(nominal_panels, 1))
    method_delta = 0.00015 if method in {"TRIUNIFORM", "TRILINEAR", "QUADS"} else 0.00045
    efficiency = max(
        0.56,
        0.93
        - 0.10 * (taper - 0.48) ** 2
        - 0.0007 * sweep**2
        - 0.003 * (twist + 1.5) ** 2
        - 0.025 * (mid_factor - 1.08) ** 2
        - 0.002 * (mid_twist - 0.55 * twist) ** 2,
    )
    cases = []
    for case in request["cases"]:
        speed = float(case["speed_m_s"])
        q = 0.5 * density * speed**2
        points = []
        for alpha in alphas(request):
            cl = 0.145 * (alpha + 0.7 + 0.11 * twist)
            cdi = cl * cl / (math.pi * aspect_ratio * efficiency)
            cdv = 0.0080 + 0.0012 * (root_chord - 0.33) ** 2 + method_delta + 0.0014 * cl**2
            cd = cdi + cdv + mesh_delta
            distribution = []
            for index in range(-5, 6):
                eta = index / 5.0
                eta_abs = abs(eta)
                if eta_abs <= 0.5:
                    chord = root_chord + 2.0 * eta_abs * (mid_chord - root_chord)
                else:
                    chord = mid_chord + 2.0 * (eta_abs - 0.5) * (tip_chord - mid_chord)
                local_cl = cl * math.sqrt(max(0.0, 1.0 - eta * eta))
                distribution.append(
                    {
                        "y_m": eta * span / 2.0,
                        "chord_m": chord,
                        "local_cl": local_cl,
                        "lift_n_per_m": q * chord * local_cl,
                        "reynolds": speed * chord / float(
                            request["fluid"]["kinematic_viscosity_m2_s"]
                        ),
                        "induced_angle_deg": -2.0 * abs(eta),
                        "cdi": cdi,
                        "cdv": cdv,
                        "bending_moment_nm": q * area * cl * span * (1.0 - abs(eta)) / 8.0,
                        "twist_deg": twist * abs(eta),
                        "converged": True,
                    }
                )
            points.append(
                {
                    "alpha_deg": alpha,
                    "cl": cl,
                    "cd": cd,
                    "cdi": cdi,
                    "cdv": cdv,
                    "cm": -0.04,
                    "lift_n": q * area * cl,
                    "drag_n": q * area * cd,
                    "root_bending_moment_nm": q * area * cl * span / 8.0,
                    "out_of_mesh": False,
                    "viscous_converged": True,
                    "viscous_converged_fraction": 1.0,
                    "station_count": len(distribution),
                    "panel4_count": nominal_panels,
                    "panel3_count": 2 * nominal_panels,
                    "cp_min": -0.85 - 0.65 * max(cl, 0.0),
                    "distribution": distribution,
                }
            )
        cases.append({"speed_m_s": speed, "method": method, "points": points})
    artifacts = {}
    if request.get("save_project"):
        project = Path(request["output_dir"]) / "aeropt-optimized.fl5"
        project.write_bytes(b"FLOW5_TEST_DOUBLE_PROJECT\x00")
        artifacts["project_fl5"] = str(project)
    return {
        "cases": cases,
        "artifacts": artifacts,
        "mesh": {
            "chordwise_panels": chordwise_panels,
            "half_span_panels": half_span_panels,
            "actual_panel4_count": nominal_panels,
            "actual_panel3_count": 2 * nominal_panels,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args()
    response = {
        "protocol": PROTOCOL,
        "ok": False,
        "solver": {"name": "flow5", "version": "7.57-test-double"},
    }
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        if request.get("protocol") != PROTOCOL:
            raise ValueError("protocol mismatch")
        payload = run_foil(request) if request.get("mode") == "foil" else run_wing(request)
        response.update(
            ok=True,
            mode=request.get("mode"),
            foil_coordinate_points_used=int(request.get("foil_coordinate_points", 100)),
            **payload,
        )
    except Exception as exc:  # pragma: no cover - diagnostic path
        response["error"] = str(exc)
    Path(args.response).write_text(json.dumps(response), encoding="utf-8")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
