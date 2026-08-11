from __future__ import annotations

from dataclasses import asdict, dataclass
from math import cos, radians, sin
from typing import Any, TypeAlias


@dataclass(frozen=True)
class Fluid:
    name: str
    density: float
    dynamic_viscosity: float
    speed_of_sound: float

    @property
    def kinematic_viscosity(self) -> float:
        return self.dynamic_viscosity / self.density

    def dynamic_pressure(self, speed: float) -> float:
        return 0.5 * self.density * speed**2

    def reynolds(self, speed: float, chord: float) -> float:
        return self.density * speed * chord / self.dynamic_viscosity

    def mach(self, speed: float) -> float:
        return speed / self.speed_of_sound

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AirfoilDesign:
    max_camber: float
    camber_position: float
    thickness: float
    name: str = "AeroOpt"

    @property
    def family(self) -> str:
        return "NACA4"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "family": self.family}


@dataclass(frozen=True)
class CSTAirfoilDesign:
    """Kulfan/CST airfoil defined by independent upper and lower surfaces."""

    upper_weights: tuple[float, ...]
    lower_weights: tuple[float, ...]
    max_camber: float
    camber_position: float
    thickness: float
    name: str = "AeroOpt-CST"
    trailing_edge_gap: float = 0.0

    def __post_init__(self) -> None:
        if len(self.upper_weights) != len(self.lower_weights):
            raise ValueError("CST üst ve alt yüzey ağırlık sayıları eşit olmalı")
        if len(self.upper_weights) < 3:
            raise ValueError("CST yüzeyinde en az üç ağırlık olmalı")

    @property
    def family(self) -> str:
        return f"CST{len(self.upper_weights) - 1}"

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["upper_weights"] = list(self.upper_weights)
        result["lower_weights"] = list(self.lower_weights)
        result["family"] = self.family
        return result


AirfoilLike: TypeAlias = AirfoilDesign | CSTAirfoilDesign


@dataclass(frozen=True)
class WingGeometry:
    span: float
    root_chord: float
    taper: float
    sweep_deg: float
    tip_twist_deg: float
    alpha_deg: float
    mid_chord_factor: float = 1.0
    mid_twist_deg: float | None = None
    winglet_enabled: bool = False
    winglet_height: float = 0.0
    winglet_cant_deg: float = 90.0
    winglet_toe_deg: float = 0.0
    winglet_taper: float = 1.0

    @property
    def mid_span_fraction(self) -> float:
        return 0.5

    @property
    def tip_chord(self) -> float:
        return self.root_chord * self.taper

    @property
    def winglet_active(self) -> bool:
        return bool(self.winglet_enabled and self.winglet_height > 1.0e-9)

    @property
    def winglet_developed_length(self) -> float:
        """Length of one winglet measured along its canted span axis."""
        if not self.winglet_active:
            return 0.0
        return self.winglet_height / max(sin(radians(self.winglet_cant_deg)), 1.0e-9)

    @property
    def winglet_horizontal_projection(self) -> float:
        if not self.winglet_active:
            return 0.0
        return self.winglet_developed_length * cos(radians(self.winglet_cant_deg))

    @property
    def raw_main_semispan(self) -> float:
        """Planar semispan before the winglet's horizontal projection begins."""
        return 0.5 * self.span - self.winglet_horizontal_projection

    @property
    def main_semispan(self) -> float:
        return max(self.raw_main_semispan, 1.0e-9)

    @property
    def main_span(self) -> float:
        return 2.0 * self.main_semispan

    @property
    def winglet_geometry_valid(self) -> bool:
        if not self.winglet_active:
            return True
        return bool(
            0.0 < self.winglet_height
            and 5.0 <= self.winglet_cant_deg <= 90.0
            and 0.05 <= self.winglet_taper <= 1.5
            and self.raw_main_semispan > max(0.02 * self.span, 1.0e-5)
        )

    @property
    def winglet_root_chord(self) -> float:
        return self.tip_chord

    @property
    def winglet_tip_chord(self) -> float:
        return self.winglet_root_chord * self.winglet_taper

    @property
    def winglet_tip_twist_deg(self) -> float:
        return self.tip_twist_deg + self.winglet_toe_deg

    @property
    def linear_mid_chord(self) -> float:
        return 0.5 * (self.root_chord + self.tip_chord)

    @property
    def mid_chord(self) -> float:
        return self.linear_mid_chord * self.mid_chord_factor

    @property
    def effective_mid_twist_deg(self) -> float:
        return (
            0.5 * self.tip_twist_deg
            if self.mid_twist_deg is None
            else float(self.mid_twist_deg)
        )

    def chord_at(self, span_fraction: float) -> float:
        eta = min(max(abs(float(span_fraction)), 0.0), 1.0)
        if eta <= self.mid_span_fraction:
            local = eta / self.mid_span_fraction
            return self.root_chord + local * (self.mid_chord - self.root_chord)
        local = (eta - self.mid_span_fraction) / (1.0 - self.mid_span_fraction)
        return self.mid_chord + local * (self.tip_chord - self.mid_chord)

    def twist_at(self, span_fraction: float) -> float:
        eta = min(max(abs(float(span_fraction)), 0.0), 1.0)
        mid_twist = self.effective_mid_twist_deg
        if eta <= self.mid_span_fraction:
            return eta / self.mid_span_fraction * mid_twist
        local = (eta - self.mid_span_fraction) / (1.0 - self.mid_span_fraction)
        return mid_twist + local * (self.tip_twist_deg - mid_twist)

    def le_offset_at(self, span_fraction: float) -> float:
        from math import tan

        eta = min(max(abs(float(span_fraction)), 0.0), 1.0)
        half_span = self.main_semispan
        quarter_chord_x = eta * half_span * tan(radians(self.sweep_deg))
        return 0.25 * self.root_chord + quarter_chord_x - 0.25 * self.chord_at(eta)

    @property
    def main_area(self) -> float:
        half_span = self.main_semispan
        first = 0.5 * half_span * self.mid_span_fraction * (
            self.root_chord + self.mid_chord
        )
        second = 0.5 * half_span * (1.0 - self.mid_span_fraction) * (
            self.mid_chord + self.tip_chord
        )
        return 2.0 * (first + second)

    @property
    def winglet_projected_area(self) -> float:
        if not self.winglet_active:
            return 0.0
        return self.winglet_horizontal_projection * (
            self.winglet_root_chord + self.winglet_tip_chord
        )

    @property
    def winglet_surface_area(self) -> float:
        if not self.winglet_active:
            return 0.0
        return self.winglet_developed_length * (
            self.winglet_root_chord + self.winglet_tip_chord
        )

    @property
    def area(self) -> float:
        """flow5 PROJECTED reference area, including the winglet projection."""
        return self.main_area + self.winglet_projected_area

    @property
    def developed_area(self) -> float:
        """Actual lifting-surface area used to expose winglet wetted-area growth."""
        return self.main_area + self.winglet_surface_area

    @property
    def aspect_ratio(self) -> float:
        return self.span**2 / self.area

    @property
    def mean_aerodynamic_chord(self) -> float:
        half_span = self.main_semispan

        def integral_c2(length: float, c0: float, c1: float) -> float:
            return length * (c0 * c0 + c0 * c1 + c1 * c1) / 3.0

        first_length = half_span * self.mid_span_fraction
        second_length = half_span * (1.0 - self.mid_span_fraction)
        half_integral = integral_c2(
            first_length, self.root_chord, self.mid_chord
        ) + integral_c2(second_length, self.mid_chord, self.tip_chord)
        if self.winglet_active and self.winglet_horizontal_projection > 0.0:
            half_integral += integral_c2(
                self.winglet_horizontal_projection,
                self.winglet_root_chord,
                self.winglet_tip_chord,
            )
        return 2.0 * half_integral / self.area

    @property
    def mid_le_offset(self) -> float:
        return self.le_offset_at(self.mid_span_fraction)

    @property
    def tip_le_offset(self) -> float:
        """Main-wing/winglet-junction LE offset for quarter-chord sweep."""
        return self.le_offset_at(1.0)

    @property
    def winglet_tip_le_offset(self) -> float:
        """Winglet tip LE offset with zero winglet quarter-chord sweep in v1."""
        return self.tip_le_offset + 0.25 * (
            self.winglet_root_chord - self.winglet_tip_chord
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update(
            tip_chord=self.tip_chord,
            mid_span_fraction=self.mid_span_fraction,
            mid_chord=self.mid_chord,
            effective_mid_twist_deg=self.effective_mid_twist_deg,
            mid_le_offset=self.mid_le_offset,
            area=self.area,
            aspect_ratio=self.aspect_ratio,
            mean_aerodynamic_chord=self.mean_aerodynamic_chord,
            tip_le_offset=self.tip_le_offset,
            winglet_active=self.winglet_active,
            winglet_geometry_valid=self.winglet_geometry_valid,
            main_span=self.main_span,
            main_semispan=self.main_semispan,
            winglet_developed_length=self.winglet_developed_length,
            winglet_horizontal_projection=self.winglet_horizontal_projection,
            winglet_root_chord=self.winglet_root_chord,
            winglet_tip_chord=self.winglet_tip_chord,
            winglet_tip_twist_deg=self.winglet_tip_twist_deg,
            winglet_tip_le_offset=self.winglet_tip_le_offset,
            main_area=self.main_area,
            winglet_projected_area=self.winglet_projected_area,
            winglet_surface_area=self.winglet_surface_area,
            developed_area=self.developed_area,
        )
        return result


FLUID_PRESETS: dict[str, Fluid] = {
    "air": Fluid("Hava (15 °C)", 1.225, 1.7894e-5, 340.3),
    "fresh_water": Fluid("Tatlı su (15 °C)", 999.1, 1.138e-3, 1481.0),
    "sea_water": Fluid("Deniz suyu (15 °C)", 1025.0, 1.188e-3, 1500.0),
}
