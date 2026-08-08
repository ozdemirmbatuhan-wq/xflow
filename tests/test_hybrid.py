import unittest

import numpy as np

from aeropt.hybrid import compare_internal_to_xfoil, optimize_cst_with_xfoil
from aeropt.models import AirfoilDesign
from aeropt.xfoil import point_at_alpha, point_at_cl, polar_mesh_cd


def fake_xfoil(foil):
    points = []
    shape_penalty = 0.08 * (foil.thickness - 0.12) ** 2 + 0.04 * (foil.max_camber - 0.025) ** 2
    for alpha in np.arange(-6.0, 15.0, 1.0):
        cl = 0.105 * (alpha + 2.0 + 18.0 * foil.max_camber)
        cd = 0.0085 + 0.006 * (cl - 0.55) ** 2 + shape_penalty
        points.append(
            {
                "alpha_deg": float(alpha),
                "cl": float(cl),
                "cd": float(cd),
                "cdp": float(cd * 0.85),
                "cm_c4": -0.04,
            }
        )
    return {"solver": "fake-XFOIL", "points": points, "converged_points": len(points)}


class HybridTests(unittest.TestCase):
    def test_polar_interpolation_and_discrepancy_rule(self):
        polar = fake_xfoil(AirfoilDesign(0.02, 0.4, 0.12))["points"]
        at_alpha = point_at_alpha(polar, 3.5)
        at_cl = point_at_cl(polar, 0.60)
        self.assertIsNotNone(at_alpha)
        self.assertIsNotNone(at_cl)
        check = compare_internal_to_xfoil(
            internal_point={"alpha_deg": 3.5, "cl": 0.9, "cd": 0.005},
            xfoil_points=polar,
            target_cl=0.6,
            cl_tolerance_percent=5.0,
            cd_tolerance_percent=15.0,
        )
        self.assertFalse(check["accepted"])

    def test_parallel_cst_search_returns_xfoil_scored_profile(self):
        foil, polar, metadata = optimize_cst_with_xfoil(
            initial_foil=AirfoilDesign(0.025, 0.4, 0.12),
            target_cl=0.60,
            alpha_bounds=(-2.0, 12.0),
            camber_bounds=(0.0, 0.06),
            camber_position_bounds=(0.25, 0.65),
            thickness_bounds=(0.10, 0.16),
            candidate_budget=32,
            workers=16,
            seed=9,
            evaluator=fake_xfoil,
        )
        self.assertEqual(foil.family, "CST3")
        self.assertEqual(metadata["candidates_evaluated"], 32)
        self.assertGreaterEqual(metadata["parallel_workers_used"], 1)
        self.assertGreater(len(polar["points"]), 10)

    def test_reynolds_mesh_interpolation(self):
        low = fake_xfoil(AirfoilDesign(0.02, 0.4, 0.12))["points"]
        high = [{**point, "cd": point["cd"] * 0.8} for point in low]
        mesh = [
            {"reynolds": 100_000.0, "points": low},
            {"reynolds": 400_000.0, "points": high},
        ]
        cd_low = polar_mesh_cd(mesh, 0.6, 100_000.0)
        cd_mid = polar_mesh_cd(mesh, 0.6, 200_000.0)
        cd_high = polar_mesh_cd(mesh, 0.6, 400_000.0)
        self.assertGreater(cd_low, cd_mid)
        self.assertGreater(cd_mid, cd_high)


if __name__ == "__main__":
    unittest.main()
