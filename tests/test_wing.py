import unittest

import numpy as np

from aeropt.models import FLUID_PRESETS, AirfoilDesign, WingGeometry
from aeropt.wing import evaluate_wing, geometry_for_target_lift


class WingTests(unittest.TestCase):
    def setUp(self):
        self.air = FLUID_PRESETS["air"]
        self.foil = AirfoilDesign(0.025, 0.4, 0.12)

    def test_lifting_line_integrates_to_global_cl(self):
        geometry = WingGeometry(2.4, 0.32, 0.5, 0.0, -2.0, 5.0)
        result = evaluate_wing(self.foil, geometry, self.air, 18.0, modes=12, distribution_points=1001)
        integrated = 2.0 * np.trapezoid(result.local_cl * result.chord, result.y) / geometry.area
        self.assertAlmostEqual(result.cl, float(integrated), places=4)
        self.assertGreater(result.cd_induced, 0.0)
        self.assertGreater(result.cd_profile, 0.0)
        self.assertGreater(result.span_efficiency, 0.75)
        self.assertLess(result.span_efficiency, 1.05)

    def test_target_lift_selects_incidence(self):
        geometry = WingGeometry(2.5, 0.3, 0.45, 0.0, -1.5, 0.0)
        result = geometry_for_target_lift(
            self.foil, geometry, self.air, 18.0, 95.0, -3.0, 14.0, modes=10
        )
        self.assertAlmostEqual(result.lift_n, 95.0, places=5)
        self.assertGreater(result.geometry.alpha_deg, -3.0)
        self.assertLess(result.geometry.alpha_deg, 14.0)

    def test_sweep_does_not_get_free_induced_drag_gain(self):
        straight = evaluate_wing(
            self.foil, WingGeometry(2.4, 0.3, 0.55, 0.0, -1.0, 5.0), self.air, 18.0
        )
        swept = evaluate_wing(
            self.foil, WingGeometry(2.4, 0.3, 0.55, 25.0, -1.0, 5.0), self.air, 18.0
        )
        self.assertGreater(swept.cd_induced, straight.cd_induced)

    def test_xfoil_polar_mesh_drives_section_drag_and_lift_slope(self):
        points = [
            {
                "alpha_deg": float(alpha),
                "cl": 0.105 * (alpha + 2.0),
                "cd": 0.020 + 0.004 * (0.105 * (alpha + 2.0)) ** 2,
                "cdp": 0.018,
                "cm_c4": -0.05,
            }
            for alpha in range(-6, 13)
        ]
        mesh = [{"reynolds": 300_000.0, "points": points}]
        result = evaluate_wing(
            self.foil,
            WingGeometry(2.4, 0.3, 0.55, 0.0, -1.0, 5.0),
            self.air,
            18.0,
            polar_mesh=mesh,
        )
        self.assertEqual(result.section_polar_source, "XFOIL polar mesh")
        self.assertGreater(result.cd_profile, 0.018)


if __name__ == "__main__":
    unittest.main()
