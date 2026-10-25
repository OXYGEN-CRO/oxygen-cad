"""Nonmatching full-P2 bonds, complete load paths and refusal of invented ties."""
import json
import numpy as np
import pytest
from o3d.model import Project,geometry_key
from o3d.simulation_mesh import block_mesh
from o3d.simulation_models import *
from o3d.simulation_assembly import BondedAssemblySystem,bonded_solve
from o3d.simulation_worker import solve
from .test_canonical_quadratic import request


def mesh_pair(key,divisions=((2,2,2),(3,3,3)),gap=0.,curved=False):
    blocks=[];nodes={};elements=[];regions=[]
    for name,origin,split in [('first',(0,0,0),divisions[0]),('second',(10+gap,0,0),divisions[1])]:
        m=block_mesh(key,origin=origin,divisions=split,body_id=name,element_family='C3D10');offset=max(nodes,default=0)
        nodes.update({n+offset:p for n,p in m.nodes.items()})
        element_offset=len(elements)
        elements.extend(e.model_copy(update={'id':e.id+element_offset,'nodes':tuple(n+offset for n in e.nodes)}) for e in m.elements)
        regions.extend(r.model_copy(update={'node_ids':[n+offset for n in r.node_ids],'triangles':[tuple(n+offset for n in t) for t in r.triangles]}) for r in m.regions)
    if curved:nodes={n:(x+.01*y*(2-y),y,z) for n,(x,y,z) in nodes.items()}
    return MeshRecord(geometry_key=key,nodes=nodes,elements=elements,regions=regions,element_family='C3D10',source='Declared nonmatching two-solid numerical reference, not manufactured hardware')


def reference(refined=True,curved=False):
    project=Project();key=geometry_key(project)
    mesh=mesh_pair(key,curved=curved)
    material=MaterialRecord(id='synthetic',name='Declared numerical material',youngs_modulus_mpa=200000,poisson_ratio=0,source='Declared SI elastic test, no grade/process calibration')
    interface=Interface(id='bond',kind='bonded',formulation='quadratic_surface_mortar',primary_region_id='first:xmax',secondary_region_id='second:xmin',position_tolerance_mm=1e-7,source='Declared ideal zero-thickness perfect bond')
    case=LoadCase(id='pull',name='Bonded axial load',supports=[Support(id='root',region_id='first:xmin')],loads=[Load(id='force',region_id='second:xmax',vector=(100,0,0),source='Declared 100 N SI reference')])
    study=StudyRecord(id='bonded',name='Nonmatching surface-bond reference',type='linear_static',model_id='model',assignments=[BodyAssignment(body_id=b,material_id=material.id) for b in ['first','second']],cases=[case],interfaces=[interface],assumptions_confirmed=True)
    study.settings.probe_point_mm=(20.01 if curved else 20,1,1);study.settings.probe_body_id='second'
    model=AnalysisModel(id='model',name='Two separate bonded solids',geometry_key=key,body_ids=['first','second'],mesh=mesh,refinements=[mesh_pair(key,((3,3,3),(4,4,4)),curved=curved)] if refined else [])
    domain=SimulationDomain(models=[model],materials=[material],studies=[study],active_study_id=study.id)
    return project,domain,study,case


def test_nonmatching_bond_transmits_force_and_matches_SI_series_bar(tmp_path):
    project,domain,study,_=reference()
    result=solve(request(project,domain,study),tmp_path)
    assert result['values']['probe_displacement_mm']==pytest.approx(100*.020/(200e9*.002*.002)*1000,rel=1e-9)
    assert result['values']['max_von_mises_mpa']==pytest.approx(25,rel=1e-8)
    assert result['values']['strain_energy_n_mm']==pytest.approx(.125,rel=1e-9)
    assert result['numerical_state']=='verified_numerically',result['verification_gaps']
    assert result['checks']['residual']<1e-9
    assert result['checks']['maximum_constraint_displacement_error_mm']<1e-12
    assert result['checks']['force_balance_error']<1e-9
    assert result['checks']['moment_balance_error']<1e-9
    assert result['checks']['energy_balance_error']<1e-9
    assert result['interfaces'][0]['secondary_resultant_n']==pytest.approx([-100,0,0],abs=1e-7)
    assert sum(v[0] for v in result['field']['support_reactions_n'].values())==pytest.approx(-100,rel=1e-9)
    assert (tmp_path/'mesh-1/bonded/constraint-transformation.npz').exists()
    assert len(result['field']['integration_point_stress_mpa'])==len(domain.models[0].refinements[-1].elements)


def test_two_sourced_materials_add_actual_series_compliance(tmp_path):
    project,domain,study,_=reference(refined=False)
    domain.materials.append(domain.materials[0].model_copy(update={'id':'soft','youngs_modulus_mpa':100000,'source':'Declared independent second elastic test'}));study.assignments[1].material_id='soft'
    result=solve(request(project,domain,study),tmp_path)
    assert result['values']['probe_displacement_mm']==pytest.approx(.00375,rel=1e-9)
    assert result['interfaces'][0]['secondary_resultant_n']==pytest.approx([-100,0,0],abs=1e-7)
    assert 'mass_g' not in result['values']


def test_curved_bond_preserves_complete_quadratic_fields_and_rigid_motions(tmp_path):
    _,domain,study,case=reference(refined=False,curved=True)
    system=BondedAssemblySystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    system.qualify(tmp_path/'quality')
    # Translation/rotation and general linear Cartesian fields are reproduced
    # on the actual curved geometry, not a corner-only triangulation.
    a=np.array([[.7,-.2,.1],[.4,-.9,.8],[.2,.3,-.5]])
    field={n:a@np.array(p)+[2,3,4] for n,p in system.mesh.nodes.items()}
    for n,weights in system.bonding.relations.items():assert sum(w*field[m] for m,w in weights.items())==pytest.approx(field[n],abs=1e-10)
    assert any(any(w<-.01 for w in weights.values()) for weights in system.bonding.relations.values())
    result=bonded_solve(system,case,tmp_path/'assembly')
    assert result['checks']['residual']<1e-9
    assert result['checks']['force_balance_error']<1e-9
    assert result['checks']['moment_balance_error']<1e-9
    assert result['interfaces'][0]['coordinate_adjustment'] is False


def test_bond_does_not_hide_a_global_rigid_mechanism(tmp_path):
    project,domain,study,case=reference(refined=False);case.supports=[]
    with pytest.raises(ValueError,match='unrestrained rigid motion'):solve(request(project,domain,study),tmp_path)


def test_actual_gap_is_not_adjusted_or_extrapolated_into_a_bond(tmp_path):
    project,domain,study,_=reference(refined=False);domain.models[0].mesh=mesh_pair(geometry_key(project),gap=.001)
    with pytest.raises(ValueError,match='covered|projection'):solve(request(project,domain,study),tmp_path)


def test_dependent_support_conflict_and_cycle_refuse(tmp_path):
    project,domain,study,case=reference(refined=False)
    case.supports.append(Support(id='invalid-slave-support',region_id='second:xmin'))
    with pytest.raises(ValueError,match='direct support'):solve(request(project,domain,study),tmp_path/'support')
    case.supports.pop()
    study.interfaces.append(study.interfaces[0].model_copy(update={'id':'cycle','primary_region_id':'second:xmin','secondary_region_id':'first:xmax'}))
    with pytest.raises(ValueError,match='Cyclic'):solve(request(project,domain,study),tmp_path/'cycle')


def test_static_assembly_refuses_other_interface_physics(tmp_path):
    project,domain,study,_=reference(refined=False)
    study.interfaces=[Interface(id='old-spring',kind='bonded',node_pairs=[(1,2)],stiffness_n_mm=100,source='Declared finite spring, not a perfect surface bond')]
    with pytest.raises(ValueError,match='reinterpret'):solve(request(project,domain,study),tmp_path)


def test_actual_native_two_body_mesh_solver_and_handoff_keep_the_complete_bond(tmp_path):
    import hashlib,zipfile
    from .test_native_document import native_project
    from o3d.geometry import build
    from o3d.store import Store
    from o3d.domains.simulation import execute
    project=native_project([('solid.box',{'length':10,'width':2,'height':2,'body_name':'First'}),('solid.box',{'length':10,'width':2,'height':2,'corner':[10,0,0],'body_name':'Second'})])
    store=Store(tmp_path/'native');store.init(project);built=build(store,project)
    bodies={b['name']:b for b in built['scene']['bodies']};assert len(bodies)==2
    _,domain,study,case=reference(refined=False)
    selected=[('root','First',-1),('primary','First',1),('secondary','Second',-1),('tip','Second',1)]
    regions=[Region(id=role,body_id=bodies[name]['id'],selection=next(f['id'] for f in bodies[name]['faces'] if f.get('normal') and sign*f['normal'][0]>.99)) for role,name,sign in selected]
    model=domain.models[0];model.geometry_key=geometry_key(project);model.body_ids=[bodies[n]['id'] for n in ['First','Second']];model.regions=regions;model.mesh=None
    for assignment,name in zip(study.assignments,['First','Second']):assignment.body_id=bodies[name]['id']
    study.settings.probe_body_id=bodies['Second']['id'];case.supports[0].region_id='root';case.loads[0].region_id='tip';study.interfaces[0].primary_region_id='primary';study.interfaces[0].secondary_region_id='secondary'
    meshed=execute(store,project,'mesh',{'data':domain.model_dump(mode='json'),'mesh_size_mm':1.5,'refinements':2})
    saved=SimulationDomain.model_validate(meshed['data']);first=saved.models[0].mesh
    assert {e.body_id for e in first.elements}==set(model.body_ids)
    result=execute(store,project,'solve',{'data':saved.model_dump(mode='json')})
    actual=result['results'][0];assert actual['numerical_state']=='verified_numerically',actual['verification_gaps']
    assert actual['values']['probe_displacement_mm']==pytest.approx(.0025,rel=1e-8)
    assert actual['interfaces'][0]['secondary_resultant_n']==pytest.approx([-100,0,0],abs=1e-7)
    evidence=store.artifact(actual['interface_artifact']);assert hashlib.sha256(evidence.read_bytes()).hexdigest()==actual['interface_evidence']['sha256']
    handoff=execute(store,project,'handoff',{'data':saved.model_dump(mode='json')})
    with zipfile.ZipFile(store.artifact(handoff['artifact'])) as z:
        manifest=json.loads(z.read('manifest.json'));assert not manifest['exported_decks']
        for row in manifest['evidence_files']:assert hashlib.sha256(z.read(row['path'])).hexdigest()==row['sha256']
        assert any(p.endswith('/bonded/mortar-0-cross.npz') for p in z.namelist())
        assert any(p.endswith('/bonded/constraint-transformation.npz') for p in z.namelist())


def test_curved_graph_mortar_has_the_same_physics_after_world_rotation(tmp_path):
    from scipy.spatial.transform import Rotation
    project,domain,study,case=reference(refined=False,curved=True)
    first=solve(request(project,domain,study),tmp_path/'before')
    rotation=Rotation.from_euler('xyz',[23,31,17],degrees=True).as_matrix()
    mesh=domain.models[0].mesh;mesh.nodes={n:tuple(rotation@np.array(p)) for n,p in mesh.nodes.items()}
    case.loads[0].vector=tuple(rotation@np.array(case.loads[0].vector));study.settings.probe_point_mm=tuple(rotation@np.array(study.settings.probe_point_mm))
    after=solve(request(project,domain,study),tmp_path/'after')
    before=np.array(list(first['field']['displacements_mm'].values()))@rotation.T
    actual=np.array(list(after['field']['displacements_mm'].values()))
    assert np.max(abs(before-actual))/np.max(abs(before))<1e-8
    assert after['values']['strain_energy_n_mm']==pytest.approx(first['values']['strain_energy_n_mm'],rel=1e-9)
    assert after['checks']['moment_balance_error']<1e-9


def test_three_body_corner_chain_preserves_interface_forces_and_moments(tmp_path):
    # The second interface uses primary nodes that are themselves dependent on
    # the first interface, exercising actual recursive displacement/force transfer.
    project,domain,study,case=reference(refined=False)
    first=domain.models[0].mesh;third=block_mesh(geometry_key(project),origin=(10,2,0),divisions=(2,2,2),body_id='third',element_family='C3D10');offset=max(first.nodes);element_offset=len(first.elements)
    mesh=first.model_copy(update={'nodes':first.nodes|{n+offset:p for n,p in third.nodes.items()},'elements':first.elements+[e.model_copy(update={'id':e.id+element_offset,'nodes':tuple(n+offset for n in e.nodes)}) for e in third.elements],
        'regions':first.regions+[r.model_copy(update={'node_ids':[n+offset for n in r.node_ids],'triangles':[tuple(n+offset for n in t) for t in r.triangles]}) for r in third.regions]})
    domain.models[0].mesh=mesh;domain.models[0].body_ids.append('third');study.assignments.append(BodyAssignment(body_id='third',material_id='synthetic'))
    study.interfaces.append(Interface(id='corner',kind='bonded',formulation='quadratic_surface_mortar',primary_region_id='second:ymax',secondary_region_id='third:ymin',position_tolerance_mm=1e-7,source='Declared synthetic perfect corner bond'))
    study.settings.probe_point_mm=(20,3,1);study.settings.probe_body_id='third';case.loads[0].region_id='third:xmax';case.loads[0].vector=(0,0,-10)
    system=BondedAssemblySystem(mesh,study,{m.id:m for m in domain.materials});system.qualify(tmp_path/'quality')
    result=bonded_solve(system,case,tmp_path/'assembly')
    assert any(m in system.bonding.relations for n,weights in system.bonding.relations.items() for m in weights)
    assert result['checks']['residual']<1e-8
    assert result['checks']['force_balance_error']<1e-8
    assert result['checks']['moment_balance_error']<1e-8
    assert result['checks']['energy_balance_error']<1e-8
    for interface in result['interfaces']:
        assert interface['secondary_resultant_n']==pytest.approx([0,0,10],abs=1e-7)
        assert interface['secondary_moment_n_mm']==pytest.approx([30,-200,0],abs=1e-6)
        assert interface['action_reaction_moment_error_n_mm']<1e-7
