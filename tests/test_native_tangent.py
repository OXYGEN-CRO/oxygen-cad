"""Native equation exports: transaction integrity, actual solves and state limits."""
import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
import numpy as np
import pytest
from scipy import sparse
from o3d.analysis import parse_frd
from o3d.extended_solver import calculix_solve
from o3d.simulation_native_tangent import LAYOUT, audit_native_newton, read_linearization
from o3d.simulation_solver_profile import contract, validated_build
from o3d.solver import run_process, solver_binary


def synthetic(directory, *, endian='<', width=4):
    name = 'analysis.native-tangent-00000001.bin'
    frame = {'type': 'frame', 'step': 1, 'increment': 1, 'iteration': 2,
        'assembly_serial': 2, 'accepted_time': 1., 'available': True, 'file': name}
    header = {'schema': 1, 'layout': LAYOUT, 'step': 1, 'increment': 1, 'iteration': 2,
        'nodes': 2, 'stride': 4, 'equations': 2, 'off_diagonal': 1, 'asymmetric': 1,
        'penalty_scale': 1, 'integer_bytes': width, 'real_bytes': 8,
        'byte_order': 'little' if endian == '<' else 'big', 'assembly_time': 1.}
    i = np.dtype(endian+'i'+str(width)); d = np.dtype(endian+'f8')
    pieces = [(d,[4,3]), (d,[2]), (d,[1]), (i,[1,2,2]), (i,[2]),
              (i,[0,2,-2,-4,0,1,-6,-8]), (d,[0]*8), (d,[.7,.1]), (d,[.2,-.1]),
              (d,[.01,0,0,.02,0,0])]
    path = directory/name
    path.write_bytes((json.dumps(header)+'\n').encode()+b''.join(np.array(v,dtype=t).tobytes() for t,v in pieces))
    frame['bytes'] = path.stat().st_size
    pinned = contract()
    meta = {'type': 'header', 'schema': 1, 'upstream': pinned['upstream_version'],
        'source_sha256': pinned['source_sha256'], 'instrumentation': 'native-newton-linearization-v1'}
    manifest = directory/'analysis.native-tangent.jsonl'
    manifest.write_text(json.dumps(meta)+'\n'+json.dumps(frame)+'\n')
    return frame


def synthetic_audit(directory):
    system = SimpleNamespace(ids=[1,2], region_nodes=lambda _: [0,1],
        _contact_build={'binary_sha256':'a'*64, 'receipt_sha256':'b'*64})
    case = SimpleNamespace(supports=[SimpleNamespace(kind='partial', components=(False,True,True), region_id='guide')])
    result = {'checks':{}, 'field':{'displacements_mm':{1:[.01,0,0],2:[.02,0,0]}}}
    increments = [{'step':1,'increment':1,'total_time':1.}]
    return audit_native_newton(directory,system,case,result,increments)


@pytest.mark.parametrize('endian,width', [('<',4),('>',8)])
def test_actual_nonsymmetric_equation_orientation_abi_and_separate_states(tmp_path,endian,width):
    frame=synthetic(tmp_path,endian=endian,width=width)
    value=read_linearization(tmp_path,frame)
    assert value['matrix'].toarray()==pytest.approx(np.array([[4,1],[2,3]]))
    assert value['matrix']@value['native_correction_mm']==pytest.approx(value['rhs_n'])
    # Accepted update deliberately differs from the raw linear-solver correction.
    assert value['accepted_displacements_mm'][:,0]==pytest.approx([.01,.02])
    audit=synthetic_audit(tmp_path)
    assert audit['all_accepted_frames_verified'] and audit['maximum_solve_backward_error']<1e-15
    assert audit['maximum_assembly_to_accepted_displacement_mm']==.02
    assert not audit['accepted_state_tangent_qualified'] and not audit['full_contact_stability_qualified']


@pytest.mark.parametrize('damage', ['truncated','extra','escape','symlink','mapping','nan','sparse','matrix'])
def test_malformed_or_false_native_equations_refuse(tmp_path,damage):
    frame=synthetic(tmp_path);path=tmp_path/frame['file'];raw=path.read_bytes();line,payload=raw.split(b'\n',1)
    if damage=='truncated':path.write_bytes(raw[:-1])
    elif damage=='extra':path.write_bytes(raw+b'x')
    elif damage=='escape':frame['file']='../'+frame['file']
    elif damage=='symlink':
        original=tmp_path/'original';path.rename(original);path.symlink_to(original)
    else:
        content=bytearray(payload)
        if damage=='mapping':content[56:60]=np.array([-1],dtype='<i4').tobytes() # eliminated mechanical MPC
        elif damage=='nan':content[:8]=np.array([np.nan],dtype='<f8').tobytes()
        elif damage=='sparse':content[44:48]=np.array([1],dtype='<i4').tobytes() # lower row <= column
        elif damage=='matrix':content[:8]=np.array([40.],dtype='<f8').tobytes()
        path.write_bytes(line+b'\n'+content)
    if damage=='matrix':
        with pytest.raises(ValueError,match='solved correction'):synthetic_audit(tmp_path)
    else:
        message={'mapping':'equation mapping','sparse':'row indices'}.get(damage)
        with pytest.raises(ValueError,match=message):read_linearization(tmp_path,frame)


@pytest.mark.parametrize('damage', ['missing-frame','duplicate','source','time','truncated-line','support'])
def test_manifest_coverage_and_state_identity_refuse(tmp_path,damage):
    synthetic(tmp_path);path=tmp_path/'analysis.native-tangent.jsonl';lines=path.read_text().splitlines();a,b=map(json.loads,lines)
    if damage=='missing-frame':lines=lines[:1]
    elif damage=='duplicate':lines.append(lines[1])
    elif damage=='source':a['source_sha256']='0'*64;lines[0]=json.dumps(a)
    elif damage=='time':b['accepted_time']=.5;lines[1]=json.dumps(b)
    elif damage=='support':
        frame=json.loads(lines[1]);binary=tmp_path/frame['file'];raw=binary.read_bytes();header,payload=raw.split(b'\n',1);values=bytearray(payload)
        # Still a valid bijective native equation map, but a free Y replaces X.
        values[48:80]=np.array([0,0,2,-4,0,1,-6,-8],dtype='<i4').tobytes();binary.write_bytes(header+b'\n'+values)
    path.write_text('\n'.join(lines)+('' if damage=='truncated-line' else '\n'))
    with pytest.raises(ValueError,match='declared mesh supports' if damage=='support' else None):synthetic_audit(tmp_path)


def test_explicit_unavailable_frame_does_not_invent_an_operator(tmp_path):
    synthetic(tmp_path);path=tmp_path/'analysis.native-tangent.jsonl';lines=path.read_text().splitlines();frame=json.loads(lines[1])
    frame.update(available=False,reason='native_tangent_resource_budget');lines[1]=json.dumps(frame);path.write_text('\n'.join(lines)+'\n')
    audit=synthetic_audit(tmp_path)
    assert not audit['performed'] and not audit['all_accepted_frames_verified']
    assert audit['maximum_solve_backward_error'] is None


@pytest.fixture(scope='module')
def actual(tmp_path_factory):
    binary=Path(solver_binary()).resolve();build=validated_build(binary)
    if build is None:pytest.skip('Pinned native Newton exporter is not configured')
    from .test_contact_work import fixture
    from .test_simulation_domain import fixture as axial
    root=tmp_path_factory.mktemp('native-newton');values={}
    for kind in ['compression','slide','plastic','plastic-unload','large']:
        if kind in {'plastic','plastic-unload','large'}:
            _,domain,study,case,system=axial(divisions=(2,1,1),kind='nonlinear_static')
            if kind.startswith('plastic'):
                material=domain.materials[0];material.plastic_curve=[(250,0),(350,.01)]
                material.plastic_source='Declared synthetic J2 numerical reference; no physical calibration'
                case.loads[0].vector=(1200,0,0)
                if kind=='plastic-unload':case.history=[(0,0),(1,1),(2,0)]
            else:study.settings.large_deformation=True;case.loads[0].vector=(4000,0,0)
        else:system,case=fixture(kind)
        directory=root/kind;result=calculix_solve(system,case,directory)
        values[kind]=(system,case,directory,result)
    return binary,build,values


def test_actual_native_equations_cover_contact_sliding_plasticity_and_large_geometry(actual):
    _,_,values=actual
    for kind,(system,case,directory,result) in values.items():
        audit=result['checks']['native_newton_audit']
        assert audit['performed'] and audit['all_accepted_frames_verified']
        from o3d.extended_solver import _last_increment
        offset=system._contact_definition.initialization_period if system._contact_definition else 0.
        assert audit['verified_frames']==len(_last_increment(directory/'analysis.sta',system._load_path.duration+offset))
        assert audit['maximum_solve_backward_error']<1e-12
        assert not audit['accepted_state_tangent_qualified'] and not audit['full_contact_stability_qualified']
        if kind=='plastic':
            assert max(u[0] for u in result['field']['displacements_mm'].values())==pytest.approx(.065,rel=1e-5)
            assert result['values']['max_equivalent_plastic_strain']==pytest.approx(.005,rel=1e-5)
        if kind=='plastic-unload':
            assert max(u[0] for u in result['field']['displacements_mm'].values())==pytest.approx(.05,rel=1e-5)
        if kind=='slide':
            rows=json.loads((directory/'native-newton-audit.json').read_text())['history']
            assert any(r.get('asymmetric') for r in rows)


def test_actual_material_tangent_has_independent_plastic_and_unloading_compliance(actual):
    from scipy.sparse.linalg import spsolve
    _,_,values=actual
    for kind,expected in [('plastic',10/4*(1/200000+1/10000)), ('plastic-unload',10/(4*200000))]:
        system,case,directory,result=values[kind]
        frames=[json.loads(line) for line in (directory/'analysis.native-tangent.jsonl').read_text().splitlines()[1:]]
        data=read_linearization(directory,frames[-1]);mapping=data['equation_by_node']
        # Independent uniform uniaxial loading: d(epsilon)/d(sigma)=1/E+1/H
        # while plastic loading; unloading recovers elastic compliance 1/E.
        force=system.loads(case).reshape(-1,3)/1200
        rhs=np.zeros(data['matrix'].shape[0])
        for i,node in enumerate(system.ids):
            for axis in range(3):
                if mapping[node-1,axis]>0:rhs[mapping[node-1,axis]-1]=force[i,axis]
        delta=spsolve(data['matrix'],rhs)
        axial=max(delta[mapping[n-1,0]-1] for n in system.ids if mapping[n-1,0]>0)
        assert axial==pytest.approx(expected,rel=1e-10)


def test_actual_export_is_numerically_neutral_on_same_binary(actual,tmp_path):
    binary,build,values=actual
    # Fix result/stiffness threads for a reproducible enabled/disabled comparison.
    for flag in ['0','1']:
        target=tmp_path/flag;target.mkdir();shutil.copyfile(values['compression'][2]/'analysis.inp',target/'analysis.inp')
        run_process([binary,'analysis'],target,target/'solver.log',environment_overrides={
            'O3D_CCX_CONTACT_TELEMETRY':'1','O3D_CCX_NATIVE_TANGENT':flag,
            'CCX_NPROC_RESULTS':'1','CCX_NPROC_STIFFNESS':'1','LD_LIBRARY_PATH':build['runtime_library_path']})
        assert (target/'analysis.native-tangent.jsonl').exists()==(flag=='1')
    assert (tmp_path/'0/analysis.dat').read_bytes()==(tmp_path/'1/analysis.dat').read_bytes()
    assert parse_frd(tmp_path/'0/analysis.frd')==parse_frd(tmp_path/'1/analysis.frd')


def test_native_matrix_matches_independent_frozen_bulk_and_contact(actual):
    from o3d.simulation_native_tangent import read_linearization
    _,_,values=actual
    for kind in ['compression','slide']:
        system,case,directory,result=values[kind]
        dofs=json.loads((directory/'contact-global-dofs.json').read_text());when=dofs['snapshot']['time_s']
        rows=json.loads((directory/'contact-stability.json').read_text())['history']
        identity=next((r['step'],r['increment']) for r in rows if r['time_s']==when)
        frames=[json.loads(line) for line in (directory/'analysis.native-tangent.jsonl').read_text().splitlines()[1:]]
        frame=next(r for r in frames if (r['step'],r['increment'])==identity)
        data=read_linearization(directory,frame);mapping=data['equation_by_node']
        reorder=[int(mapping[system.ids[d//3]-1,d%3])-1 for d in dofs['free_dofs']]
        native=data['matrix'][reorder][:,reorder];frozen=sparse.load_npz(directory/'contact-global-reduced.npz')
        # These controlled linear-bulk cases also have an independent FE/native
        # kernel comparison in test_contact_global; nonlinear states are not
        # silently substituted with this frozen elastic operator.
        assert sparse.linalg.norm(native-frozen)/sparse.linalg.norm(frozen)<1e-10


@pytest.fixture(scope='module')
def exporter_probe(tmp_path_factory):
    import subprocess
    from o3d.simulation_solver_profile import file_hash
    root=Path(__file__).resolve().parents[1]
    configured=os.environ.get('O3D_NATIVE_TANGENT_PROBE')
    if configured:
        binary=Path(configured).resolve();directory=binary.parent
        declared=dict(line.split(maxsplit=1)[::-1] for line in (directory/'SHA256SUMS').read_text().splitlines())
        assert set(declared)=={'probe','native_tangent_limits.c','oxygen_contact_telemetry.h','oxygen_native_tangent.h','COPYING'}
        for name,digest in declared.items():assert file_hash(directory/name)==digest
        assert file_hash(directory/'oxygen_native_tangent.h')==contract()['native_tangent_header_sha256']
        assert file_hash(directory/'oxygen_contact_telemetry.h')==contract()['telemetry_header_sha256']
        assert file_hash(directory/'native_tangent_limits.c')==file_hash(root/'scripts/native_tangent_limits.c')
        return binary
    compiler=shutil.which('cc')
    if not compiler:pytest.skip('C compiler needed for actual exporter resource/transaction branches')
    directory=tmp_path_factory.mktemp('native-exporter-probe');binary=directory/'probe'
    subprocess.run([compiler,'-O2','-I',str(root/'third_party/calculix'),str(root/'scripts/native_tangent_limits.c'),'-lm','-o',str(binary)],check=True,capture_output=True)
    return binary


@pytest.mark.parametrize('mode,reason', [
    ('mpc','multipoint_constraint_expansion_not_exported'),
    ('budget','native_tangent_resource_budget'),
    ('total-budget','native_tangent_total_output_budget'),
    ('missing-solve','no_matching_newton_linearization'),
    ('time','no_matching_newton_linearization')])
def test_real_exporter_refuses_unavailable_or_uncommitted_matrices(tmp_path,exporter_probe,mode,reason):
    import subprocess
    subprocess.run([str(exporter_probe),mode],cwd=tmp_path,check=True,capture_output=True)
    frames=[json.loads(line) for line in (tmp_path/'analysis.native-tangent.jsonl').read_text().splitlines()]
    assert len(frames)==2 and frames[1]['available'] is False and frames[1]['reason']==reason
    assert not list(tmp_path.glob('analysis.native-tangent-*.bin'))
