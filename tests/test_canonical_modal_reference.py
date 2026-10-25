"""Independent scikit-fem stiffness/mass/eigenvectors, same full C3D10 inputs."""
import json
import os
from pathlib import Path
import numpy as np
import pytest

from o3d.simulation_eigen import QuadraticEigenSystem,modal_solve
from o3d.simulation_mode_tracking import track_modes
from o3d.solver import run_process
from o3d.store import write_json
from .test_canonical_modal import reference

REFERENCE=os.environ.get("O3D_REFERENCE_PYTHON")
pytestmark=pytest.mark.skipif(not REFERENCE,reason="Set O3D_REFERENCE_PYTHON to the pinned independent scikit-fem environment")


@pytest.mark.parametrize("curved",[False,True])
def test_consistent_canonical_modes_agree_with_independent_FE(tmp_path,curved):
    _,domain,study,case=reference(divisions=(4,2,2),refinements=None,count=2)
    mesh=domain.models[0].mesh
    if curved:
        mesh.nodes={n:(x,y+.002*x*(10-x)*z,z) for n,(x,y,z) in mesh.nodes.items()}
        mesh.source="Explicit curved numerical modal reference, no user CAD"
    system=QuadraticEigenSystem(mesh,study,{m.id:m for m in domain.materials})
    system.qualify(tmp_path/"quality")
    own=modal_solve(system,case,tmp_path/"canonical")
    bundle=own.pop("_mode_bundle")
    results=[]
    for order in (4,6):
        source=tmp_path/f"reference-{order}.json";output=tmp_path/f"reference-{order}-result.json"
        write_json(source,{"mesh":system.raw_mesh,"quadrature_order":order,
            "material":{"youngs_modulus_mpa":200000,"poisson_ratio":.3,"density_g_cm3":7.8},
            "study":{"type":"modal","fixed_faces":["root"],"forces":[],"mode_count":2},"aliases":{"fixed_0":"part:xmin"}})
        run_process([REFERENCE,Path(__file__).resolve().parents[1]/"scripts/reference_fem.py",source,output],tmp_path,tmp_path/f"reference-{order}.log",timeout=180)
        results.append(json.loads(output.read_text()))
    independent=results[-1]["field"]
    reference_bundle={**bundle,"fields":[{int(n):v for n,v in mode.items()} for mode in independent["modes"]],"values":independent["frequencies_hz"]}
    correlation=track_modes(bundle,reference_bundle,2)
    actual=np.array(bundle["values"]);expected=np.array(independent["frequencies_hz"])
    error=float(np.max(abs(actual-expected)/expected))
    low=np.array(results[0]["field"]["frequencies_hz"])
    quadrature=float(np.max(abs(low-expected)/expected))
    comparison={"curved":curved,"relative_frequency_error":error,"independent_quadrature_change":quadrature,
        "mass_orthogonality_error":own["checks"]["mass_orthogonality_error"],"equation_residual":own["checks"]["residual"],
        "mode_tracking":correlation,"reference_software":results[-1]["software"]}
    write_json(tmp_path/"comparison.json",comparison)
    assert error<1e-5
    assert quadrature<1e-5
    assert correlation["passed"],correlation
