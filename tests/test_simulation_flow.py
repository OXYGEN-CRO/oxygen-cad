"""Conservative geometry, explicit physics and actual native laminar benchmarks."""
import copy
import os
from pathlib import Path
import numpy as np
import pytest
from pydantic import ValidationError
from o3d.simulation_models import AnalysisModel,BodyAssignment,FlowBoundary,FlowSettings,LoadCase,MaterialRecord,SimulationDomain,StudyRecord
from o3d.simulation_mesh import block_mesh
from o3d.simulation_flow_case import write_case
from o3d.simulation_flow_mesh import write_mesh
from o3d.simulation_flow import solve_mesh,mesh_report,validate_inputs,final_iteration
from o3d.simulation_flow_fields import read_field
from o3d.simulation_worker import solve


def fixture(divisions=(3,3,3),kind='couette'):
    mesh=block_mesh('fluid-domain',size=(10,10,10),divisions=divisions)
    material=MaterialRecord(id='fluid',name='Synthetic analytical fluid',source='Numerical benchmark; no physical material calibration',density_g_cm3=.001,dynamic_viscosity_pa_s=.001,
        property_sources={'density_g_cm3':'Declared analytical density 1 kg/m3','dynamic_viscosity_pa_s':'Declared analytical viscosity 0.001 Pa s'})
    flow=[FlowBoundary(id='top',region_id='part:ymax',kind='moving_wall' if kind=='couette' else 'no_slip',velocity_m_s=(.1,0,0) if kind=='couette' else None,source='Analytical wall'),
        FlowBoundary(id='bottom',region_id='part:ymin',kind='no_slip',source='Analytical stationary wall')]
    flow.extend(FlowBoundary(id='p-'+side,region_id='part:x'+side,kind='pressure',pressure_gauge_pa=.08 if side=='min' and kind=='poiseuille' else 0,source='Analytical pressure end') for side in ['min','max'])
    flow.extend(FlowBoundary(id='s-'+side,region_id='part:z'+side,kind='symmetry',source='Spanwise symmetry') for side in ['min','max'])
    case=LoadCase(id=kind,name=kind,flow=flow)
    settings=FlowSettings(domain_kind='fluid_volume',model='steady_laminar_incompressible_newtonian',source='Explicit fluid volume; laminar isothermal incompressible numerical benchmark',maximum_iterations=2000)
    study=StudyRecord(id='flow',name='Flow',type='general_cfd',model_id='model',assignments=[BodyAssignment(body_id='part',material_id='fluid')],cases=[case],flow=settings,assumptions_confirmed=True)
    model=AnalysisModel(id='model',name='Explicit fluid volume',geometry_key='fluid-domain',mesh=mesh)
    domain=SimulationDomain(models=[model],materials=[material],studies=[study])
    return domain,study,model,material,case


def test_convex_subdivision_conserves_volume_first_moment_and_boundary_coverage(tmp_path):
    _,_,model,_,case=fixture((2,3,2));data=write_mesh(model.mesh,case.flow,tmp_path)
    assert len(data['cell_ids'])==4*len(model.mesh.elements)
    assert sum(data['volumes_m3'])==pytest.approx(1e-6,rel=1e-12)
    assert np.einsum('i,ij->j',data['volumes_m3'],data['centers_m'])==pytest.approx([5e-9]*3,rel=1e-12)
    assert sum(p['face_count'] for p in data['patches'])==len(data['faces'])-data['internal_faces']
    assert len(data['coordinates_m'])>len(model.mesh.nodes)
    for face in data['faces']:
        v=data['coordinates_m'][face['nodes']];c=v.mean(axis=0);normal=sum((np.cross(v[i]-c,v[(i+1)%4]-c) for i in range(4)),np.zeros(3))/2
        assert np.dot(normal,data['centers_m'][face['owner']]-c)<0
        if face['neighbour'] is not None:assert np.dot(normal,data['centers_m'][face['neighbour']]-c)>0


def test_boundaries_are_explicit_disjoint_complete_and_physics_is_not_discarded(tmp_path):
    domain,study,model,material,case=fixture((1,1,1))
    with pytest.raises(ValueError,match='Every exterior'):write_mesh(model.mesh,case.flow[:-1],tmp_path/'missing')
    with pytest.raises(ValueError,match='Overlapping'):write_mesh(model.mesh,[*case.flow,case.flow[0]],tmp_path/'overlap')
    material.property_sources.pop('dynamic_viscosity_pa_s')
    with pytest.raises(ValueError,match='property source'):validate_inputs(study,model,{material.id:material})
    study.settings.large_deformation=True
    with pytest.raises(ValueError,match='large_deformation'):validate_inputs(study,model,{material.id:material})
    with pytest.raises(ValidationError):FlowBoundary(id='p',region_id='r',kind='pressure',source='Test')
    with pytest.raises(ValidationError):FlowSettings(domain_kind='solid_volume',model='steady_laminar_incompressible_newtonian',source='Test')
    with pytest.raises(ValidationError):StudyRecord(id='s',name='s',model_id='model',type='steady_thermal',cases=[case])


def test_source_pressure_in_pa_is_converted_to_kinematic_pressure(tmp_path):
    _,study,model,material,case=fixture((1,1,1),'poiseuille');material.density_g_cm3=.002
    data=write_case(model.mesh,material,case,study.flow,tmp_path,1e-7)
    assert data['density_kg_m3']==2
    assert 'nu [0 2 -1 0 0 0 0] 0.00050000000000000001' in (tmp_path/'constant/transportProperties').read_text()
    assert 'value uniform 0.040000000000000001' in (tmp_path/'0/p').read_text()


def test_zero_exit_does_not_override_failed_native_mesh_report():
    with pytest.raises(ValueError,match='full topology'):mesh_report('Checking geometry...\nFailed 1 mesh checks.\nEnd\n')
    assert mesh_report('Mesh OK.\nEnd\n')['full_topology_geometry_passed']


def test_ascii_reader_refuses_units_counts_directives_nonfinite_and_truncation(tmp_path):
    header='FoamFile { format ascii; class volScalarField; }\ndimensions [0 2 -2 0 0 0 0];\n'
    p=tmp_path/'p';p.write_text(header+'internalField nonuniform List<scalar> 2 (1 2); boundaryField {}')
    assert read_field(p,2,1,[0,2,-2,0,0,0,0],'volScalarField')[0]==pytest.approx([1,2])
    for value in [header+'internalField nonuniform List<scalar> 3 (1 2);',header+'internalField uniform nan;',header+'#include "evil"',header+'internalField uniform 0',header.replace('[0 2','[0 1')+'internalField uniform 0;']:
        p.write_text(value)
        with pytest.raises(ValueError):read_field(p,2,1,[0,2,-2,0,0,0,0],'volScalarField')


@pytest.mark.skipif(not os.environ.get('O3D_OPENFOAM_ROOT'),reason='Pinned native OpenFOAM runtime not configured')
@pytest.mark.parametrize('kind',['couette','poiseuille'])
def test_native_analytical_profiles_refinement_and_conservation(tmp_path,kind):
    from o3d.simulation_flow_profile import profile
    runtime=profile();errors=[];records=[];pressure_errors=[]
    for n in [3,5]:
        _,study,model,material,case=fixture((n,n,n),kind)
        result=solve_mesh(model.mesh,material,case,study.flow,study.settings,tmp_path/str(n),runtime)
        cells=result['field']['native_flow_cells'];xyz=np.array(cells['centers_m']);velocity=np.array(cells['velocity_m_s']);vol=np.array(cells['volumes_m3'])
        y=xyz[:,1];expected=.1*y/.01 if kind=='couette' else .08/(2*.001*.01)*y*(.01-y)
        error=np.sqrt(np.dot(vol,np.sum((velocity-np.column_stack([expected,np.zeros((len(y),2))]))**2,axis=1))/np.dot(vol,expected**2))
        errors.append(float(error));records.append(result['checks'])
        assert result['checks']['native_mesh']['full_topology_geometry_passed']
        assert result['checks']['mass_balance_error']<1e-5
        assert result['checks']['local_continuity_error']<1e-4
        assert result['checks']['residual']<=study.settings.residual_tolerance
        assert result['values']['mass_g']==pytest.approx(.001,rel=1e-12)
        if kind=='poiseuille':
            expected_pressure=.08*(1-xyz[:,0]/.01)
            pressure_errors.append(float(np.sqrt(np.dot(vol,(np.array(cells['pressure_gauge_pa'])-expected_pressure)**2)/sum(vol))))
    print({'kind':kind,'relative_velocity_L2':errors,'checks':records,'pressure_L2_Pa':pressure_errors,'pressure_reference_qualified':False})
    assert errors[1]<errors[0]*.8
    assert errors[1]<.06
    if pressure_errors:
        # The pressure benchmark is a retained NONPASS, not an accepted solver
        # qualification. Improving velocity/residuals cannot override this hold.
        assert all(not r['pressure_field_spatial_accuracy_qualified'] for r in records)
        assert pressure_errors[1] > .004


@pytest.mark.skipif(not os.environ.get('O3D_OPENFOAM_ROOT'),reason='Pinned native OpenFOAM runtime not configured')
def test_worker_retains_actual_flow_cells_and_never_claims_physical_validation(tmp_path):
    domain,study,model,_,case=fixture((2,2,2))
    request={'data':domain.model_dump(mode='json'),'study_id':study.id,'case_id':case.id,'input_key':'test','geometry_key':'fluid-domain','revision':1}
    result=solve(request,tmp_path)
    assert result['study_type']=='general_cfd'
    assert result['physical_validation']=='not_supplied'
    assert result['numerical_state']=='inconclusive'
    assert any('pressure-field spatial accuracy' in gap for gap in result['verification_gaps'])
    assert 'flow mesh convergence not demonstrated' in result['verification_gaps']
    assert len(result['field']['native_flow_cells']['velocity_m_s'])==4*len(model.mesh.elements)
    assert 'displacements_mm' not in result['field']
    assert (tmp_path/'mesh-0/flow/flow-evidence.json').is_file()


def test_flow_evidence_closure_refuses_changed_fields_added_files_and_links(tmp_path):
    from o3d.simulation_flow_evidence import bind,verify
    (tmp_path/'flow-evidence.json').write_text('{}')
    (tmp_path/'U').write_text('original')
    record=bind(tmp_path);assert verify(tmp_path,record)['files']['U']
    (tmp_path/'U').write_text('changed')
    with pytest.raises(ValueError,match='changed'):verify(tmp_path,record)
    (tmp_path/'U').write_text('original');assert verify(tmp_path,record)
    (tmp_path/'extra').write_text('extra')
    with pytest.raises(ValueError,match='changed'):verify(tmp_path,record)
    (tmp_path/'extra').unlink();(tmp_path/'link').symlink_to(tmp_path/'U')
    with pytest.raises(ValueError,match='link'):verify(tmp_path,record)


def test_sampled_native_fields_use_physical_locations_and_forbid_extrapolation(tmp_path):
    from o3d.simulation_flow_convergence import sampled_change
    _,_,coarse,_,case=fixture((1,1,1));a=write_mesh(coarse.mesh,case.flow,tmp_path/'coarse')
    _,_,fine,_,case=fixture((2,2,2));b=write_mesh(fine.mesh,case.flow,tmp_path/'fine')
    def field(data):
        return {'centers_m':data['centers_m'].tolist(),'volumes_m3':data['volumes_m3'].tolist(),
            'velocity_m_s':[[.1,0,0]]*len(data['cell_ids']),'pressure_gauge_pa':[3.]*len(data['cell_ids'])}
    old=field(a);new=field(b);changes=sampled_change(coarse.mesh,old,new,1)
    assert max(changes.values())<1e-10
    new['pressure_gauge_pa']=[5.]*len(b['cell_ids'])
    assert sampled_change(coarse.mesh,old,new,1)['sampled_pressure_field_change']<1e-10
    new['centers_m'][0]=[10,10,10]
    with pytest.raises(ValueError,match='extrapolation'):sampled_change(coarse.mesh,old,new,1)


def test_negative_source_connectivity_orientation_keeps_physical_control_volumes(tmp_path):
    _,_,model,_,case=fixture((1,1,1));a=write_mesh(model.mesh,case.flow,tmp_path/'positive')
    for e in model.mesh.elements:e.nodes=(e.nodes[1],e.nodes[0],*e.nodes[2:])
    b=write_mesh(model.mesh,case.flow,tmp_path/'negative')
    assert sum(a['volumes_m3'])==pytest.approx(sum(b['volumes_m3']),rel=1e-12)
    assert np.array(sorted(map(tuple,a['centers_m'])))==pytest.approx(np.array(sorted(map(tuple,b['centers_m']))),abs=1e-15)


def test_runtime_is_administrator_configured_and_rejects_user_overrides(tmp_path,monkeypatch):
    from o3d.simulation_flow_profile import profile
    monkeypatch.delenv('O3D_OPENFOAM_ROOT',raising=False)
    with pytest.raises(ValueError,match='unavailable'):profile()
    monkeypatch.setenv('O3D_OPENFOAM_ROOT','relative/project/tool')
    with pytest.raises(ValueError,match='absolute'):profile()
    monkeypatch.setenv('O3D_OPENFOAM_ROOT',str(tmp_path));monkeypatch.setattr(Path,'home',lambda:tmp_path)
    (tmp_path/'.OpenFOAM').mkdir()
    with pytest.raises(ValueError,match='override'):profile()
