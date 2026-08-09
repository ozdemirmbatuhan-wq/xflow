from __future__ import annotations

import argparse
import json
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


TARGET_LIFT_N = 140_000.0
REFERENCE_SPEED_M_S = 12.5


def request_json(
    url: str, *, method: str = "GET", payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def acceptance_request() -> dict[str, Any]:
    return {
        "workflow": {"mode": "coupled"},
        "flow": {
            "fluid": "sea_water",
            "density_kg_m3": 1025.0,
            "dynamic_viscosity_pa_s": 1.188e-3,
            "speed_of_sound_m_s": 1500.0,
            "speed_m_s": REFERENCE_SPEED_M_S,
            "speed_min_m_s": REFERENCE_SPEED_M_S,
            "speed_max_m_s": REFERENCE_SPEED_M_S,
            "speed_samples": 5,
            "target_lift_n": TARGET_LIFT_N,
        },
        "airfoil": {"design_cl": 1.0},
        "wing": {
            "span_min_m": 4.3,
            "span_max_m": 4.4,
            "root_chord_min_m": 0.5,
            "root_chord_max_m": 2.0,
        },
        "solver": {
            "airfoil_strategy": "flow5_native",
            "flow5_runner_path": "",
            "flow5_threads": 16,
            "flow5_foil_candidate_budget": 8,
            "flow5_wing_candidate_budget": 8,
            "flow5_finalists": 1,
            "flow5_coupled_iterations": 2,
            "flow5_budget_escalation_enabled": False,
            "flow5_surrogate_enabled": False,
            "flow5_mesh_convergence_enabled": False,
            "flow5_checkpoint_enabled": False,
            "flow5_multi_seed_runs": 1,
            "seed": 140000,
        },
        "structure": {"enabled": False},
        "hydro": {"enabled": False},
    }


def validate_result(result: dict[str, Any]) -> dict[str, Any]:
    speeds = result["flow"]["sampled_speeds_m_s"]
    if speeds != [REFERENCE_SPEED_M_S]:
        raise AssertionError(f"Expected one 12.5 m/s condition, got {speeds!r}")

    wing = result["wing"]
    geometry = wing["geometry"]
    lift_n = float(wing["lift_n"])
    lift_error_percent = 100.0 * abs(lift_n - TARGET_LIFT_N) / TARGET_LIFT_N
    if lift_error_percent > 1.0:
        raise AssertionError(
            f"Reference lift closure is {lift_error_percent:.3f}%: {lift_n:.3f} N"
        )
    if not 4.3 <= float(geometry["span"]) <= 4.4:
        raise AssertionError(f"Span is outside 4.3-4.4 m: {geometry['span']}")
    if not 0.5 <= float(geometry["root_chord"]) <= 2.0:
        raise AssertionError(
            f"Root chord is outside 0.5-2.0 m: {geometry['root_chord']}"
        )

    coupled = result["coupled_design"]
    history = coupled["history"]
    if len(history) != 2 or int(coupled["selected_iteration"]) != 2:
        raise AssertionError(
            "Expected two foil-wing rounds with the final pair selected: "
            f"{coupled!r}"
        )

    return {
        "status": result["status"],
        "target_lift_n": TARGET_LIFT_N,
        "resolved_lift_n": lift_n,
        "lift_error_percent": lift_error_percent,
        "speed_m_s": REFERENCE_SPEED_M_S,
        "fluid": result["flow"]["name"],
        "sampled_speeds_m_s": speeds,
        "initial_profile_cl": coupled["initial_design_cl_at_reference"],
        "final_profile_cl": coupled["final_design_cl_at_reference"],
        "coupled_rounds": coupled["iterations_completed"],
        "coupled_converged": coupled["converged"],
        "airfoil": result["airfoil"]["name"],
        "wing_cl": wing["cl"],
        "wing_cd": wing["cd_total"],
        "wing_ld": wing["ld"],
        "span_m": geometry["span"],
        "root_chord_m": geometry["root_chord"],
        "taper": geometry["taper"],
        "sweep_deg": geometry["sweep_deg"],
        "mean_aerodynamic_chord_m": geometry["mean_aerodynamic_chord"],
        "reference_reynolds": result["airfoil_optimization"]["reynolds"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the requested 140 kN single-speed design on packaged AeroOpt."
    )
    parser.add_argument("base_url")
    parser.add_argument("--timeout-seconds", type=float, default=1200.0)
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    job = request_json(
        f"{base_url}/api/jobs", method="POST", payload=acceptance_request()
    )
    job_id = str(job["id"])
    deadline = time.monotonic() + args.timeout_seconds
    last_message = ""
    while time.monotonic() < deadline:
        state = request_json(f"{base_url}/api/jobs/{job_id}")
        progress = state.get("progress") or {}
        message = str(progress.get("message") or "")
        if message and message != last_message:
            print(
                f"[{float(progress.get('percent', 0.0)):6.2f}%] {message}",
                flush=True,
            )
            last_message = message
        if state.get("status") == "completed":
            summary = validate_result(state["result"])
            print("PACKAGED_ACCEPTANCE_RESULT=" + json.dumps(summary, sort_keys=True))
            return
        if state.get("status") in {"failed", "cancelled"}:
            raise RuntimeError(
                "Packaged 140 kN acceptance optimization failed: "
                + json.dumps(state.get("error") or state, ensure_ascii=False)
            )
        time.sleep(2.0)

    try:
        request_json(f"{base_url}/api/jobs/{job_id}/cancel", method="POST", payload={})
    finally:
        raise TimeoutError(
            f"Packaged 140 kN acceptance optimization exceeded {args.timeout_seconds:g} s"
        )


if __name__ == "__main__":
    main()
