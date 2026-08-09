from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class StaticUiContractTests(unittest.TestCase):
    def test_javascript_element_references_exist_and_ids_are_unique(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        element_ids = re.findall(r'\bid="([^"]+)"', html)
        referenced = set(re.findall(r'\$\("([A-Za-z][A-Za-z0-9_-]*)"\)', javascript))
        self.assertEqual(len(element_ids), len(set(element_ids)))
        self.assertFalse(referenced.difference(element_ids))

    def test_reliability_controls_and_result_panels_are_present(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        for identifier in (
            "surrogateEnabled",
            "optimizerCheckpoint",
            "multiSeedRuns",
            "validationPanel",
            "diagnosticPanel",
            "paretoPanel",
            "stabilityPanel",
            "historyPanel",
            "budgetEscalation",
            "budgetPanel",
            "flow5WingOptimizer",
        ):
            self.assertIn(f'id="{identifier}"', html)

    def test_foil_and_wing_workflows_are_selectable_and_reusable(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        for identifier in (
            "optimizationMode",
            "wingAirfoilSource",
            "wingAirfoilDatFile",
            "savedAirfoilStatus",
        ):
            self.assertIn(f'id="{identifier}"', html)
        for mode in ("coupled", "foil_only", "wing_only"):
            self.assertIn(f'value="{mode}"', html)
        self.assertIn('workflow: { mode: workflowMode }', javascript)
        self.assertIn('aeropt.savedAirfoil.v1', javascript)
        self.assertIn('result.workflow_mode === "foil_only"', javascript)

    def test_coupled_round_control_explains_cl_and_re_feedback(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        control = re.search(
            r'<input\b[^>]*id="flow5CoupledIterations"[^>]*>', html
        )
        self.assertIsNotNone(control)
        self.assertIn('min="1"', control.group(0))
        self.assertIn('max="8"', control.group(0))
        self.assertIn("kanadın gerçek kesit C<sub>L</sub> dağılımı ve MAC/Re", html)

    def test_design_form_uses_application_validation(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        form = re.search(r'<form\b[^>]*\bid="designForm"[^>]*>', html)
        self.assertIsNotNone(form)
        self.assertIn("novalidate", form.group(0))
        self.assertIn('if (raw === "")', javascript)
        self.assertIn('if (input.min !== "")', javascript)
        self.assertIn('if (input.max !== "")', javascript)

    def test_number_inputs_accept_free_continuous_values_or_plain_integers(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        number_inputs = re.findall(r'<input\b[^>]*type="number"[^>]*>', html)
        target_lift = next(tag for tag in number_inputs if 'id="targetLift"' in tag)
        self.assertIn('step="any"', target_lift)
        for tag in number_inputs:
            step_match = re.search(r'\bstep="([^"]+)"', tag)
            if step_match is None:
                continue
            step = step_match.group(1)
            self.assertIn(step, {"any", "1"}, tag)
            if step == "1":
                minimum = re.search(r'\bmin="([^"]+)"', tag)
                if minimum is not None:
                    self.assertTrue(float(minimum.group(1)).is_integer(), tag)

    def test_packaged_ui_smoke_is_part_of_windows_workflow(self):
        workflow = (ROOT / ".github" / "workflows" / "build-windows-flow5.yml").read_text(
            encoding="utf-8"
        )
        smoke = (ROOT / "ci" / "smoke_packaged_ui.py").read_text(encoding="utf-8")
        self.assertIn("verify-windows-artifact:", workflow)
        self.assertIn("actions/download-artifact@v5", workflow)
        self.assertIn("python ci/smoke_packaged_ui.py", workflow)
        self.assertIn('page.locator("#runButton").click()', smoke)
        self.assertIn('form => form.noValidate', smoke)


if __name__ == "__main__":
    unittest.main()
