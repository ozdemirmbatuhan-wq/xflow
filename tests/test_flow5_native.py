from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import unittest
import zipfile

from aeropt.pipeline import InputError, run_design
from aeropt.flow5_pipeline import _sample_speeds


FAKE_RUNNER = Path(__file__).with_name("fake_flow5_runner.py").resolve()


class Flow5NativePipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        old_value = os.environ.get("AEROPT_ALLOW_TEST_DOUBLE")
        os.environ["AEROPT_ALLOW_TEST_DOUBLE"] = "1"
        try:
            cls.result = run_design(
                {
                    "flow": {
                        "speed_m_s": 18.0,
                        "speed_min_m_s": 14.0,
                        "speed_max_m_s": 22.0,
                        "speed_samples": 3,
                        "target_lift_n": 40.0,
                    },
                    "solver": {
                        "airfoil_strategy": "flow5_native",
                        "flow5_runner_path": str(FAKE_RUNNER),
                        "flow5_threads": 16,
                        "flow5_foil_candidate_budget": 8,
                        "flow5_wing_candidate_budget": 8,
                        "flow5_finalists": 1,
                        "flow5_alpha_step_search_deg": 2.0,
                        "flow5_alpha_step_final_deg": 1.0,
                        "seed": 9,
                    },
                }
            )
        finally:
            if old_value is None:
                os.environ.pop("AEROPT_ALLOW_TEST_DOUBLE", None)
            else:
                os.environ["AEROPT_ALLOW_TEST_DOUBLE"] = old_value

    def test_all_aerodynamic_provenance_is_flow5(self):
        result = self.result
        self.assertTrue(result["flow5_native"])
        self.assertEqual(result["solver_run"]["aerodynamic_score_source"], "flow5 only")
        self.assertIn("flow5", result["polar_source"])
        self.assertEqual(result["flow5_native_analysis"]["foil_solver"], "flow5::XFoilTask")
        self.assertEqual(len(result["foil_polars"]), 3)
        self.assertEqual(len(result["wing_cases"]), 3)
        self.assertGreater(result["wing"]["ld"], 5.0)
        self.assertTrue(result["wing_optimization"]["mesh_convergence"]["passed"])
        self.assertTrue(
            result["wing_optimization"]["solver_telemetry"][
                "spanwise_distribution_available"
            ]
        )
        self.assertIn("coupled_design", result)
        self.assertFalse(result["structural_analysis"]["enabled"])
        json.dumps(result, allow_nan=False)

    def test_real_project_payload_is_required_and_packaged(self):
        exports = self.result["exports"]
        self.assertEqual(
            base64.b64decode(exports["flow5_project_base64"]),
            b"FLOW5_TEST_DOUBLE_PROJECT\x00",
        )
        bundle = base64.b64decode(exports["flow5_bundle_base64"])
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            self.assertIn("aeropt-optimized.fl5", archive.namelist())
            self.assertIn("aeropt-analysis.xml", archive.namelist())
            self.assertIn("aeropt-validation.json", archive.namelist())
            self.assertIn("aeropt-pareto.json", archive.namelist())
            self.assertIn("aeropt-diagnostics.json", archive.namelist())
            self.assertEqual(
                archive.read("aeropt-optimized.fl5"), b"FLOW5_TEST_DOUBLE_PROJECT\x00"
            )

    def test_sixteen_core_budget_avoids_outer_inner_oversubscription(self):
        foil_meta = self.result["airfoil_optimization"]
        self.assertLessEqual(
            foil_meta["outer_parallel_runners"] * foil_meta["threads_per_runner"], 16
        )
        self.assertEqual(self.result["wing_optimization"]["threads_inside_flow5"], 16)
        self.assertTrue(self.result["wing_optimization"]["oversubscription_prevented"])

    def test_validation_pareto_diagnostics_and_resume_metadata_are_exported(self):
        validation = self.result["validation_report"]
        self.assertTrue(validation["enabled"])
        self.assertTrue(validation["passed"])
        self.assertEqual(validation["checks_total"], 9)
        self.assertEqual(len(validation["regression_signature_sha256"]), 64)
        pareto = self.result["pareto_analysis"]
        self.assertGreaterEqual(pareto["candidate_count"], 2)
        self.assertGreaterEqual(pareto["frontier_count"], 1)
        self.assertTrue(all("fidelity" in row for row in pareto["candidates"]))
        self.assertEqual(pareto["provenance"], "optimizer_generated")
        self.assertEqual(self.result["wing_optimization"]["optimizer"], "nsga2")
        self.assertTrue(self.result["wing_optimization"]["multi_objective"]["enabled"])
        self.assertIn(
            self.result["wing_optimization"]["budget_convergence"]["status"],
            {"converged", "budget_exhausted"},
        )
        self.assertIn(
            self.result["diagnostic_report"]["status"],
            {"clear", "warning", "critical"},
        )
        stability = self.result["multi_seed_stability"]
        self.assertEqual(stability["status"], "single_run")
        self.assertFalse(stability["enabled"])
        self.assertIn("checkpoint", self.result["airfoil_optimization"])
        self.assertIn("surrogate", self.result["wing_optimization"])
        exports = self.result["exports"]
        for key in (
            "validation_json",
            "pareto_json",
            "multi_seed_json",
            "diagnostics_json",
        ):
            self.assertIn(key, exports)

    def test_e818_baseline_is_compared_before_the_same_100_point_foil_enters_wing_search(self):
        foil_meta = self.result["airfoil_optimization"]
        self.assertEqual(foil_meta["baseline"]["identifier"], "e818")
        self.assertEqual(foil_meta["baseline"]["cst_order"], 6)
        self.assertTrue(foil_meta["baseline"]["search_converged"])
        self.assertEqual(foil_meta["solver_coordinate_points"], 100)
        self.assertEqual(self.result["wing_optimization"]["foil_coordinate_points"], 100)
        self.assertEqual(foil_meta["selection"]["selected_name"], self.result["airfoil"]["name"])
        self.assertGreaterEqual(foil_meta["selection"]["finalists_evaluated"], 1)
        self.assertEqual(len(self.result["exports"]["airfoil_dat"].splitlines()) - 1, 100)

    def test_missing_native_runner_is_rejected(self):
        with self.assertRaisesRegex(InputError, "runner"):
            run_design({"solver": {"airfoil_strategy": "flow5_native", "flow5_runner_path": ""}})

    def test_speed_mesh_contains_bounds_and_exact_reference(self):
        self.assertEqual(_sample_speeds((13.0, 22.0), 18.0, 4), [13.0, 16.0, 18.0, 22.0])


if __name__ == "__main__":
    unittest.main()
