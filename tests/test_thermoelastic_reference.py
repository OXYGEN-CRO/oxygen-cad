"""Independent full thermal-to-structural chain, not a shared assembly oracle."""
import json
import os
from pathlib import Path
import numpy as np
import pytest

from o3d.model import geometry_key
from o3d.simulation_mesh import block_mesh
from o3d.simulation_models import Support, ThermalBoundary
from o3d.simulation_quadratic import QuadraticSystem
from o3d.solver import run_process
from o3d.store import write_json
from o3d.simulation_worker import solve
from .test_canonical_thermoelastic import reference, request

REFERENCE=os.environ.get("O3D_REFERENCE_PYTHON")
pytestmark=pytest.mark.skipif(not REFERENCE,reason="Set O3D_REFERENCE_PYTHON to the pinned independent scikit-fem environment")


def test_nonuniform_thermal_stress_agrees_with_independent_sequential_FE(tmp_path):
    project,domain,study,case=reference(refinements=False)
    case.supports=[Support(id="clamp",region_id="part:xmin")]
    case.thermal[0].value=300
    case.thermal.append(ThermalBoundary(id="body",kind="body_heat",body_id="part",value=.08,source="Declared .08 W total internal heat"))
    comparisons=[]
    for level,divisions in enumerate([(4,2,2),(8,3,3)]):
        mesh=block_mesh(geometry_key(project),divisions=divisions,element_family="C3D10")
        domain.models[0].mesh=mesh
        system=QuadraticSystem(mesh,study,{m.id:m for m in domain.materials})
        own=solve(request(project,domain,study),tmp_path/f"canonical-{level}")
        constraints={system.ids[i]:(True,True,True) for i in system.region_nodes("part:xmin")}
        reference_fields=[]
        for order in (4,6):
            source=tmp_path/f"reference-{level}-{order}.json";output=tmp_path/f"reference-{level}-{order}-result.json"
            write_json(source,{"thermal_request":{"mesh":system.raw_mesh,"quadrature_order":6,
                "material":{"thermal_conductivity_w_m_k":20},
                "study":{"type":"thermal","fixed_faces":[],"forces":[],"temperatures":[{"temperature_c":26.85}],"heat_sources":[],"internal_heat_w":.08},
                "aliases":{"temperature_0":"part:xmin"}},
                "material":{"youngs_modulus_mpa":200000,"poisson_ratio":.3,"expansion_per_k":12e-6},
                "stress_free_temperature_k":300,"node_constraints":constraints,"mechanical_quadrature_order":order})
            run_process([REFERENCE,Path(__file__).resolve().parents[1]/"scripts/reference_thermoelastic.py",source,output],
                        tmp_path,tmp_path/f"reference-{level}-{order}.log",timeout=180)
            reference_fields.append(json.loads(output.read_text()))
        nodes=sorted(own["field"]["nodes"])
        actual=np.array([own["field"]["displacements_mm"][n] for n in nodes])
        independent=[np.array([r["displacements_mm"][str(n)] for n in nodes]) for r in reference_fields]
        scale=float(np.linalg.norm(independent[-1],axis=1).max())
        error=float(np.linalg.norm(actual-independent[-1],axis=1).max()/scale)
        quadrature=float(np.linalg.norm(independent[-1]-independent[0],axis=1).max()/scale)
        temperature_error=max(abs(own["field"]["temperature_k"][n]-reference_fields[-1]["temperature_k"][str(n)]) for n in nodes)
        comparisons.append({"level":level,"nodes":len(nodes),"elements":len(mesh.elements),"relative_displacement_error":error,
            "independent_quadrature_change":quadrature,"max_temperature_error_k":temperature_error,"reference_equation_residual":reference_fields[-1]["equation_residual"]})
        assert quadrature<1e-5
        assert temperature_error<1e-8
    write_json(tmp_path/"comparison.json",comparisons)
    # CalculiX's standard C3D10 uses four integration points; independent higher
    # quadrature exposes this difference for nonuniform quadratic eigenstrain.
    # Require a tighter-than-default cross-solver error and actual reduction.
    assert comparisons[-1]["relative_displacement_error"]<.005
    assert comparisons[-1]["relative_displacement_error"]<comparisons[0]["relative_displacement_error"]*.75
