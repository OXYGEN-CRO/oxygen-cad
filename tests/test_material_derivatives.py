"""Native constitutive replay, nonsymmetric force derivatives and refusal gates."""
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pytest

from o3d.simulation_accepted_bulk import element_operator
from o3d.simulation_material_derivatives import assess_point,quadrature_strains,run_replay
from o3d.simulation_solver_profile import validated_build,validated_material_probe
from o3d.solver import solver_binary
from .test_accepted_bulk import cell,actual


@pytest.fixture
def native_probe():
    if not os.environ.get('O3D_BULK_KERNEL_EVIDENCE'):pytest.skip('Configure source-bound material replay tool')
    build=validated_build(solver_binary())
    if build is None:pytest.skip('Configure pinned accepted state solver')
    return validated_material_probe(build)


def request(strain,nonlinear=1,initial=None):
    return {'strain':strain,'nonlinear':nonlinear,'youngs_modulus_mpa':200000.,'poisson_ratio':.3,
        'curve':[(0.,250.),(.01,350.)],'initial_state':np.zeros(13) if initial is None else initial,'increment_duration':.1}


@pytest.mark.parametrize('nodes',[4,10])
def test_finite_plastic_nonsymmetric_material_derivative_reconstructs_all_nodal_force_columns(tmp_path,native_probe,nodes):
    xyz=cell(nodes)
    if nodes==10:xyz[4]+=[0,.02,.01];xyz[8]+=[.01,0,-.015]
    u=xyz@np.array([[.018,.005,.002],[.001,-.004,.002],[.002,.001,-.003]])
    strains=quadrature_strains(xyz,u,True)
    values,_=run_replay(native_probe,[request(e) for e in strains],tmp_path)
    tensors=[]
    for point in values:
        row,c=assess_point(point,point[0,:6],point[0,6:19],point[0,19:40])
        assert row['replay_bound'] and row['finite_difference_qualified']
        assert not row['native_algorithmic_tensor_qualified']
        assert row['derivative_asymmetry_ratio']>1e-4
        tensors.append(c)
    packed=values[:,0,19:40];stress=values[:,0,:6]
    candidate,_=element_operator(xyz,u,packed,stress,-51,True,material_tensors=tensors)
    native,_=element_operator(xyz,u,packed,stress,-51,True)
    # Independent displacement perturbations differentiate the full native-stress
    # force, including geometric terms and every quadratic midside DOF.
    h=2e-7;requests=[];displacements=[]
    for d in range(3*nodes):
        change=np.zeros_like(u);change.flat[d]=h
        for displacement in (u+change,u-change):
            displacements.append(displacement)
            requests.extend(request(e) for e in quadrature_strains(xyz,displacement,True))
    other=tmp_path/'force-columns';other.mkdir()
    perturbed,_=run_replay(native_probe,requests,other)
    stresses=perturbed[:,0,:6].reshape(6*nodes,len(strains),6)
    forces=[element_operator(xyz,v,packed,s,-51,True)[1].ravel() for v,s in zip(displacements,stresses)]
    fd=np.column_stack([(forces[2*d]-forces[2*d+1])/(2*h) for d in range(3*nodes)])
    (tmp_path/'force-derivative-comparison.json').write_text(json.dumps({'nodes':nodes,'curved_quadratic_geometry':nodes==10,
        'all_nodal_force_columns':3*nodes,'displacement_step_mm':h,
        'candidate_vs_native_force_fd_relative_error':float(np.linalg.norm(candidate-fd)/np.linalg.norm(fd)),
        'native_algorithmic_vs_native_force_fd_relative_error':float(np.linalg.norm(native-fd)/np.linalg.norm(fd)),
        'candidate_asymmetry_ratio':float(np.linalg.norm(candidate-candidate.T)/np.linalg.norm(candidate))},indent=2)+'\n')
    assert np.linalg.norm(candidate-fd)/np.linalg.norm(fd)<2e-7
    assert np.linalg.norm(native-fd)/np.linalg.norm(fd)>1e-4
    assert np.linalg.norm(candidate-candidate.T)/np.linalg.norm(candidate)>1e-4


def test_exact_yield_boundary_cannot_inherit_a_two_sided_derivative(tmp_path,native_probe):
    # Uniaxial stress at first J2 yield, no stored plastic history.
    strain=np.array([250/200000,-.3*250/200000,-.3*250/200000,0,0,0])
    values,_=run_replay(native_probe,[request(strain,nonlinear=0)],tmp_path)
    point=values[0];row,_=assess_point(point,point[0,:6],point[0,6:19],point[0,19:40])
    assert row['replay_bound'] and not row['finite_difference_qualified']
    assert row['maximum_column_one_sided_gaps'][-1]>.1
    assert not row['native_algorithmic_tensor_qualified']


def test_captured_history_mismatch_refuses_an_otherwise_smooth_material_derivative(tmp_path,native_probe):
    values,_=run_replay(native_probe,[request(np.array([.01,-.002,-.002,.003,.001,-.002]))],tmp_path)
    point=values[0];state=point[0,6:19].copy();state[0]+=1e-5
    row,_=assess_point(point,point[0,:6],state,point[0,19:40])
    assert not row['replay_bound'] and not row['finite_difference_qualified']
    assert 'replay mismatch' in row['reason']


def test_final_accepted_audit_replays_every_plastic_point_without_changing_native_qualification(actual):
    _,cases=actual
    for name,(_,_,directory,result) in cases.items():
        audit=result['checks']['material_derivative_audit']
        assert not audit['accepted_state_tangent_qualified'] and not audit['full_contact_stability_qualified']
        if name.startswith('plastic'):
            if not os.environ.get('O3D_BULK_KERNEL_EVIDENCE'):
                assert not audit['performed'] and 'unavailable' in audit['reason'];continue
            assert audit['performed'] and audit['plastic_points']==12
            assert audit['replay_bound_points']==audit['plastic_points']
            assert audit['two_sided_qualified_points']==audit['plastic_points']
            assert audit['candidate_operator_available']
            assert (directory/'material-final-force-jacobian.npz').is_file()
            assert not result['checks']['accepted_bulk_state_audit']['force_jacobian_complete_frames']
        else:assert not audit['performed'] and not audit['plastic_points']


@pytest.mark.parametrize('damage',['binary','driver','runtime','source','build','archive','api','escape','symlink'])
def test_replay_identity_rejects_changed_or_unrelated_native_tools(tmp_path,monkeypatch,native_probe,damage):
    original=Path(os.environ['O3D_BULK_KERNEL_EVIDENCE']);value=json.loads(original.read_text())
    for name in ['material-kernel-probe','source/material_kernel_probe.f90','COPYING']:
        p=tmp_path/name;p.parent.mkdir(exist_ok=True);shutil.copy2(original.parent/name,p)
    if damage in ('binary','driver'):
        p=tmp_path/('material-kernel-probe' if damage=='binary' else 'source/material_kernel_probe.f90');p.write_bytes(p.read_bytes()+b'x')
    elif damage=='runtime':value['runtime_libraries_sha256']['material'][next(iter(value['runtime_libraries_sha256']['material']))]='0'*64
    elif damage=='source':value['source_sha256']='0'*64
    elif damage=='build':value['native_build_receipt_sha256']='0'*64
    elif damage=='archive':value['native_archive_sha256']='0'*64
    elif damage=='api':value['material_api']='another-api'
    elif damage=='escape':value['binaries']['material']='../material-kernel-probe'
    else:
        p=tmp_path/'material-kernel-probe';p.unlink();p.symlink_to(original.parent/'material-kernel-probe')
    receipt=tmp_path/'result.json';receipt.write_text(json.dumps(value));monkeypatch.setenv('O3D_BULK_KERNEL_EVIDENCE',str(receipt))
    with pytest.raises(ValueError):validated_material_probe(validated_build(solver_binary()))


def test_material_replay_never_runs_without_an_explicit_tool_configuration(monkeypatch):
    monkeypatch.delenv('O3D_BULK_KERNEL_EVIDENCE',raising=False)
    assert validated_material_probe({}) is None


def test_material_budget_is_checked_before_creating_or_executing_evidence(tmp_path):
    with pytest.raises(ValueError,match='4096-point budget'):
        run_replay({},[{}]*4097,tmp_path)
    assert not list(tmp_path.iterdir())


def test_unsupported_or_absent_history_refuses_replay_before_any_native_call(actual):
    from o3d.simulation_accepted_bulk import read_bulk_frame
    from o3d.simulation_material_derivatives import replay_requests
    _,cases=actual;system,_,directory,_=cases['plastic']
    frame=json.loads((directory/'analysis.accepted-bulk.jsonl').read_text().splitlines()[-1]);data=read_bulk_frame(directory,frame)
    data['header']['state_count']=14
    with pytest.raises(ValueError,match='13-variable'):replay_requests(system,data)


def test_later_cancellation_does_not_replace_an_earlier_qualified_plateau(tmp_path,native_probe):
    values,_=run_replay(native_probe,[request(np.array([.01,-.002,-.002,.003,.001,-.002]))],tmp_path)
    point=values[0];baseline=point[0].copy()
    # Emulate increasing cancellation at the smallest step while preserving an
    # already measured smooth plateau. The audit must keep the larger valid h.
    point[-1,40:76]*=1.01
    row,_=assess_point(point,baseline[:6],baseline[6:19],baseline[19:40])
    assert row['finite_difference_qualified'] and row['selected_engineering_strain_step']==1e-8


def test_large_bulk_modulus_cannot_hide_a_nonsmooth_shear_column(tmp_path,native_probe):
    values,_=run_replay(native_probe,[request(np.zeros(6),nonlinear=0)],tmp_path)
    point=values[0];baseline=point[0].copy()
    for value in point:
        forward=value[76:112].reshape(6,6);backward=value[112:148].reshape(6,6)
        forward[:,3]*=1.1;backward[:,3]*=.9
    row,_=assess_point(point,baseline[:6],baseline[6:19],baseline[19:40])
    assert not row['finite_difference_qualified']


def test_native_replay_tool_and_receipt_changes_invalidate_the_study_fingerprint(tmp_path,monkeypatch,native_probe):
    from o3d.simulation_provenance import software_fingerprint
    original=Path(os.environ['O3D_BULK_KERNEL_EVIDENCE'])
    before=software_fingerprint('nonlinear_static')
    for name in ['material-kernel-probe','source/material_kernel_probe.f90','COPYING']:
        p=tmp_path/name;p.parent.mkdir(exist_ok=True);shutil.copy2(original.parent/name,p)
    value=json.loads(original.read_text());value['capture_note']='New independently versioned receipt; numerical binary unchanged'
    receipt=tmp_path/'result.json';receipt.write_text(json.dumps(value));monkeypatch.setenv('O3D_BULK_KERNEL_EVIDENCE',str(receipt))
    after=software_fingerprint('nonlinear_static')
    assert before['material_replay_tool']['binary_sha256']==after['material_replay_tool']['binary_sha256']
    assert before['material_replay_tool']['receipt_sha256']!=after['material_replay_tool']['receipt_sha256']
    monkeypatch.delenv('O3D_BULK_KERNEL_EVIDENCE')
    assert software_fingerprint('nonlinear_static')['material_replay_tool']=='unavailable'
