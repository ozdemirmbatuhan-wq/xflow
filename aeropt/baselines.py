from __future__ import annotations

from dataclasses import asdict, dataclass

from .airfoil import (
    airfoil_coordinates,
    cst_coordinate_fit_error,
    fit_coordinates_to_cst,
    parse_airfoil_dat,
    resample_parsed_coordinates,
)
from .models import CSTAirfoilDesign


# UIUC Applied Aerodynamics Group coordinate database:
# https://m-selig.ae.illinois.edu/ads/coord/e818.dat
EPPLER_E818_DAT = """EPPLER 818 HYDROFOIL AIRFOIL
       35.       33.

 0.0000100 -.0000300
 0.0001700 0.0006600
 0.0009600 0.0018600
 0.0028000 0.0037000
 0.0070900 0.0068200
 0.0121100 0.0095500
 0.0258700 0.0153900
 0.0443800 0.0214800
 0.0674700 0.0276200
 0.0948500 0.0336700
 0.1262600 0.0394700
 0.1613700 0.0449200
 0.1998300 0.0499000
 0.2412400 0.0543300
 0.2851700 0.0581200
 0.3311700 0.0612200
 0.3787700 0.0635500
 0.4274600 0.0650800
 0.4767600 0.0657500
 0.5261500 0.0655700
 0.5751100 0.0644800
 0.6231300 0.0624900
 0.6697200 0.0595500
 0.7143500 0.0555800
 0.7568300 0.0503300
 0.7972500 0.0440100
 0.8354000 0.0371800
 0.8707400 0.0303400
 0.9027100 0.0237600
 0.9307801 0.0177000
 0.9544200 0.0122300
 0.9733900 0.0073400
 0.9876500 0.0033300
 0.9968000 0.0008100
 1.0000000 0.0000000

 0.0000100 -.0000300
 0.0002000 -.0007100
 0.0010400 -.0018700
 0.0029500 -.0036300
 0.0073500 -.0065500
 0.0124800 -.0090600
 0.0263300 -.0142400
 0.0448000 -.0193900
 0.0676500 -.0242700
 0.0945600 -.0286600
 0.1252300 -.0322800
 0.1594300 -.0347900
 0.1971400 -.0359900
 0.2381800 -.0359900
 0.2822600 -.0348300
 0.3290100 -.0326100
 0.3780600 -.0294400
 0.4289800 -.0254900
 0.4813000 -.0209300
 0.5345100 -.0160200
 0.5880300 -.0109800
 0.6412400 -.0061000
 0.6935100 -.0016300
 0.7440900 0.0021900
 0.7922200 0.0051500
 0.8371300 0.0071000
 0.8780300 0.0079600
 0.9141600 0.0077300
 0.9447800 0.0065100
 0.9690900 0.0044800
 0.9864200 0.0022600
 0.9966400 0.0006000
 1.0000000 0.0000000
"""


@dataclass(frozen=True)
class BaselineProfile:
    foil: CSTAirfoilDesign
    identifier: str
    display_name: str
    raw_name: str
    source: str
    source_url: str | None
    source_point_count: int
    solver_point_count: int
    cst_order: int
    fit_rms_over_c: float
    fit_max_over_c: float
    solver_dat_text: str
    solver_x: tuple[float, ...]
    solver_y: tuple[float, ...]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result.pop("foil", None)
        result.pop("solver_dat_text", None)
        result.pop("solver_x", None)
        result.pop("solver_y", None)
        result["foil"] = self.foil.to_dict()
        return result


def build_baseline_profile(
    identifier: str,
    *,
    custom_dat: str = "",
    cst_order: int = 6,
    solver_point_count: int = 100,
) -> BaselineProfile:
    if cst_order not in {5, 6}:
        raise ValueError("Başlangıç profili için CST derecesi 5 veya 6 olmalı")
    if solver_point_count != 100:
        raise ValueError("flow5/XFoil profil çözüm ağı tam olarak 100 nokta olmalı")
    if identifier == "e818":
        text = EPPLER_E818_DAT
        display_name = "Eppler E818"
        source = "UIUC Airfoil Data Site"
        source_url = "https://m-selig.ae.illinois.edu/ads/coord/e818.dat"
        foil_name = f"Eppler-E818-CST{cst_order}"
    elif identifier == "custom_dat":
        if not custom_dat.strip():
            raise ValueError("Özel başlangıç profili için bir DAT dosyası seçin")
        if len(custom_dat.encode("utf-8")) > 500_000:
            raise ValueError("DAT dosyası 500 kB sınırını aşıyor")
        text = custom_dat
        display_name = "Özel DAT profili"
        source = "Kullanıcı tarafından yüklenen DAT"
        source_url = None
        foil_name = f"Custom-Baseline-CST{cst_order}"
    else:
        raise ValueError("Başlangıç profili 'e818' veya 'custom_dat' olmalı")

    coordinates = parse_airfoil_dat(text, fallback_name=display_name)
    foil = fit_coordinates_to_cst(coordinates, order=cst_order, name=foil_name)
    fit_rms, fit_max = cst_coordinate_fit_error(coordinates, foil)
    if fit_rms > 0.0020 or fit_max > 0.0080:
        raise ValueError(
            f"DAT → CST{cst_order} uyumu yetersiz: RMS={fit_rms:.5f}c, maks={fit_max:.5f}c"
        )
    x, _ = airfoil_coordinates(foil, total_points=solver_point_count)
    if len(x) != solver_point_count:
        raise RuntimeError("Başlangıç profili 100 solver noktasına örneklenemedi")
    solver_x, solver_y = resample_parsed_coordinates(
        coordinates, total_points=solver_point_count
    )
    solver_dat_text = "\n".join(
        [foil.name]
        + [f"{xi:.8f} {yi:.8f}" for xi, yi in zip(solver_x, solver_y)]
    ) + "\n"
    return BaselineProfile(
        foil=foil,
        identifier=identifier,
        display_name=display_name,
        raw_name=coordinates.name,
        source=source,
        source_url=source_url,
        source_point_count=coordinates.source_point_count,
        solver_point_count=solver_point_count,
        cst_order=cst_order,
        fit_rms_over_c=fit_rms,
        fit_max_over_c=fit_max,
        solver_dat_text=solver_dat_text,
        solver_x=tuple(float(value) for value in solver_x),
        solver_y=tuple(float(value) for value in solver_y),
    )


def build_derived_baseline_profile(
    foil: CSTAirfoilDesign,
    solver_dat_text: str,
    *,
    identifier: str,
    display_name: str,
    solver_point_count: int = 100,
) -> BaselineProfile:
    """Promote a selected solver foil to the next coupled-design iteration."""
    if solver_point_count != 100:
        raise ValueError("flow5/XFoil profil çözüm ağı tam olarak 100 nokta olmalı")
    coordinates = parse_airfoil_dat(solver_dat_text, fallback_name=display_name)
    solver_x, solver_y = resample_parsed_coordinates(
        coordinates, total_points=solver_point_count
    )
    fit_rms, fit_max = cst_coordinate_fit_error(coordinates, foil)
    normalized_dat = "\n".join(
        [foil.name]
        + [f"{xi:.8f} {yi:.8f}" for xi, yi in zip(solver_x, solver_y)]
    ) + "\n"
    return BaselineProfile(
        foil=foil,
        identifier=identifier,
        display_name=display_name,
        raw_name=coordinates.name,
        source="Önceki bağlı foil–kanat iterasyonu",
        source_url=None,
        source_point_count=coordinates.source_point_count,
        solver_point_count=solver_point_count,
        cst_order=len(foil.upper_weights) - 1,
        fit_rms_over_c=fit_rms,
        fit_max_over_c=fit_max,
        solver_dat_text=normalized_dat,
        solver_x=tuple(float(value) for value in solver_x),
        solver_y=tuple(float(value) for value in solver_y),
    )
