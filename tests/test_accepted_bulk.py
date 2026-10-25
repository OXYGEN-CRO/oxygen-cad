"""Accepted-state integrity, native tensors and nonlinear bulk force derivatives."""
import json,os,shutil,subprocess
from pathlib import Path
import numpy as np
import pytest
from o3d.simulation_accepted_bulk import read_bulk_frame,native_material_tensor,element_operator
from o3d.simulation_solver_profile import file_hash,validated_build
from o3d.solver import solver_binary


def synthetic(directory,*,endian='<',width=4):
    h={'schema':1,'layout':'constitutive-force-state-v2','step':1,'increment':1,'iteration':2,'nodes':4,'stride':4,'elements':1,'points':1,'state_count':13,'iperturb':[2,0],'integer_bytes':width,'real_bytes':8,'byte_order':'little' if endian=='<' else 'big','evaluation_time':1.,'increment_duration':.1}
    real=np.dtype(endian+'f8');integer=np.dtype(endian+'i'+str(width));c=np.r_[200000.,.3,np.zeros(19)]
    arrays=[np.zeros((3,4,4),dtype=real).tobytes(),np.array([[1,4,1,2,1,2,3,4,0,0,0,0,0,0]],dtype=integer).tobytes(),c.astype(real).tobytes(),np.zeros(18+26,dtype=real).tobytes(),np.zeros(12+13,dtype=real).tobytes()]
    path=directory/'analysis.accepted-bulk-00000001.bin';path.write_bytes((json.dumps(h)+'\n').encode()+b''.join(arrays));return {'step':1,'increment':1,'iteration':2,'accepted_time':1.,'file':path.name,'bytes':path.stat().st_size}


@pytest.mark.parametrize('endian,width',[('<',4),('>',8)])
def test_abi_and_exact_candidate_accepted_displacement_and_history_binding(tmp_path,endian,width):
    value=read_bulk_frame(tmp_path,synthetic(tmp_path,endian=endian,width=width))
    assert value['material_state_matches_accepted_output'] and value['state'].shape==(1,13)
    assert np.array_equal(value['candidate_displacements_mm'],value['accepted_displacements_mm'])


@pytest.mark.parametrize('damage',['truncated','extra','escape','symlink','candidate','state','connectivity','nan'])
def test_malformed_state_cannot_inherit_accepted_binding(tmp_path,damage):
    frame=synthetic(tmp_path);path=tmp_path/frame['file'];raw=path.read_bytes();line,payload=raw.split(b'\n',1);values=bytearray(payload)
    if damage=='truncated':path.write_bytes(raw[:-1])
    elif damage=='extra':path.write_bytes(raw+b'x')
    elif damage=='escape':frame['file']='../'+frame['file']
    elif damage=='symlink':
        original=tmp_path/'original';path.rename(original);path.symlink_to(original)
    else:
        if damage=='candidate':values[8:16]=np.array([1e-10],dtype='<f8').tobytes()
        if damage=='state':values[-8:]=np.array([1e-10],dtype='<f8').tobytes()
        if damage=='connectivity':values[384+20:384+24]=np.array([1],dtype='<i4').tobytes()
        if damage=='nan':values[384+56:384+64]=np.array([np.nan],dtype='<f8').tobytes()
        path.write_bytes(line+b'\n'+values)
    if damage=='state':assert not read_bulk_frame(tmp_path,frame)['material_state_matches_accepted_output']
    else:
        with pytest.raises(ValueError):read_bulk_frame(tmp_path,frame)


def cell(nodes):
    xyz=np.array([[0.,0.,0.],[1.3,0.,0.],[0.,1.1,0.],[0.,0.,.8]])
    if nodes==10:xyz=np.vstack([xyz,*[(xyz[a]+xyz[b])/2 for a,b in [(0,1),(1,2),(2,0),(0,3),(1,3),(2,3)]]])
    return xyz


@pytest.mark.parametrize('nodes',[4,10])
def test_total_lagrangian_tangent_has_geometric_term_and_all_dof_force_derivatives(nodes):
    from o3d.quadratic import weights_and_gradients
    xyz=cell(nodes);u=xyz@np.array([[.05,.02,0],[0,-.015,.01],[.01,0,.02]])
    packed=np.tile(np.r_[200000.,.3,np.zeros(19)],(4 if nodes==10 else 1,1));c=native_material_tensor(packed[0],2)
    def stress(displacement):
        gradients=[np.array([[-1.,-1.,-1.],[1.,0,0],[0,1.,0],[0,0,1.]])] if nodes==4 else [weights_and_gradients(np.array([.5854101966249685 if j==q else .1381966011250105 for j in range(4)])[1:])[1] for q in range(4)]
        result=[]
        for reference in gradients:
            g=reference@np.linalg.inv((xyz-xyz[:1]).T@reference);f=np.eye(3)+displacement.T@g;e=(f.T@f-np.eye(3))/2;v=np.r_[np.diag(e),2*e[0,1],2*e[0,2],2*e[1,2]];result.append(c@v)
        return np.array(result)
    matrix,force=element_operator(xyz,u,packed,stress(u),2,True)
    h=1e-7;columns=[]
    for d in range(3*nodes):
        change=np.zeros_like(u);change.flat[d]=h
        plus=element_operator(xyz,u+change,packed,stress(u+change),2,True)[1]
        minus=element_operator(xyz,u-change,packed,stress(u-change),2,True)[1]
        columns.append(((plus-minus)/(2*h)).ravel())
    fd=np.column_stack(columns)
    assert np.linalg.norm(matrix-fd)/np.linalg.norm(fd)<1e-8
    assert np.linalg.norm(force.sum(axis=0))<1e-9


@pytest.fixture(scope='module')
def actual(tmp_path_factory):
    from .test_contact_work import fixture as contact
    from .test_simulation_domain import fixture as axial
    from o3d.extended_solver import calculix_solve
    from o3d.simulation_accepted_bulk import AcceptedBulkAudit
    build=validated_build(solver_binary())
    if build is None:pytest.skip('Configure pinned accepted bulk exporter')
    root=tmp_path_factory.mktemp('accepted-bulk');values={}
    for kind in ['compression','slide','plastic','plastic-unload','large']:
        if kind in ['compression','slide']:system,case=contact(kind)
        else:
            _,domain,study,case,system=axial(divisions=(2,1,1),kind='nonlinear_static')
            if kind.startswith('plastic'):
                m=domain.materials[0];m.plastic_curve=[(250,0),(350,.01)];m.plastic_source='Declared synthetic J2 numerical reference';case.loads[0].vector=(1200,0,0)
                if kind=='plastic-unload':case.history=[(0,0),(1,1),(2,0)]
            else:study.settings.large_deformation=True;case.loads[0].vector=(4000,0,0)
        directory=root/kind;result=calculix_solve(system,case,directory);values[kind]=(system,case,directory,result)
    return build,values


def test_actual_states_bind_exactly_and_reconstruct_native_internal_forces(actual):
    _,values=actual
    for kind,(_,_,directory,result) in values.items():
        audit=result['checks']['accepted_bulk_state_audit']
        assert audit['all_accepted_states_bound'] and audit['state_bound_frames']>0
        assert audit['maximum_native_force_relative_error']<1e-10
        assert not audit['accepted_state_tangent_qualified'] and not audit['full_contact_stability_qualified']
        frames=json.loads((directory/'accepted-bulk-audit.json').read_text())['history']
        assert all(r['exact_material_history_binding'] and r['exact_displacement_binding'] for r in frames)
        if kind.startswith('plastic'):assert not audit['force_jacobian_complete_frames']


@pytest.mark.skipif(not os.environ.get('O3D_BULK_KERNEL_EVIDENCE'),reason='Configure pinned independent bulk/material kernel receipt')
def test_actual_accepted_tensors_match_unmodified_native_elements_and_material_replay(actual):
    _,values=actual;receipt_path=Path(os.environ['O3D_BULK_KERNEL_EVIDENCE']);receipt=json.loads(receipt_path.read_text());root=receipt_path.parent
    for name,digest in receipt['files_sha256'].items():assert file_hash(root/name)==digest
    for kind,(system,_,directory,result) in values.items():
        frame=json.loads((directory/'analysis.accepted-bulk.jsonl').read_text().splitlines()[-1]);data=read_bulk_frame(directory,frame);record=data['records'][0];_,nn,nq,code=map(int,record[:4]);nodes=np.array(record[4:4+nn],dtype=int);xyz=np.array([system.mesh.nodes[int(n)] for n in nodes]);u=data['accepted_displacements_mm'][nodes-1];packed=data['tangent'][:nq];stress=data['stress'][:nq];nonlinear=data['header']['iperturb'][1]
        own,_=element_operator(xyz,u,packed,stress,code,bool(nonlinear));lines=[f'{nn} {code} {nonlinear}',*[' '.join(map(str,x)) for x in xyz],*[' '.join(map(str,x)) for x in u],*[' '.join(map(str,x)) for x in packed],*[' '.join(map(str,x)) for x in stress]]
        raw=subprocess.run([str(root/receipt['binaries']['bulk'])],input='\n'.join(lines)+'\n',text=True,capture_output=True,check=True).stdout;native=np.array([float(x.replace('D','E')) for x in raw.split()]).reshape(3*nn,3*nn)
        assert np.linalg.norm(native-own)/np.linalg.norm(native)<1e-10
        if kind.startswith('plastic'):
            reference=np.array([[-1.,-1.,-1.],[1.,0,0],[0,1.,0],[0,0,1.]])
            gradient=u.T@(reference@np.linalg.inv((xyz-xyz[:1]).T@reference));strain=(gradient+gradient.T)/2
            e=np.r_[np.diag(strain),strain[0,1],strain[0,2],strain[1,2]]
            material=system.assignments[system.mesh.elements[0].body_id]
            lines=[f'0 {material.youngs_modulus_mpa} {material.poisson_ratio} {len(material.plastic_curve)} .1 1e-8',' '.join(map(str,e)),' '.join(map(str,data['initial_state'][0])),*[f'{plastic} {stress}' for stress,plastic in material.plastic_curve]]
            raw=subprocess.run([str(root/receipt['binaries']['material'])],input='\n'.join(lines)+'\n',text=True,capture_output=True,check=True).stdout
            point=np.array([float(x.replace('D','E')) for x in raw.split()]);assert point.shape==(148,)
            assert point[:6]==pytest.approx(data['stress'][0],abs=1e-8)
            assert point[6:19]==pytest.approx(data['state'][0],abs=1e-10)
            assert point[19:40]==pytest.approx(data['tangent'][0],abs=1e-8)
            fd=point[40:76].reshape(6,6);tensor=native_material_tensor(point[19:40],-51)
            assert np.linalg.norm(tensor-fd)/np.linalg.norm(fd)<1e-7
    checks=receipt['material_checks'];assert next(x for x in checks if not x['finite_strain'])['force_derivative_qualified']
    # The pinned finite-strain native symmetric tensor has a measured mismatch;
    # its source identity and failure remain explicit, rather than tolerance inflation.
    finite=next(x for x in checks if x['finite_strain']);assert not finite['force_derivative_qualified']


from .test_native_tangent import exporter_probe


@pytest.mark.parametrize('mode,reason',[
    ('bulk-mpc','unsupported_bulk_evaluation_context'),('bulk-budget','accepted_bulk_label_budget'),
    ('bulk-family','unsupported_bulk_element_family'),('bulk-missing','no_matching_bulk_evaluation'),
    ('bulk-time','no_matching_bulk_evaluation')])
def test_actual_exporter_refuses_unsupported_or_unmatched_bulk_state(tmp_path,exporter_probe,mode,reason):
    subprocess.run([str(exporter_probe),mode],cwd=tmp_path,capture_output=True,check=True)
    rows=[json.loads(x) for x in (tmp_path/'analysis.accepted-bulk.jsonl').read_text().splitlines()]
    assert not rows[1]['available'] and rows[1]['reason']==reason
    assert not list(tmp_path.glob('analysis.accepted-bulk-*.bin'))


def test_actual_exporter_produces_a_complete_state_bound_payload(tmp_path,exporter_probe):
    subprocess.run([str(exporter_probe),'bulk-valid'],cwd=tmp_path,capture_output=True,check=True)
    frame=json.loads((tmp_path/'analysis.accepted-bulk.jsonl').read_text().splitlines()[1])
    assert frame['available'];data=read_bulk_frame(tmp_path,frame)
    assert data['material_state_matches_accepted_output'] and data['records'].tolist()==[[1,4,1,2,1,2,3,4,0,0,0,0,0,0]]


@pytest.mark.parametrize('kind',['plastic','large','combined'])
def test_native_nonlinear_contact_keeps_state_force_and_derivative_qualification_distinct(tmp_path,kind):
    from .test_canonical_contact import reference
    from o3d.simulation_models import Region,Support
    from o3d.simulation_quadratic import QuadraticSystem
    from o3d.extended_solver import calculix_solve
    if validated_build(solver_binary()) is None:pytest.skip('Configure pinned accepted bulk exporter')
    _,domain,study,case=reference(refined=False);mesh=domain.models[0].mesh
    case.supports=[Support(id='root-x',region_id='first:xmin',kind='partial',components=(True,False,False))]
    for body in ['first','second']:
        for axis,label in [(1,'y'),(2,'z')]:
            nodes=sorted({n for e in mesh.elements if e.body_id==body for n in e.nodes if abs(mesh.nodes[n][axis])<1e-10})
            name=f'{body}:{label}-symmetry';mesh.regions.append(Region(id=name,body_id=body,node_ids=nodes))
            case.supports.append(Support(id=name,region_id=name,kind='partial',components=tuple(i==axis for i in range(3))))
    if kind in ['plastic','combined']:
        for material in domain.materials:
            material.plastic_curve=[(10,0),(25,.001)];material.plastic_source='Declared synthetic hardening curve; no measured physical material'
    study.settings.large_deformation=kind in ['large','combined']
    system=QuadraticSystem(mesh,study,{m.id:m for m in domain.materials});result=calculix_solve(system,case,tmp_path)
    bulk=result['checks']['accepted_bulk_state_audit'];operator=result['checks']['contact_current_state_audit']['full_frozen_operator']
    assert bulk['all_accepted_states_bound'] and bulk['maximum_native_force_relative_error']<1e-10
    assert operator['performed'] and operator['frames']==10
    assert (operator['complete_frames']==10)==(kind=='large')
    assert not bulk['accepted_state_tangent_qualified'] and not operator['full_contact_stability_qualified']
    assert (result['values']['max_equivalent_plastic_strain']>0)==(kind!='large')
    from o3d.simulation_contact_global import matrix_hash
    from scipy import sparse
    history=json.loads((tmp_path/'material-derivative-history.json').read_text())
    states={r['accepted_bulk_file_sha256']:r for r in history['history']}
    global_history=json.loads((tmp_path/'contact-global.json').read_text())['history']
    for row in global_history:
        state=states[row['accepted_bulk_file_sha256']]
        assert row['bulk_force_jacobian_complete']==state['available']
        assert row['bulk_operator_kind']==state['operator_kind']
        assert row['material_derivative_audit_sha256']==state.get('audit_file_sha256')
    snapshot=json.loads((tmp_path/'contact-global-dofs.json').read_text())['snapshot']
    native=sparse.load_npz(tmp_path/'contact-global-native-algorithmic-bulk.npz')
    selected=sparse.load_npz(tmp_path/'contact-global-bulk.npz')
    assert matrix_hash(native)==snapshot['native_algorithmic_bulk_operator_sha256']
    state=states[snapshot['accepted_bulk_file_sha256']]
    assert matrix_hash(selected)==(state['operator_sha256'] if state['available'] and state['plastic_points'] else matrix_hash(native))


@pytest.mark.skipif(not os.environ.get('O3D_BULK_KERNEL_EVIDENCE'),reason='Configure native bulk kernel receipt')
def test_curved_quadratic_large_geometry_matches_native_element_and_preserves_rigid_rotation():
    from o3d.quadratic import weights_and_gradients
    root=Path(os.environ['O3D_BULK_KERNEL_EVIDENCE']).parent;receipt=json.loads((root/'result.json').read_text())
    xyz=cell(10);xyz[4]+=[0,.02,.01];xyz[8]+=[.01,0,-.015]
    u=np.random.default_rng(832).normal(0,.008,xyz.shape)
    packed=np.tile(np.r_[200000.,.27,np.zeros(19)],(4,1));c=native_material_tensor(packed[0],2)
    def stresses(displacement):
        values=[]
        for q in range(4):
            reference=weights_and_gradients(np.array([.5854101966249685 if j==q else .1381966011250105 for j in range(4)])[1:])[1]
            g=reference@np.linalg.inv((xyz-xyz[:1]).T@reference);f=np.eye(3)+displacement.T@g;e=(f.T@f-np.eye(3))/2
            values.append(c@np.r_[np.diag(e),2*e[0,1],2*e[0,2],2*e[1,2]])
        return np.array(values)
    s=stresses(u);own,_=element_operator(xyz,u,packed,s,2,True)
    lines=['10 2 1',*[' '.join(map(str,x)) for x in xyz],*[' '.join(map(str,x)) for x in u],*[' '.join(map(str,x)) for x in packed],*[' '.join(map(str,x)) for x in s]]
    run=subprocess.run([str(root/receipt['binaries']['bulk'])],input='\n'.join(lines)+'\n',capture_output=True,text=True,check=True)
    native=np.array([float(x.replace('D','E')) for x in run.stdout.split()]).reshape(30,30)
    assert np.linalg.norm(native-own)/np.linalg.norm(native)<1e-10
    rotation=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
    rigid=xyz@(rotation-np.eye(3)).T
    _,force=element_operator(xyz,rigid,packed,stresses(rigid),2,True)
    assert np.linalg.norm(force)<1e-8


def test_worst_contact_frame_saves_its_own_bulk_instead_of_a_later_tensor(tmp_path):
    from scipy import sparse
    from .test_contact_global import problem,frozen_point
    from o3d.simulation_contact_global import GlobalContactAudit
    system,case=problem();response,trace=frozen_point(system);audit=GlobalContactAudit(system,case)
    audit.start();audit.point(response['tangent_n_mm'],trace);audit.finish(1.)
    original=audit.bulk.copy()
    # Emulate a subsequent accepted material tensor changing after selection of
    # the retained worst frame. Its bulk/contact/reduced triplet must stay bound.
    audit.bulk=original*2;audit.write(tmp_path)
    saved=sparse.load_npz(tmp_path/'contact-global-bulk.npz');assert sparse.linalg.norm(saved-original)==0
    contact=sparse.load_npz(tmp_path/'contact-global-contact.npz');reduced=sparse.load_npz(tmp_path/'contact-global-reduced.npz')
    assert sparse.linalg.norm((saved+contact)[audit.free][:,audit.free]-reduced)==0
