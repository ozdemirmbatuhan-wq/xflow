from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from aeropt.baselines import build_baseline_profile
from aeropt.flow5 import Flow5Runner
from aeropt.models import FLUID_PRESETS, WingGeometry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("runner", type=Path)
    args = parser.parse_args()

    baseline = build_baseline_profile("e818", cst_order=6, solver_point_count=100)
    runner = Flow5Runner(args.runner, timeout_seconds=240.0)
    fluid = FLUID_PRESETS["air"]
    common = {
        "foil": baseline.foil,
        "fluid": fluid,
        "speeds_m_s": [18.0],
        "alpha_min_deg": -2.0,
        "alpha_max_deg": 6.0,
        "alpha_step_deg": 2.0,
        "max_threads": 4,
        "coordinate_points": 100,
        "foil_dat_text": baseline.solver_dat_text,
    }

    foil = runner.analyze_foil(reference_chord_m=0.30, **common)
    assert foil["foil_coordinate_points_used"] == 100
    assert len(foil["polars"][0]["points"]) >= 3

    geometry = WingGeometry(1.8, 0.36, 0.55, 3.0, -1.5, 0.0)
    for method, save_project in (("VLM2", False), ("TRIUNIFORM", True)):
        wing = runner.analyze_wing(
            geometry=geometry,
            method=method,
            save_project=save_project,
            **common,
        )
        assert wing["foil_coordinate_points_used"] == 100
        points = wing["cases"][0]["points"]
        assert len(points) >= 3
        assert all("out_of_mesh" in point for point in points)
        assert all("viscous_converged" in point for point in points)
        assert all("panel4_count" in point and "panel3_count" in point for point in points)
        assert any(point.get("distribution") for point in points)
        assert wing["mesh"]["chordwise_panels"] == 14
        assert wing["mesh"]["half_span_panels"] == 18
        if save_project:
            assert any(point.get("cp_min") is not None for point in points)
            assert "project_fl5" in wing.get("artifact_payloads", {})

    print(
        "Real flow5 7.57 smoke test passed: E818/100 points, VLM2, "
        "TRIUNIFORM, telemetry, Cp_min, spanwise distribution, FL5"
    )


if __name__ == "__main__":
    main()
