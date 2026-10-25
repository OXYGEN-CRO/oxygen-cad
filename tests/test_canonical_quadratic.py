"""Canonical ten-node transfer: actual solver/mesher and independent mechanics."""
import json

import numpy as np
import pytest
from pydantic import ValidationError

from o3d.model import Project, geometry_key, Profile, Extrude
from o3d.simulation_models import (AnalysisModel, BodyAssignment, Element, Load,
    LoadCase, MaterialRecord, MeshRecord, Region, SimulationDomain, StudyRecord,
    Support, Interface)
from o3d.simulation_mesh import block_mesh, validate_boundary, display_triangles
from o3d.simulation_quadratic import QuadraticSystem
from o3d.simulation_worker import solve
from o3d.simulation_fem import System
from o3d.domains.simulation import execute, input_key
from o3d.store import Store


def reference(divisions=(2,1,1),kind="linear_static"):
    project=Project();key=geometry_key(project)
    mesh=block_mesh(key,divisions=divisions,element_family="C3D10")
    material=MaterialRecord(id="synthetic",name="Declared numerical material",source="Synthetic SI benchmark, no grade or physical calibration",youngs_modulus_mpa=200000,poisson_ratio=.3,density_g_cm3=7.8)
    case=LoadCase(id="pull",name="Uniform axial reference",supports=[Support(id="x",region_id="part:xmin",kind="partial",components=(True,False,False)),Support(id="y",region_id="part:ymin",kind="partial",components=(False,True,False)),Support(id="z",region_id="part:zmin",kind="partial",components=(False,False,True))],loads=[Load(id="traction",region_id="part:xmax",vector=(100,0,0),source="Declared analytical test force")])
    study=StudyRecord(id="study",name="Canonical P2 reference",type=kind,model_id="model",assignments=[BodyAssignment(body_id="part",material_id=material.id)],cases=[case],assumptions_confirmed=True)
    study.settings.probe_point_mm=(10,1,1)
    model=AnalysisModel(id="model",name="Reference block",geometry_key=key,mesh=mesh)
    domain=SimulationDomain(models=[model],materials=[material],studies=[study],active_study_id=study.id)
    return project,domain,study,case


def request(project,domain,study):
    return {"data":domain.model_dump(mode="json"),"study_id":study.id,"case_id":study.cases[0].id,"input_key":input_key(project,domain,study),"geometry_key":geometry_key(project),"revision":project.revision}


def two_body_mesh(key,divisions):
    first=block_mesh(key,divisions=divisions,element_family="C3D10")
    second=block_mesh(key,divisions=divisions,body_id="second",origin=(20,0,0),element_family="C3D10")
    offset=max(first.nodes)
    nodes={**first.nodes,**{n+offset:p for n,p in second.nodes.items()}}
    elements=[*first.elements,*[Element(id=e.id+len(first.elements),body_id=e.body_id,nodes=tuple(n+offset for n in e.nodes)) for e in second.elements]]
    regions=[*first.regions,*[Region(**{**r.model_dump(),"node_ids":[n+offset for n in r.node_ids],"triangles":[tuple(n+offset for n in t) for t in r.triangles]}) for r in second.regions]]
    return MeshRecord(geometry_key=key,nodes=nodes,elements=elements,regions=regions,element_family="C3D10",source="Two separate explicit numerical blocks; no geometric bond inferred")


def test_quadratic_connectivity_and_boundary_order_cannot_fall_back_to_corners():
    _,domain,study,_=reference()
    mesh=domain.models[0].mesh
    assert all(len(e.nodes)==10 for e in mesh.elements)
    assert all(len(t)==6 for r in mesh.regions for t in r.triangles)
    validate_boundary(mesh)
    with pytest.raises(ValueError,match="requires C3D4"):
        System(mesh,study,{m.id:m for m in domain.materials})
    changed=mesh.model_dump();changed["element_family"]="C3D4"
    with pytest.raises(ValidationError):MeshRecord.model_validate(changed)
    changed=mesh.model_copy(deep=True)
    t=changed.regions[0].triangles[0]
    changed.regions[0].triangles[0]=(*t[:3],t[4],t[3],t[5])
    with pytest.raises(ValueError,match="ordering"):validate_boundary(changed)
    mids={n for e in mesh.elements for n in e.nodes[4:]}
    shown={n for t in display_triangles(mesh) for n in t}
    assert mids & shown
    assert len(display_triangles(mesh))==4*len(__import__('o3d.simulation_mesh',fromlist=['exterior_triangles']).exterior_triangles(mesh.nodes,mesh.elements))


def test_consistent_quadratic_force_pressure_gravity_and_remote_moment():
    _,domain,study,case=reference()
    system=QuadraticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    force=system.loads(case).reshape(-1,3)
    assert force.sum(axis=0)==pytest.approx([100,0,0],abs=1e-12)
    assert np.cross(system.xyz,force).sum(axis=0)==pytest.approx([0,100,-100],abs=1e-11)
    # Affine quadratic face corners have zero uniform-traction weights.
    corners={n for e in system.mesh.elements for n in e.nodes[:4]}
    assert max(np.linalg.norm(force[system.index[n]]) for n in corners)<1e-12
    case.loads=[Load(id="pressure",kind="pressure",region_id="part:xmax",magnitude=25,source="25 MPa on 4 mm² reference")]
    assert system.loads(case).reshape(-1,3).sum(axis=0)==pytest.approx([-100,0,0],abs=1e-11)
    case.loads=[Load(id="weight",kind="gravity",vector=(0,0,-9806.65),source="SI weight reference")]
    weight=system.loads(case).reshape(-1,3)
    assert weight.sum(axis=0)==pytest.approx([0,0,-7800*(.010*.002*.002)*9.80665],abs=1e-15)
    assert any(weight[system.index[n],2]>0 for n in corners)  # genuine signed body weights
    case.loads=[Load(id="remote",kind="remote",region_id="part:xmax",vector=(3,4,5),axis=(6,7,8),origin_mm=(1,2,3),source="Conservative moment test")]
    force=system.loads(case).reshape(-1,3)
    assert force.sum(axis=0)==pytest.approx([3,4,5],abs=1e-12)
    assert np.cross(system.xyz-[1,2,3],force).sum(axis=0)==pytest.approx([6,7,8],abs=1e-11)


def test_canonical_quad_axial_solution_quality_probe_and_mesh_refinement(tmp_path):
    project,domain,study,_=reference()
    domain.models[0].refinements=[block_mesh(geometry_key(project),divisions=(4,2,2),element_family="C3D10")]
    result=solve(request(project,domain,study),tmp_path)
    # Independent SI uniaxial stress/strain and Poisson contraction at (10,1,1).
    stress_pa=100/(.002*.002);strain=stress_pa/200e9
    expected_m=np.array([strain*.010,-.3*strain*.001,-.3*strain*.001])
    actual=result["displacement_probe"]["final"]
    assert np.array(actual["displacement_mm"])/1000==pytest.approx(expected_m,rel=2e-6,abs=1e-12)
    # Nodal extrapolation/recovery has finite precision; displacement and mass
    # above/below retain their tighter independent tolerances.
    assert result["values"]["max_von_mises_mpa"]==pytest.approx(stress_pa/1e6,rel=3e-5)
    assert result["values"]["mass_g"]==pytest.approx(.312,rel=1e-12)
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]
    assert result["checks"]["mesh_converged"]
    assert "probe_displacement_mm" in result["checks"]["converged_quantities"]
    assert result["mesh_quality"]["curved_jacobian_checked"]
    assert result["mesh_quality"]["integrated_volume_mm3"]==pytest.approx(40,rel=1e-10)
    assert result["field_location"]["stress"]=="nodal_extrapolated"
    assert result["physical_validation"]=="not_supplied"
    deck=(tmp_path/"mesh-1/linear/analysis.inp").read_text()
    assert "*ELEMENT,TYPE=C3D10" in deck and "*ELEMENT,TYPE=C3D4" not in deck
    assert all(len(e)==10 for e in result["field"]["element_connectivity"].values())


def test_missing_physical_probe_stays_inconclusive_and_outside_probe_refuses(tmp_path):
    project,domain,study,_=reference()
    study.settings.probe_point_mm=None
    result=solve(request(project,domain,study),tmp_path/"missing")
    assert result["numerical_state"]=="inconclusive"
    assert "fixed physical displacement probe not supplied" in result["verification_gaps"]
    study.settings.probe_point_mm=(100,1,1)
    with pytest.raises(ValueError,match="probe could not be located"):
        solve(request(project,domain,study),tmp_path/"outside")


def test_unrestrained_quad_component_refuses_before_solver(tmp_path):
    _,domain,study,case=reference()
    case.supports=[Support(id="one-direction",region_id="part:xmin",kind="partial",components=(True,False,False))]
    system=QuadraticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    from o3d.extended_solver import write_calculix
    with pytest.raises(ValueError,match="rigid body modes"):
        write_calculix(tmp_path/"invalid.inp",system,case,nonlinear=False)
    assert not (tmp_path/"invalid.inp").exists()


def test_native_step_quad_mesh_keeps_cad_region_area_volume_and_material(tmp_path):
    project,domain,study,case=reference()
    project=Project(geometry_engine="occt",features=[Profile(id="profile",width=2,height=2,plane="YZ"),Extrude(id="part",profile="profile",distance=10)])
    domain.models[0].geometry_key=geometry_key(project);domain.models[0].mesh=None
    case.supports=[Support(id="root",region_id="part:xmin")]
    store=Store(tmp_path/"actual-geometry");store.init(project)
    meshed=execute(store,project,"mesh",{"data":domain.model_dump(mode="json"),"element_family":"C3D10","mesh_size_mm":1.5,"refinements":2})
    saved=SimulationDomain.model_validate(meshed["data"])
    for mesh in [saved.models[0].mesh,*saved.models[0].refinements]:
        assert mesh.element_family=="C3D10"
        assert mesh.cad_volumes_mm3["part"]==pytest.approx(40,rel=1e-12)
        assert all(len(e.nodes)==10 for e in mesh.elements)
        assert all(len(t)==6 for r in mesh.regions for t in r.triangles)
        for r in mesh.regions:
            assert all(mesh.nodes[n][0]==pytest.approx(0 if r.id.endswith("min") else 10,abs=1e-8) for t in r.triangles for n in t)
    result=solve(request(project,saved,saved.studies[0]),tmp_path/"actual-solve")
    assert result["mesh_quality"]["body_volume_comparisons"]["part"]["relative_error"]<1e-8
    assert result["checks"]["force_balance_error"]<1e-6
    # Audit and result use exactly the coordinate values parsed from the deck,
    # including tiny negative values that used to exceed the F20.0 field width.
    deck=(tmp_path/"actual-solve/mesh-1/linear/analysis.inp").read_text().splitlines()
    start=deck.index("*NODE")+1;parsed={}
    for line in deck[start:]:
        if line.startswith("*"):break
        tokens=line.split(",");assert all(len(t)<=20 for t in tokens[1:])
        parsed[int(tokens[0])]=[float(v) for v in tokens[1:]]
    audited=json.loads((tmp_path/"actual-solve/mesh-1/quality/mesh.json").read_text())
    assert parsed=={int(n):p for n,p in audited["nodes"].items()}==result["field"]["nodes"]


def test_two_bodies_retain_distinct_materials_and_physical_probe_identity(tmp_path):
    project,domain,study,case=reference()
    model=domain.models[0];model.body_ids=["part","second"]
    model.mesh=two_body_mesh(geometry_key(project),(2,1,1))
    model.refinements=[two_body_mesh(geometry_key(project),(4,2,2))]
    domain.materials.append(domain.materials[0].model_copy(update={"id":"alternate","youngs_modulus_mpa":100000,"density_g_cm3":2.7,"source":"Second declared synthetic material"}))
    study.assignments.append(BodyAssignment(body_id="second",material_id="alternate"))
    for axis in range(3):
        case.supports.append(Support(id=f"second-{axis}",region_id=f"second:{'xyz'[axis]}min",kind="partial",components=tuple(i==axis for i in range(3))))
    case.loads.append(Load(id="second-force",region_id="second:xmax",vector=(100,0,0),source="Second explicit test force"))
    study.settings.probe_point_mm=(30,1,1);study.settings.probe_body_id="second"
    result=solve(request(project,domain,study),tmp_path)
    assert result["values"]["mass_g"]==pytest.approx(.312+.108,rel=1e-12)
    assert result["displacement_probe"]["final"]["body_id"]=="second"
    assert result["displacement_probe"]["final"]["displacement_mm"][0]==pytest.approx(100*.010/(.002*.002*100e9)*1000,rel=2e-6)
    assert result["mesh_quality"]["connected_components"]==2
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]
    deck=(tmp_path/"mesh-1/linear/analysis.inp").read_text()
    assert "MATERIAL=MAT0" in deck and "MATERIAL=MAT1" in deck


def test_canonical_quad_plastic_unload_keeps_peak_stress_and_probe_history(tmp_path):
    project,domain,study,case=reference(kind="nonlinear_static")
    domain.models[0].refinements=[block_mesh(geometry_key(project),divisions=(4,2,2),element_family="C3D10")]
    material=domain.materials[0];material.plastic_curve=[(250,0),(350,.01)];material.plastic_source="Synthetic analytical hardening law, no real calibration"
    case.loads[0].vector=(1200,0,0);case.history=[(0,0),(1,1),(2,0)]
    result=solve(request(project,domain,study),tmp_path)
    final=result["field"]["displacements_mm"]
    assert max(v[0] for v in final.values())==pytest.approx(.05,rel=2e-5)
    assert result["values"]["max_von_mises_mpa"]==pytest.approx(300,rel=3e-5)
    assert result["values"]["final_von_mises_mpa"]<.01
    assert result["displacement_probe"]["peak"]["time_s"]<result["displacement_probe"]["final"]["time_s"]
    assert result["values"]["probe_displacement_mm"]>result["displacement_probe"]["final"]["magnitude_mm"]
    assert max(row["force_balance_error"] for row in result["checks"]["increment_equilibrium"])<1e-6
    assert result["field"]["field_state"]=="final"


def test_canonical_quad_audit_refuses_a_fold_missed_by_corner_checks(tmp_path):
    from o3d.quadratic import EDGES,FACES
    project,domain,study,_=reference()
    corners=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])
    points=np.vstack([corners,[(corners[a]+corners[b])/2 for a,b in EDGES]])
    nodes={i+1:(p[0]*(1+2.5*p[1]),p[1]+3.4*p[0]*(1-p[0]),p[2]) for i,p in enumerate(points)}
    mesh=MeshRecord(geometry_key=geometry_key(project),nodes=nodes,elements=[Element(id=1,nodes=tuple(nodes))],regions=[Region(id="face",triangles=[tuple(i+1 for i in FACES[0])])],element_family="C3D10",source="Explicit folded quadratic mapping regression")
    context=QuadraticSystem(mesh,study,{m.id:m for m in domain.materials})
    with pytest.raises(ValueError,match="curved Jacobian"):
        context.qualify(tmp_path)


@pytest.mark.parametrize("value",[-1.7763568394002505e-15,-1.7976931348623157e308,1e-300,1.234567890123456,0.])
def test_calculix_numeric_tokens_keep_the_complete_exponent(value):
    from o3d.solver import calculix_number
    token=calculix_number(value)
    assert len(token)<=20
    assert float(token)==pytest.approx(value,rel=1e-12,abs=0.)


def test_actual_solidcraft_curved_tube_uses_native_body_regions_and_quadratic_geometry(tmp_path,mesh_families):
    from o3d.geometry import build
    from o3d.quadratic import EDGES
    _,domain,study,case=reference()
    project=mesh_families['tube'].model_copy(update={"geometry_engine":"solidcraft"})
    store=Store(tmp_path/'native-tube');store.init(project)
    built=build(store,project);body=built['scene']['bodies'][0]
    assert len(built['scene']['bodies'])==1
    root=next(f for f in body['faces'] if f.get('normal') and f['normal'][0]<-.99)
    tip=next(f for f in body['faces'] if f.get('normal') and f['normal'][0]>.99)
    model=domain.models[0];model.geometry_key=geometry_key(project);model.body_ids=[body['id']];model.mesh=None
    model.regions=[Region(id='root',body_id=body['id'],selection=root['id']),Region(id='tip',body_id=body['id'],selection=tip['id'])]
    study.assignments=[BodyAssignment(body_id=body['id'],material_id=domain.materials[0].id)]
    study.settings.probe_body_id=body['id'];study.settings.probe_point_mm=(40,7,0)
    case.supports=[Support(id='clamp',region_id='root')]
    case.loads=[Load(id='axial',region_id='tip',vector=(100,0,0),source='Declared native-tube numerical load')]
    meshed=execute(store,project,'mesh',{'data':domain.model_dump(mode='json'),'element_family':'C3D10','mesh_size_mm':4,'refinements':2})
    accepted=[a for a in meshed['meshing_attempts'] if a['accepted']]
    assert len(accepted)==2
    assert accepted[1]['nodes']>accepted[0]['nodes'] and accepted[1]['elements']>accepted[0]['elements']
    assert accepted[1]['max_element_volume_mm3']<accepted[0]['max_element_volume_mm3']*(1-1e-6)
    data=SimulationDomain.model_validate(meshed['data']);mesh=data.models[0].mesh
    assert {e.body_id for e in mesh.elements}=={body['id']}
    deviation=max(np.linalg.norm(np.array(mesh.nodes[e.nodes[i+4]])-(np.array(mesh.nodes[e.nodes[a]])+np.array(mesh.nodes[e.nodes[b]]))/2) for e in mesh.elements for i,(a,b) in enumerate(EDGES))
    assert deviation>1e-4  # Actual curved midsides, not elevated straight cells.
    result=solve(request(project,data,data.studies[0]),tmp_path/'native-tube-solve')
    expected_volume=np.pi*(.008**2-.006**2)*.040*1e9
    assert result['mesh_quality']['integrated_volume_mm3']==pytest.approx(expected_volume,rel=.01)
    assert result['mesh_quality']['curved_jacobian_checked']
    assert result['checks']['force_balance_error']<1e-6
    assert result['checks']['moment_balance_error']<1e-6
    assert result['displacement_probe']['final']['point_mm']==[40,7,0]
    assert result['displacement_probe']['final']['body_id']==body['id']
    assert result['displacement_probe']['final']['displacement_mm'][0]>0


def test_canonical_quad_handoff_contains_exact_finest_deck_fields_and_quality(tmp_path):
    import hashlib,zipfile
    project,domain,study,_=reference()
    domain.models[0].refinements=[block_mesh(geometry_key(project),divisions=(4,2,2),element_family="C3D10")]
    project.domains['simulation']=domain.model_dump(mode='json')
    store=Store(tmp_path/'project');store.init(project)
    result=execute(store,project,'solve',{})
    assert result['results'][0]['numerical_state']=='verified_numerically'
    exported=execute(store,project,'handoff',{})
    with zipfile.ZipFile(store.artifact(exported['artifact'])) as package:
        manifest=json.loads(package.read('manifest.json'))
        assert manifest['analysis_mesh_included'] and manifest['geometry_included'] is False
        provenance=manifest['deck_provenance']['pull']
        assert provenance['kind']=='exact executed finest-mesh deck'
        assert len([name for name in package.namelist() if name.endswith('curved-quality.json')])==2
        assert any(name.endswith('field.json') for name in package.namelist())
        deck_name=next(name for name in package.namelist() if name.startswith('calculix/') and name.endswith('.inp'))
        assert package.read(deck_name)==package.read(provenance['source'])
        assert 'mesh-1/linear/analysis.inp' in provenance['source']
        for row in manifest['evidence_files']:
            assert hashlib.sha256(package.read(row['path'])).hexdigest()==row['sha256']
        field_name=next(name for name in package.namelist() if name.endswith('field.json'))
        field=json.loads(package.read(field_name))
        assert all(len(e)==10 for e in field['element_connectivity'].values())
    assert not list(store.artifact(exported['artifact']).parent.glob('*.partial'))


def test_canonical_quad_rejects_untranslated_conditions_without_fallback(tmp_path):
    from o3d.domains.simulation import preflight
    project,domain,study,case=reference()
    study.interfaces=[Interface(id='untranslated',kind='bonded',node_pairs=[(1,2)],stiffness_n_mm=1000,source='Explicit test interface')]
    errors=preflight(project,domain,study,domain.models[0])
    assert any('no condition will be discarded' in e for e in errors)
    with pytest.raises(ValueError,match='no condition will be discarded'):
        execute(Store(tmp_path/'unused'),project,'solve',{'data':domain.model_dump(mode='json')})


def test_linear_reference_uses_the_same_declared_physical_probe(tmp_path):
    project,domain,study,_=reference()
    domain.models[0].mesh=block_mesh(geometry_key(project),divisions=(2,1,1))
    domain.models[0].refinements=[block_mesh(geometry_key(project),divisions=(4,2,2))]
    result=solve(request(project,domain,study),tmp_path)
    expected=np.linalg.norm([.00125,-.0000375,-.0000375])
    assert result['values']['probe_displacement_mm']==pytest.approx(expected,rel=1e-10)
    assert result['displacement_probe']['final']['point_mm']==[10,1,1]
    assert 'probe_displacement_mm' in result['checks']['converged_quantities']


def test_point_connected_quadratic_elements_do_not_create_a_solid_load_path():
    from o3d.quadratic import EDGES
    project,domain,study,_=reference()
    corners=[np.array([[0.,0.,0.],[1,0,0],[0,1,0],[0,0,1]]),np.array([[0.,0.,0.],[-1,0,0],[0,0,-1],[0,-1,0]])]
    nodes={1:(0.,0.,0.)};elements=[]
    for i,vertices in enumerate(corners):
        points=np.vstack([vertices,[(vertices[a]+vertices[b])/2 for a,b in EDGES]])
        ids=[1]
        for point in points[1:]:
            n=max(nodes)+1;nodes[n]=tuple(point);ids.append(n)
        elements.append(Element(id=i+1,nodes=tuple(ids)))
    mesh=MeshRecord(geometry_key=geometry_key(project),nodes=nodes,elements=elements,element_family='C3D10',source='Two tetrahedra touching at one point, not a solid interface')
    with pytest.raises(ValueError,match='points or edges'):
        QuadraticSystem(mesh,study,{m.id:m for m in domain.materials})


def test_quality_audit_maps_internal_indices_to_actual_solver_element_labels(tmp_path):
    from o3d.extended_solver import calculix_solve
    _,domain,study,case=reference()
    mesh=domain.models[0].mesh
    for e in mesh.elements:e.id*=10
    context=QuadraticSystem(mesh,study,{m.id:m for m in domain.materials})
    quality=context.qualify(tmp_path/'quality')
    assert quality['audit_index_to_solver_element_id']=={str(i+1):e.id for i,e in enumerate(mesh.elements)}
    result=calculix_solve(context,case,tmp_path/'solve',nonlinear=False)
    assert result['displacement_probe']['final']['displacement_mm'][0]==pytest.approx(.00125,rel=2e-6)


@pytest.mark.parametrize('family',['C3D4','C3D10'])
def test_sparse_labels_cannot_bypass_the_external_solver_resource_budget(tmp_path,family):
    from o3d.extended_solver import write_calculix
    project,domain,study,case=reference()
    mesh=block_mesh(geometry_key(project),divisions=(2,1,1),element_family=family)
    data=mesh.model_dump();offset=100000
    data['nodes']={n+offset:p for n,p in mesh.nodes.items()}
    for element in data['elements']:element['nodes']=[n+offset for n in element['nodes']]
    for region in data['regions']:
        region['node_ids']=[n+offset for n in region['node_ids']]
        region['triangles']=[[n+offset for n in t] for t in region['triangles']]
    mesh=MeshRecord.model_validate(data)
    materials={m.id:m for m in domain.materials}
    if family=='C3D10':
        with pytest.raises(ValueError,match='label budget'):QuadraticSystem(mesh,study,materials)
    else:
        system=System(mesh,study,materials)
        with pytest.raises(ValueError,match='label budget'):write_calculix(tmp_path/'unsafe.inp',system,case,nonlinear=False)
    assert not (tmp_path/'unsafe.inp').exists()
