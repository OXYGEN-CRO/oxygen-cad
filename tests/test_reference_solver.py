"""Optional independent scikit-fem checks: no application FEM routines in worker."""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from o3d.analysis import distribute_scalar
from o3d.model import Project,Material,TemperatureBoundary,HeatSource
from o3d.solver import simulate,load_boundary,run_process
from o3d.store import Store

ROOT=Path(__file__).resolve().parents[1]
REFERENCE=os.environ.get('O3D_REFERENCE_PYTHON')
pytestmark=pytest.mark.skipif(not REFERENCE,reason='Set O3D_REFERENCE_PYTHON to a pinned scikit-fem verification environment')


@pytest.mark.parametrize('case',['cantilever-netgen','tube-netgen','plate-hole-gmsh','thermal-tube-gmsh'])
def test_exact_mesh_against_independent_finite_element_assembly(tmp_path,beam,case,mesh_families):
    name='tube' if 'tube' in case else 'plate-hole' if 'plate-hole' in case else 'cantilever'
    p=mesh_families[name]
    p.material=beam.material.model_copy(deep=True)
    p.study.mesher='gmsh' if case.endswith('gmsh') else 'netgen'
    p.study.assumptions_confirmed=True
    p.study.stress_probe_mm=None
    p.study.fixed_faces=['part:xmin']
    p.study.forces=beam.study.forces
    p.requirements=[]
    thermal=case.startswith('thermal')
    if thermal:
        p.study.type='thermal';p.study.fixed_faces=[];p.study.forces=[]
        p.material.thermal_conductivity_w_m_k=20
        p.study.temperatures=[TemperatureBoundary(face='part:xmin',temperature_c=0)]
        p.study.heat_sources=[HeatSource(face='part:xmax',power_w=1)]
    store=Store(tmp_path/case);store.init(p)
    calculated=simulate(store,p)
    mesh=json.loads(store.artifact(calculated['artifacts']['mesh_0/mesh.json']).read_text())
    fields=json.loads(store.artifact(calculated['artifacts']['mesh_0/normalized-field.json']).read_text())
    transfer=json.loads(store.artifact(calculated['artifacts']['region_map']).read_text())
    for alias,canonical in transfer['aliases'].items():mesh['surfaces'][alias]=mesh['surfaces'][canonical]
    directory=tmp_path/'reference';directory.mkdir()
    request={'mesh':mesh,'study':p.study.model_dump(mode='json'),'material':p.material.model_dump(mode='json'),'aliases':transfer['aliases']}
    results=[]
    for order in (2,6):
        request['quadrature_order']=order
        request_file=directory/f'request-{order}.json';output=directory/f'reference-{order}.json'
        request_file.write_text(json.dumps(request))
        run_process([REFERENCE,ROOT/'scripts/reference_fem.py',request_file,output],directory,directory/f'reference-{order}.log',timeout=180)
        results.append(json.loads(output.read_text()))
    first,higher=results
    assert first['cell_quadrature_points']==4
    metrics={}
    if not thermal:
        ids=sorted(map(int,mesh['nodes']))
        u=np.array([fields['displacements'][str(n)] for n in ids])
        reference=np.array([first['field']['displacements'][str(n)] for n in ids])
        high=np.array([higher['field']['displacements'][str(n)] for n in ids])
        scale=max(float(np.linalg.norm(u,axis=1).max()),1e-12)
        metrics['displacement_relative_difference']=float(np.linalg.norm(reference-u,axis=1).max()/scale)
        metrics['quadrature_sensitivity']=float(np.linalg.norm(high-reference,axis=1).max()/scale)
        _,forces=load_boundary(mesh,p.study)
        reference_loads=first['field']['equivalent_loads_n']
        load_scale=max(np.linalg.norm(list(forces.values()),axis=1).max(),1e-12)
        metrics['nodal_load_relative_difference']=max(float(np.linalg.norm(np.asarray(reference_loads[str(n)])-forces.get(n,np.zeros(3)))) for n in ids)/load_scale
        energy=.5*sum(float(np.dot(f,fields['displacements'][str(n)])) for n,f in forces.items())
        metrics['energy_relative_difference']=abs(first['field']['strain_energy_n_mm']-energy)/max(abs(energy),1e-12)
        metrics['force_balance_error']=first['field']['force_balance_error']
        metrics['moment_balance_error']=first['field']['moment_balance_error']
    else:
        ids=sorted(map(int,mesh['nodes']))
        temperature=np.array([fields['temperature_c'][str(n)] for n in ids])
        reference=np.array([first['field']['temperature_c'][str(n)] for n in ids])
        high=np.array([higher['field']['temperature_c'][str(n)] for n in ids])
        scale=max(float(np.ptp(temperature)),1e-12)
        metrics['temperature_relative_difference']=float(np.max(np.abs(reference-temperature))/scale)
        metrics['quadrature_sensitivity']=float(np.max(np.abs(high-reference))/scale)
        flux=distribute_scalar(mesh,'heat_0',1000)
        reference_flux=first['field']['equivalent_heat_load_n_mm_s']
        flux_scale=max(map(abs,flux.values()))
        metrics['nodal_heat_load_relative_difference']=max(abs(reference_flux[str(n)]-flux.get(n,0)) for n in ids)/flux_scale
        metrics['heat_balance_error']=first['field']['heat_balance_error']
    report={'case':case,'calculated_study_state':calculated['state'],'mesh_level':0,'nodes':len(ids),'elements':len(mesh['elements']),
            'metrics':metrics,'reference_software':first['software'],'calculix':calculated['solver_identity'],
            'scope':'Same qualified quadratic mesh, source properties and boundary idealization; independent assembly, facet integration and sparse solve; no physical validation'}
    write_to=Path(os.environ.get('O3D_REFERENCE_REPORT_DIR',str(directory)));write_to.mkdir(parents=True,exist_ok=True)
    (write_to/f'{case}.json').write_text(json.dumps(report,indent=2)+'\n')
    assert first['equation_residual']<1e-7
    assert all(value<2e-3 for metric,value in metrics.items() if metric!='quadrature_sensitivity'),json.dumps(report,indent=2)
    assert metrics['quadrature_sensitivity']<.02,json.dumps(report,indent=2)
    assert calculated['physical_validation']=='not_performed'


@pytest.mark.parametrize('mesher,square',[('netgen',False),('gmsh',True)])
def test_modal_fields_against_independent_stiffness_mass_and_eigensolve(tmp_path,beam,mesher,square):
    p=beam.model_copy(deep=True)
    if square:p.features[0].width=4.0
    p.study.type='modal';p.study.forces=[];p.study.stress_probe_mm=None
    p.study.mode_count=2;p.study.mesher=mesher;p.study.mesh_size_mm=4
    p.requirements=[]
    store=Store(tmp_path/'modal');store.init(p)
    calculated=simulate(store,p)
    assert calculated['state']=='passed'
    mesh=json.loads(store.artifact(calculated['artifacts']['mesh_0/mesh.json']).read_text())
    fields=json.loads(store.artifact(calculated['artifacts']['mesh_0/normalized-field.json']).read_text())
    transfer=json.loads(store.artifact(calculated['artifacts']['region_map']).read_text())
    request={'mesh':mesh,'study':p.study.model_dump(mode='json'),'material':p.material.model_dump(mode='json'),'aliases':transfer['aliases']}
    results=[]
    directory=tmp_path/'modal-reference';directory.mkdir()
    for order in (2,6):
        request['quadrature_order']=order
        source=directory/f'request-{order}.json';source.write_text(json.dumps(request))
        output=directory/f'result-{order}.json'
        run_process([REFERENCE,ROOT/'scripts/reference_fem.py',source,output],directory,directory/f'order-{order}.log',timeout=180)
        results.append(json.loads(output.read_text()))
    first,higher=results
    expected=np.array([v['value'] for v in fields['modes']])
    found=np.array(first['field']['frequencies_hz'][:2]);high=np.array(higher['field']['frequencies_hz'][:2])
    ids=sorted(map(int,mesh['nodes']))
    a=np.column_stack([np.asarray([mode['vectors'][str(n)] for n in ids]).ravel() for mode in fields['modes']])
    b=np.column_stack([np.asarray([mode[str(n)] for n in ids]).ravel() for mode in first['field']['modes'][:2]])
    # Full common-mesh vectors, including a repeated bending family. No application
    # mode tracker is used for this independent comparison of the two solutions.
    u,s,_=np.linalg.svd(a,full_matrices=False);v,t,_=np.linalg.svd(b,full_matrices=False)
    assert min(s[-1]/s[0],t[-1]/t[0])>1e-8
    agreement=float(np.linalg.svd(u.T @ v,compute_uv=False)[-1]**2)
    metrics={'frequency_relative_difference':float(np.max(np.abs(found-expected)/expected)),
             'quadrature_sensitivity':float(np.max(np.abs(found-high)/found)),
             'nodal_subspace_agreement':agreement,'eigen_residual':first['equation_residual']}
    report={'case':f'modal-{mesher}-'+('square' if square else 'rectangle'),
            'metrics':metrics,'frequencies_hz':found.tolist(),'calculix_frequencies_hz':expected.tolist(),
            'reference_software':first['software'],'calculix':calculated['solver_identity'],
            'scope':'Independent C3D10 stiffness/mass assembly and generalized eigensolve; common-mesh nodal subspace comparison, no physical measurements'}
    write_to=Path(os.environ.get('O3D_REFERENCE_REPORT_DIR',str(directory)));write_to.mkdir(parents=True,exist_ok=True)
    (write_to/f"{report['case']}.json").write_text(json.dumps(report,indent=2)+'\n')
    assert metrics['frequency_relative_difference']<2e-3,json.dumps(report,indent=2)
    assert metrics['quadrature_sensitivity']<.02,json.dumps(report,indent=2)
    assert agreement>.995,json.dumps(report,indent=2)
    assert metrics['eigen_residual']<1e-7
