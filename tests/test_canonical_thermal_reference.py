"""Independent optional scikit-fem assembly, including curved/mixed boundaries."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from o3d.solver import run_process
from o3d.store import write_json
from o3d.simulation_quadratic_thermal import QuadraticThermalSystem, thermal_solve
from o3d.simulation_models import ThermalBoundary

REFERENCE = os.environ.get("O3D_REFERENCE_PYTHON")
pytestmark = pytest.mark.skipif(not REFERENCE, reason="Set O3D_REFERENCE_PYTHON to the pinned independent scikit-fem environment")


def test_canonical_curved_thermal_mixed_boundaries_agree_with_independent_fe(tmp_path):
    from .test_canonical_quadratic_thermal import reference
    _, domain, study, case = reference(refinements=False)
    mesh = domain.models[0].mesh
    # Declared curved numerical geometry; no claim that it represents user CAD.
    mesh.nodes = {n: (x, y+.002*x*(10-x)*z, z) for n, (x,y,z) in mesh.nodes.items()}
    mesh.source = "Explicit curved numerical verification mesh"
    case.thermal = [case.thermal[0],
        ThermalBoundary(id="power", kind="heat_power", region_id="part:xmax", value=.02, source="Declared 0.02 W source"),
        ThermalBoundary(id="body", kind="body_heat", body_id="part", value=.01, source="Declared 0.01 W volume source"),
        ThermalBoundary(id="film", kind="convection", region_id="part:ymax", value=1000, ambient_k=300, source="Declared film"),
        ThermalBoundary(id="radiation", kind="radiation", region_id="part:zmax", value=.8, ambient_k=300, source="Declared emissivity/ambient")]
    system = QuadraticThermalSystem(mesh, study, {m.id:m for m in domain.materials})
    system.qualify(tmp_path/"quality")
    own = thermal_solve(system, case, tmp_path/"canonical")
    results = []
    for order in (4, 6):
        # Only neutral format/unit translation is shared. scikit-fem supplies
        # geometry mapping, shape functions, gradients, forms and quadrature.
        source = tmp_path/f"reference-{order}.json"
        output = tmp_path/f"reference-{order}-result.json"
        write_json(source, {"mesh": system.raw_mesh, "quadrature_order":order,
            "material":{"thermal_conductivity_w_m_k":20},
            "study":{"type":"thermal", "fixed_faces":[], "forces":[],
                "temperatures":[{"temperature_c":26.85}], "heat_sources":[{"power_w":.02}], "internal_heat_w":.01,
                "convection":[{"coefficient_w_m2_k":1000, "ambient_temperature_c":26.85}],
                "radiation":[{"emissivity":.8, "ambient_temperature_c":26.85}]},
            "aliases":{"temperature_0":"part:xmin", "heat_0":"part:xmax", "convection_0":"part:ymax", "radiation_0":"part:zmax"}})
        run_process([REFERENCE, Path(__file__).resolve().parents[1]/"scripts/reference_fem.py", source, output],
                    tmp_path, tmp_path/f"reference-{order}.log", timeout=180)
        results.append(json.loads(output.read_text()))
    nodes = sorted(own["field"]["temperature_k"])
    actual = np.array([own["field"]["temperature_k"][n] for n in nodes])
    independent = [np.array([r["field"]["temperature_c"][str(n)]+273.15 for n in nodes]) for r in results]
    rise = float(np.max(abs(actual-300)))
    comparison = {"independent_orders_relative_change":float(np.max(abs(independent[1]-independent[0]))/rise),
                  "canonical_relative_error":float(np.max(abs(actual-independent[1]))/rise),
                  "temperature_excursion_k":rise, "canonical_heat_balance":own["checks"]["heat_balance_error"],
                  "independent_heat_balance":results[-1]["field"]["heat_balance_error"],
                  "reference_software":results[-1]["software"]}
    write_json(tmp_path/"comparison.json", comparison)
    assert comparison["independent_orders_relative_change"] < 1e-5
    assert comparison["canonical_relative_error"] < 1e-5
    assert comparison["canonical_heat_balance"] < 1e-7
    assert comparison["independent_heat_balance"] < 1e-7
