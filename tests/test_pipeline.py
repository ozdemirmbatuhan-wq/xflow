import json
import base64
import io
import unittest
import zipfile
from xml.etree import ElementTree as ET

from aeropt.pipeline import DEFAULT_REQUEST, InputError, run_design


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_design(
            {
                "solver": {
                    "quality": "quick",
                    "seed": 7,
                    "lifting_line_modes": 8,
                    "airfoil_strategy": "internal",
                }
            }
        )

    def test_end_to_end_design_is_serializable_and_hits_lift(self):
        result = self.result
        self.assertIn(result["status"], {"feasible", "review"})
        self.assertAlmostEqual(result["wing"]["lift_n"], 120.0, delta=2.4)
        self.assertGreater(result["wing"]["ld"], 5.0)
        json.dumps(result, allow_nan=False)

    def test_exports_are_consistent(self):
        exports = self.result["exports"]
        first_line = exports["airfoil_dat"].splitlines()[0]
        self.assertEqual(first_line, self.result["airfoil"]["name"])
        xml_text = exports["plane_xml"].replace("<!DOCTYPE flow5>", "")
        root = ET.fromstring(xml_text)
        self.assertEqual(root.tag, "xflplane")
        foil_names = [node.text for node in root.findall(".//Left_Side_FoilName")]
        self.assertEqual(foil_names, [first_line, first_line, first_line])
        self.assertIn("group,parameter,value,unit", exports["results_csv"])
        self.assertIn("\nv ", exports["wing_obj"])
        self.assertIn("\nf ", exports["wing_obj"])
        bundle = base64.b64decode(exports["flow5_bundle_base64"])
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            self.assertIn("aeropt-airfoil.dat", archive.namelist())
            self.assertIn("aeropt-wing.xml", archive.namelist())
            self.assertIn("aeropt-wing.obj", archive.namelist())

    def test_rejects_supersonic_or_transonic_request(self):
        with self.assertRaises(InputError):
            run_design(
                {
                    "flow": {"speed_m_s": 300.0, "speed_max_m_s": 300.0},
                    "solver": {"airfoil_strategy": "internal"},
                }
            )

    def test_hybrid_mode_requires_existing_xfoil_binary(self):
        with self.assertRaises(InputError):
            run_design(
                {
                    "solver": {
                        "airfoil_strategy": "xfoil_closed_loop",
                        "xfoil_path": "/does/not/exist/xfoil",
                    }
                }
            )

    def test_default_request_not_mutated(self):
        self.assertEqual(DEFAULT_REQUEST["solver"]["quality"], "balanced")
        self.assertEqual(DEFAULT_REQUEST["solver"]["parallel_workers"], 16)
        self.assertEqual(DEFAULT_REQUEST["solver"]["airfoil_strategy"], "flow5_native")
        self.assertEqual(DEFAULT_REQUEST["airfoil"]["baseline_profile"], "e818")
        self.assertEqual(DEFAULT_REQUEST["airfoil"]["cst_order"], 6)
        self.assertEqual(DEFAULT_REQUEST["airfoil"]["solver_coordinate_points"], 100)
        self.assertEqual(DEFAULT_REQUEST["solver"]["flow5_threads"], 16)
        self.assertTrue(DEFAULT_REQUEST["solver"]["flow5_surrogate_enabled"])
        self.assertTrue(DEFAULT_REQUEST["solver"]["flow5_checkpoint_enabled"])
        self.assertEqual(DEFAULT_REQUEST["solver"]["flow5_wing_optimizer"], "nsga2")
        self.assertTrue(DEFAULT_REQUEST["solver"]["flow5_budget_escalation_enabled"])
        self.assertEqual(DEFAULT_REQUEST["solver"]["flow5_budget_maximum_multiplier"], 4.0)
        self.assertEqual(
            DEFAULT_REQUEST["solver"]["flow5_budget_convergence_tolerance_percent"],
            3.0,
        )
        self.assertEqual(DEFAULT_REQUEST["solver"]["flow5_multi_seed_runs"], 1)
        self.assertTrue(DEFAULT_REQUEST["validation"]["enabled"])
        self.assertFalse(DEFAULT_REQUEST["structure"]["enabled"])


if __name__ == "__main__":
    unittest.main()
