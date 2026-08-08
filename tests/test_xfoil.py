import os
import tempfile
import unittest
from pathlib import Path

from aeropt.models import AirfoilDesign
from aeropt.xfoil import run_xfoil_polar


@unittest.skipIf(os.name == "nt", "Shebang tabanlı sahte çözücü yalnız POSIX testidir")
class XfoilAdapterTests(unittest.TestCase):
    def test_subprocess_protocol_and_polar_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "fake-xfoil"
            executable.write_text(
                """#!/usr/bin/env python3
import pathlib
import sys

commands = sys.stdin.read().splitlines()
index = commands.index("PACC")
polar = pathlib.Path(commands[index + 1])
rows = []
for alpha in range(-6, 15):
    cl = 0.105 * (alpha + 2.0)
    cd = 0.009 + 0.006 * cl * cl
    rows.append(f"{alpha:8.3f} {cl:9.5f} {cd:10.6f} {0.85*cd:10.6f} {-0.04:9.5f}")
polar.write_text("\\n".join(rows) + "\\n")
""",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            result = run_xfoil_polar(
                str(executable),
                AirfoilDesign(0.02, 0.4, 0.12, "adapter-test"),
                300_000.0,
                0.03,
                -6.0,
                14.0,
                1.0,
            )
            self.assertEqual(result["converged_points"], 21)
            self.assertAlmostEqual(result["reynolds"], 300_000.0)
            self.assertGreater(result["points"][10]["cd"], 0.0)


if __name__ == "__main__":
    unittest.main()
