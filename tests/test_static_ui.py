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


if __name__ == "__main__":
    unittest.main()
