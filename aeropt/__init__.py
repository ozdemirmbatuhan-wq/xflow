"""AeroOpt: flow5-native airfoil and finite-wing preliminary design tools."""

from .models import AirfoilDesign, CSTAirfoilDesign, Fluid, WingGeometry
from .pipeline import run_design

__all__ = ["AirfoilDesign", "CSTAirfoilDesign", "Fluid", "WingGeometry", "run_design"]
__version__ = "0.8.0"
