# Third-party components in the Windows build

The GitHub Actions Windows artifact bundles the AeroOpt flow5 process bridge and the runtime
libraries needed to execute it. AeroOpt's Python application remains MIT-licensed. The bridge
is GPL-3.0-or-later because it links to flow5 and Gmsh.

- flow5 7.57, exact source commit `a9e852c559590188e00e9efe997c35c1dec7209b`, GPL-3.0-or-later: https://github.com/techwinder/flow5
- Gmsh 4.14.1 SDK, GPL-2.0-or-later: https://gmsh.info
- Open CASCADE Technology 7.9.2, LGPL-2.1 with exception: https://github.com/Open-Cascade-SAS/OCCT
- Qt 6.9.1 runtime, LGPL-3.0/GPL licensing options: https://www.qt.io/licensing
- OpenBLAS, BSD-3-Clause: https://github.com/OpenMathLib/OpenBLAS
- NumPy, SciPy and their dependencies retain their respective licenses inside the PyInstaller distribution.

The workflow downloads/builds these dependencies from their upstream distributions. Review all
upstream license files and distribution obligations before redistributing a generated binary.
