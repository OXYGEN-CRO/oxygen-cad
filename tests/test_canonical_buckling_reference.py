"""Independent FE reference stress, geometric assembly, factors and subspaces."""
import json,os
from pathlib import Path
import numpy as np
import pytest
from o3d.simulation_eigen import QuadraticElasticSystem
from o3d.simulation_buckling import buckling_solve
from o3d.simulation_mode_tracking import track_modes
from o3d.solver import run_process
from o3d.store import write_json
from .test_canonical_buckling import reference

REFERENCE=os.environ.get("O3D_REFERENCE_PYTHON")
pytestmark=pytest.mark.skipif(not REFERENCE,reason="Set O3D_REFERENCE_PYTHON to the pinned independent scikit-fem environment")


@pytest.mark.parametrize("curved",[False,True])
def test_quadratic_buckling_matches_independent_FE(tmp_path,curved):
    _,domain,study,case=reference(divisions=(4,2,2),refinements=None)
    mesh=domain.models[0].mesh
    if curved:
        mesh.nodes={n:(x,y+.00002*x*(40-x)*z,z) for n,(x,y,z) in mesh.nodes.items()}
        mesh.source="Synthetic curved buckling reference, not user CAD"
    system=QuadraticElasticSystem(mesh,study,{m.id:m for m in domain.materials});system.qualify(tmp_path/"quality")
    own=buckling_solve(system,case,tmp_path/"canonical");bundle=own.pop("_mode_bundle")
    results=[]
    for order in (4,6):
        source=tmp_path/f"reference-{order}.json";output=tmp_path/f"reference-{order}-result.json"
        write_json(source,{"mode_count":study.settings.mode_count,"static_request":{"mesh":system.raw_mesh,"quadrature_order":order,
            "material":{"youngs_modulus_mpa":200000,"poisson_ratio":.3},
            "study":{"type":"linear_static","fixed_faces":["root"],"forces":[{"vector_n":[-100,0,0]}]},
            "aliases":{"fixed_0":"part:xmin","load_0":"part:xmax"}}})
        run_process([REFERENCE,Path(__file__).resolve().parents[1]/"scripts/reference_buckling.py",source,output],tmp_path,tmp_path/f"reference-{order}.log",timeout=180)
        results.append(json.loads(output.read_text()))
    result=results[-1]
    comparison_bundle={**bundle,"fields":[{int(n):v for n,v in mode.items()} for mode in result["modes"]],"values":result["load_factors"]}
    correlation=track_modes(bundle,comparison_bundle,study.settings.mode_count)
    actual=np.array(bundle["values"]);expected=np.array(result["load_factors"])
    error=float(np.max(abs(actual-expected)/expected))
    change=float(np.max(abs(np.array(results[0]["load_factors"])-expected)/expected))
    reference_fields=json.loads((tmp_path/"canonical/reference-load.json").read_text())
    a=np.array([reference_fields["displacements_mm"][str(n)] for n in system.ids])
    b=np.array([result["reference_static"]["field"]["displacements"][str(n)] for n in system.ids])
    displacement=float(np.max(abs(a-b))/np.max(abs(b)))
    write_json(tmp_path/"comparison.json",{"curved":curved,"maximum_factor_relative_error":error,"independent_quadrature_change":change,
        "reference_displacement_relative_error":displacement,"independent_eigen_residual":result["eigen_residual"],"physical_mode_correlation":correlation})
    assert error<1e-5
    assert change<1e-5
    assert displacement<1e-5
    assert result["eigen_residual"]<1e-7
    assert correlation["passed"],correlation
