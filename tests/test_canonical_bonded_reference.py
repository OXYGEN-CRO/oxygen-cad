"""Independent GEOS/scikit-fem mortar and bulk assembly, including curved graphs."""
import json,os
from pathlib import Path
import numpy as np
import pytest
from o3d.simulation_assembly import BondedAssemblySystem,bonded_solve
from o3d.store import write_json
from o3d.solver import run_process
from .test_canonical_bonded import reference
REFERENCE=os.environ.get('O3D_REFERENCE_PYTHON')
pytestmark=pytest.mark.skipif(not REFERENCE,reason='Set O3D_REFERENCE_PYTHON to the pinned independent scikit-fem/Shapely environment')


@pytest.mark.parametrize('curved',[False,True])
def test_bonded_assembly_matches_independent_mortar_and_stiffness(tmp_path,curved):
    _,domain,study,case=reference(refined=False,curved=curved)
    domain.materials[0].poisson_ratio=.3;case.loads[0].vector=(100,30,-20)
    system=BondedAssemblySystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials});system.qualify(tmp_path/'quality')
    own=bonded_solve(system,case,tmp_path/'canonical')
    constraints={n:[True,True,True] for n in system.mesh.nodes if n in {n for t in system.raw_mesh['surfaces']['first:xmin'] for n in t}}
    data={'mesh':system.raw_mesh,'element_bodies':[e.body_id for e in system.mesh.elements],
        'region_bodies':{r.id:r.body_id for r in system.mesh.regions},'materials':{b:m.model_dump(mode='json') for b,m in system.assignments.items()},
        'loads':[l.model_dump(mode='json') for l in case.loads],'node_constraints':constraints,'interface':study.interfaces[0].model_dump(mode='json'),'chart_axes':[1,2]}
    source=tmp_path/'reference-request.json';target=tmp_path/'reference-result.json';write_json(source,data)
    run_process([REFERENCE,Path(__file__).resolve().parents[1]/'scripts/reference_bonded.py',source,target],tmp_path,tmp_path/'reference.log',timeout=180)
    result=json.loads(target.read_text());a=np.array([own['field']['displacements_mm'][n] for n in system.ids]);b=np.array([result['displacements_mm'][str(n)] for n in system.ids])
    error=float(np.max(abs(a-b))/np.max(abs(b)));energy=abs(own['values']['strain_energy_n_mm']/result['strain_energy_n_mm']-1)
    write_json(tmp_path/'comparison.json',{'curved':curved,'relative_displacement_error':error,'relative_energy_error':energy,'independent_residual':result['equation_residual'],'independent_software':result['software']})
    assert error<1e-7
    assert energy<1e-7
    assert result['equation_residual']<1e-8
