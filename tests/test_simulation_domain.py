"""Numerical cases for the explicit simulation scope; never use a live project."""
import copy
import json
import math
import threading
import zipfile
from pathlib import Path
import numpy as np
import pytest
from pydantic import ValidationError
from o3d.model import Project,geometry_key
from o3d.store import Store
from o3d.simulation_models import SimulationDomain,MaterialRecord,StudyRecord,LoadCase,Support,Load,ThermalBoundary,AnalysisModel,BodyAssignment,Interface,Bolt,SearchSettings,Limit
from o3d.simulation_mesh import block_mesh
from o3d.simulation_fem import System,static_solve,thermal_solve,modal_solve,buckling_solve,event_solve
from o3d.domains.simulation import execute,validate,input_key
from o3d.simulation_worker import solve
from o3d.solver import Cancelled


def fixture(divisions=(4,2,2),kind="linear_static"):
    project=Project()
    key=geometry_key(project)
    mesh=block_mesh(key,divisions=divisions)
    material=MaterialRecord(id="test",name="Synthetic analytical reference",source="Test definition, not a commercial material card",youngs_modulus_mpa=200000,poisson_ratio=.3,density_g_cm3=7.8,conductivity_w_mk=200,expansion_per_k=1.2e-5)
    case=LoadCase(id="axial",name="Axial analytical reference",supports=[Support(id="sx",region_id="part:xmin",kind="partial",components=(True,False,False)),Support(id="sy",region_id="part:ymin",kind="partial",components=(False,True,False)),Support(id="sz",region_id="part:zmin",kind="partial",components=(False,False,True))],loads=[Load(id="pull",region_id="part:xmax",vector=(100,0,0),source="Synthetic analytical test")])
    study=StudyRecord(id="study",name="Numerical reference",type=kind,model_id="model",assignments=[BodyAssignment(body_id="part",material_id="test")],cases=[case],assumptions_confirmed=True)
    model=AnalysisModel(id="model",name="Explicit reference block",geometry_key=key,mesh=mesh)
    domain=SimulationDomain(models=[model],materials=[material],studies=[study],active_study_id="study")
    return project,domain,study,case,System(mesh,study,{material.id:material})


def test_axial_displacement_stress_reaction_and_strain_energy():
    _,_,_,case,system=fixture()
    result=static_solve(system,case)
    # Free lateral contraction under uniform uniaxial traction.
    axial=max(v[0] for v in result["field"]["displacements_mm"].values())
    assert axial==pytest.approx(100*10/(4*200000),rel=1e-10)
    assert result["values"]["max_von_mises_mpa"]==pytest.approx(25,rel=1e-10)
    assert result["values"]["strain_energy_n_mm"]==pytest.approx(.5*100*axial,rel=1e-10)
    assert result["checks"]["residual"]<1e-11
    assert result["checks"]["force_balance_error"]<1e-11
    assert result["checks"]["moment_balance_error"]<1e-11


def test_conservative_remote_force_and_moment():
    _,_,_,case,system=fixture()
    case.loads=[Load(id="moment",kind="moment",region_id="part:xmax",vector=(3,4,5),origin_mm=(1,2,3),source="Test")]
    f=system.loads(case).reshape(-1,3)
    assert f.sum(axis=0)==pytest.approx([0,0,0],abs=1e-12)
    assert np.cross(system.xyz-np.array([1,2,3]),f).sum(axis=0)==pytest.approx([3,4,5],abs=1e-12)
    case.loads=[Load(id="remote",kind="remote",region_id="part:xmax",vector=(7,8,9),axis=(3,4,5),origin_mm=(1,2,3),source="Test")]
    f=system.loads(case).reshape(-1,3)
    assert f.sum(axis=0)==pytest.approx([7,8,9],abs=1e-11)
    assert np.cross(system.xyz-np.array([1,2,3]),f).sum(axis=0)==pytest.approx([3,4,5],abs=1e-11)


def test_thermal_conduction_and_free_expansion():
    _,_,study,case,system=fixture(kind="thermal_stress")
    case.thermal=[ThermalBoundary(id="cold",kind="temperature",region_id="part:xmin",value=300,source="Test"),ThermalBoundary(id="hot",kind="temperature",region_id="part:xmax",value=400,source="Test")]
    thermal=thermal_solve(system,case)
    for node,t in thermal["field"]["temperature_k"].items():assert t==pytest.approx(300+system.mesh.nodes[node][0]*10,abs=1e-8)
    assert thermal["checks"]["residual"]<1e-10
    # Uniform free thermal expansion: zero stress and alpha*dT*L expansion.
    case.loads=[];case.thermal=[ThermalBoundary(id="uniform",kind="temperature",region_id="part:xmin",value=400,source="Test")]
    thermal=thermal_solve(system,case)
    study.stress_free_temperature_k=300
    result=static_solve(system,case,temperatures=thermal["temperature"])
    assert result["values"]["max_von_mises_mpa"]<1e-7
    assert max(v[0] for v in result["field"]["displacements_mm"].values())==pytest.approx(.012,rel=1e-8)


def test_convection_and_radiation_power_balance():
    _,_,study,case,system=fixture(kind="steady_thermal")
    case.thermal=[ThermalBoundary(id="power",kind="heat_power",region_id="part:xmin",value=2,source="Test"),ThermalBoundary(id="sink",kind="convection",region_id="part:xmax",value=1000,ambient_k=300,source="Test")]
    result=thermal_solve(system,case)
    assert result["values"]["max_temperature_k"]==pytest.approx(825,rel=1e-9)
    assert result["values"]["heat_rejected_w"]==pytest.approx(2,rel=1e-10)
    assert result["checks"]["heat_balance_error"]<1e-9
    case.thermal[-1]=ThermalBoundary(id="sink",kind="radiation",region_id="part:xmax",value=.8,ambient_k=300,source="Test")
    result=thermal_solve(system,case)
    assert result["values"]["heat_rejected_w"]==pytest.approx(2,rel=1e-8)
    assert result["checks"]["heat_balance_error"]<1e-8


def test_modal_residual_mass_orthogonality_and_analytic_axial_mode():
    _,_,study,case,system=fixture(divisions=(12,1,1),kind="modal")
    # Suppress transverse components throughout explicit reference mesh for an axial rod test.
    system.mesh.regions.append(__import__('o3d.simulation_models',fromlist=['Region']).Region(id="all",node_ids=system.ids))
    system.regions["all"]=system.mesh.regions[-1]
    case.supports=[case.supports[0],Support(id="transverse",region_id="all",kind="partial",components=(False,True,True))]
    # nu=0 removes lateral coupling and makes the one-dimensional reference exact in limit.
    system.assignments["part"].poisson_ratio=0
    system=System(system.mesh,study,system.materials)
    result=modal_solve(system,case)
    wave=math.sqrt(200000/(7.8e-12))
    expected=wave/(4*10)
    assert result["values"]["min_frequency_hz"]==pytest.approx(expected,rel=.01)
    assert result["checks"]["residual"]<1e-9
    assert result["checks"]["mass_orthogonality_error"]<1e-10


def test_free_modes_and_singular_static_refusal():
    _,_,study,case,system=fixture(divisions=(2,1,1),kind="modal")
    case.supports=[];study.settings.mode_count=8
    result=modal_solve(system,case)
    assert sum(m["rigid_body"] for m in result["modes"])==6
    with pytest.raises(ValueError,match="supports"):static_solve(system,case)
    case.supports=[Support(id="one",region_id="part:xmin",kind="partial",components=(True,False,False))]
    with pytest.raises(ValueError,match="rigid|singular"):static_solve(system,case)


def test_inertial_relief_explicit_acceleration():
    _,_,study,case,system=fixture()
    case.supports=[];study.settings.inertial_relief=True
    result=static_solve(system,case)
    acceleration=result["checks"]["inertial_relief"]["translation_acceleration_mm_s2"]
    assert acceleration[0]==pytest.approx(100/(40*7.8e-12))
    assert result["checks"]["residual"]<1e-8
    assert result["checks"]["force_balance_error"]<1e-8


def test_dynamic_energy_timestep_and_rate_are_measured():
    _,_,study,case,system=fixture(divisions=(2,1,1),kind="dynamic_event")
    study.settings.duration_s=.00005
    study.settings.max_time_steps=20000
    case.history=[(0,0),(.00001,1),(.00005,1)]
    result=event_solve(system,case)
    refined=event_solve(system,case,step_scale=.5)
    assert refined["checks"]["time_steps"]>result["checks"]["time_steps"]
    assert refined["checks"]["energy_balance_error"]<.02
    assert abs(result["values"]["final_displacement_mm"]-refined["values"]["final_displacement_mm"])/refined["values"]["final_displacement_mm"]<.05
    study.settings.time_step_s=1
    with pytest.raises(ValueError,match="stability"):event_solve(system,case)


def test_revision_snapshot_convergence_and_stale_result(tmp_path):
    project,domain,study,_,_=fixture(divisions=(2,1,1))
    domain.models[0].refinements=[block_mesh(geometry_key(project),divisions=(4,2,2))]
    directory=tmp_path/"worker";directory.mkdir()
    key=input_key(project,domain,study)
    request={"data":domain.model_dump(mode="json"),"study_id":study.id,"case_id":"axial","input_key":key,"geometry_key":geometry_key(project),"revision":project.revision}
    result=solve(request,directory)
    assert result["numerical_state"]=="verified_numerically"
    assert result["physical_validation"]=="not_supplied"
    assert result["checks"]["mesh_converged"]
    assert result["input_key"]==key
    domain.materials[0].youngs_modulus_mpa*=.5
    assert input_key(project,domain,study)!=key
    domain.models[0].mesh.geometry_key="old"
    from o3d.domains.simulation import preflight
    assert any("stale" in e for e in preflight(project,domain,study,domain.models[0]))


def test_domain_schema_scope_and_clone(tmp_path):
    project,domain,study,case,_=fixture()
    store=Store(tmp_path/"project");store.init(project)
    data=domain.model_dump(mode="json")
    assert len(execute(store,project,"capabilities",{"data":data})["capabilities"])==20
    cloned=execute(store,project,"clone_study",{"data":data,"new_id":"alternate"})
    assert cloned["changed"] and len(cloned["data"]["studies"])==2
    assert len(data["studies"])==1
    invalid=copy.deepcopy(data);invalid["materials"][0]["density_g_cm3"]=-1
    with pytest.raises(ValidationError):validate(invalid)
    invalid=copy.deepcopy(data);invalid["studies"][0]["assignments"][0]["material_id"]="invented"
    with pytest.raises(ValidationError,match="missing material"):validate(invalid)
    study.type="injection_molding"
    with pytest.raises(ValueError,match="unavailable"):execute(store,project,"solve",{"data":domain.model_dump(mode="json")})


def test_cancel_and_nonphysical_inputs_refuse():
    _,_,_,case,system=fixture()
    event=threading.Event();event.set()
    with pytest.raises(Cancelled):static_solve(system,case,event)
    bad=system.mesh.model_dump(mode="json");bad["elements"][0]["nodes"]=[1,1,2,3]
    with pytest.raises(ValidationError,match="repeated"):__import__('o3d.simulation_models',fromlist=['MeshRecord']).MeshRecord.model_validate(bad)


def test_calculix_independent_linear_and_plastic_reference(tmp_path):
    from o3d.extended_solver import calculix_solve
    _,domain,study,case,system=fixture(divisions=(2,1,1),kind="nonlinear_static")
    linear=static_solve(system,case)
    external=calculix_solve(system,case,tmp_path/"linear",nonlinear=False)
    assert external["values"]["max_displacement_mm"]==pytest.approx(linear["values"]["max_displacement_mm"],rel=1e-6)
    assert external["checks"]["force_balance_error"]<1e-6
    material=domain.materials[0]
    material.plastic_curve=[(250,0),(350,.01)];material.plastic_source="Synthetic J2 test curve, no physical calibration"
    case.loads[0].vector=(1200,0,0)
    plastic=calculix_solve(system,case,tmp_path/"plastic")
    # sigma=300 MPa gives plastic strain .005 plus elastic strain .0015.
    assert max(u[0] for u in plastic["field"]["displacements_mm"].values())==pytest.approx(.065,rel=1e-5)
    assert plastic["values"]["max_equivalent_plastic_strain"]==pytest.approx(.005,rel=1e-5)
    assert plastic["checks"]["force_balance_error"]<1e-8
    assert plastic["checks"]["residual"]<.005
    assert plastic["checks"]["increments"][-1]["step_time"]==1


def test_mesh_refinement_moves_buckling_toward_euler_column():
    _,_,study,case,system=fixture(kind="buckling")
    case.supports=[Support(id="root",region_id="part:xmin")]
    case.loads[0].vector=(-100,0,0)
    values=[]
    for nx in (10,20):
        mesh=block_mesh(system.mesh.geometry_key,size=(40,2,2),divisions=(nx,2,2))
        r=buckling_solve(System(mesh,study,system.materials),case)
        assert r["checks"]["residual"]<1e-7
        values.append(r["values"]["min_buckling_factor"])
    expected=math.pi**2*200000*(2*2**3/12)/(4*40**2)/100
    assert values[0]>values[1]>expected
    # Explicitly demonstrate C3D4 bending stiffness error, never claim this mesh is converged.
    assert abs(values[1]-expected)<abs(values[0]-expected)


def test_axial_bolt_preload_reports_physical_connector_force():
    _,_,study,case,system=fixture(divisions=(2,1,1))
    case.loads=[]
    a=min(system.ids);b=next(n for n,p in system.mesh.nodes.items() if tuple(p)==(10.,0.,0.))
    study.bolts=[Bolt(id="bolt",node_a=a,node_b=b,area_mm2=1,youngs_modulus_mpa=200000,preload_n=100,source="Synthetic idealized axial bolt test")]
    result=static_solve(system,case)
    bolt=next(i for i in result["interfaces"] if "bolt_id" in i)
    assert 0<bolt["axial_force_n"]<100
    assert result["checks"]["force_balance_error"]<1e-8
    assert result["checks"]["residual"]<1e-8


def test_real_domain_worker_report_handoff_and_staleness(tmp_path,monkeypatch):
    project,domain,study,_,_=fixture(divisions=(2,1,1))
    domain.models[0].refinements=[block_mesh(geometry_key(project),divisions=(4,2,2))]
    store=Store(tmp_path/"project");store.init(project)
    # Old Project class in the isolated branch has no domains field until parent integration.
    project=project.model_copy(update={"domains":{"simulation":domain.model_dump(mode="json")}})
    result=execute(store,project,"solve",{"data":domain.model_dump(mode="json")})
    assert len(result["results"])==1
    assert result["results"][0]["numerical_state"]=="verified_numerically"
    assert store.artifact(result["results"][0]["field_artifact"]).exists()
    report=execute(store,project,"report",{"data":domain.model_dump(mode="json")})
    assert "physical_validation" in store.artifact(report["artifact"]).read_text()
    exported=execute(store,project,"handoff",{"data":domain.model_dump(mode="json")})
    with zipfile.ZipFile(store.artifact(exported["artifact"])) as package:
        manifest=json.loads(package.read("manifest.json"))
        assert manifest["geometry_included"] is False
        assert manifest["exported_decks"]==["axial"]
    domain.materials[0].revision+=1
    inspected=execute(store,project,"inspect",{"data":domain.model_dump(mode="json")})
    assert inspected["studies"][0]["last_result"]["stale"]


def test_hydrostatic_resultant_and_center_of_pressure_including_free_surface():
    _,_,study,case,system=fixture(divisions=(1,1,1))
    mesh=block_mesh(system.mesh.geometry_key,size=(1,1,1),divisions=(1,1,1))
    system=System(mesh,study,system.materials)
    case.loads=[Load(id="water",kind="hydrostatic",region_id="part:xmax",vector=(0,0,10000),density_kg_m3=1000,source="Analytical water-column load")]
    f=system.loads(case).reshape(-1,3)
    assert f.sum(axis=0)==pytest.approx([-5e-6,0,0],abs=1e-16)
    assert np.cross(system.xyz,f).sum(axis=0)==pytest.approx([0,-1e-5/3,2.5e-6],abs=1e-16)
    case.loads[0].origin_mm=(0,0,.5)
    f=system.loads(case).reshape(-1,3)
    assert f.sum(axis=0)==pytest.approx([-1.25e-6,0,0],abs=1e-16)
    assert np.cross(system.xyz,f).sum(axis=0)==pytest.approx([0,-1.25e-6*(.5+2*.5/3),.625e-6],abs=1e-16)


def test_duplicate_and_renumbered_mesh_cannot_claim_convergence(tmp_path):
    project,domain,study,_,_=fixture(divisions=(2,1,1))
    duplicate=domain.models[0].mesh.model_copy(deep=True)
    duplicate.id="fake-refinement"
    domain.models[0].refinements=[duplicate]
    request={"data":domain.model_dump(mode="json"),"study_id":study.id,"case_id":"axial","input_key":input_key(project,domain,study),"geometry_key":geometry_key(project),"revision":1}
    with pytest.raises(ValueError,match="Identical discretization"):solve(request,tmp_path)


def test_frictionless_gap_contact_transmits_load_and_no_tension():
    from o3d.simulation_models import MeshRecord,Element,Region
    project,domain,study,case,original=fixture(divisions=(2,1,1))
    a=block_mesh(geometry_key(project),divisions=(2,1,1),body_id="a")
    b=block_mesh(geometry_key(project),divisions=(2,1,1),body_id="b",origin=(10.1,0,0))
    offset=max(a.nodes)
    nodes=a.nodes|{n+offset:p for n,p in b.nodes.items()}
    elements=a.elements+[Element(id=e.id+len(a.elements),body_id="b",nodes=tuple(n+offset for n in e.nodes)) for e in b.elements]
    regions=a.regions+[r.model_copy(update={"triangles":[tuple(n+offset for n in t) for t in r.triangles]}) for r in b.regions]
    mesh=MeshRecord(geometry_key=geometry_key(project),nodes=nodes,elements=elements,regions=regions,source="Explicit two-bar contact benchmark")
    study.assignments=[BodyAssignment(body_id=b,material_id="test") for b in ("a","b")]
    case.supports=[Support(id="root",region_id="a:xmin",kind="partial",components=(True,False,False))]+[Support(id=f"{body}-{axis}",region_id=f"{body}:{axis}min",kind="partial",components=(False,True,False) if axis=="y" else (False,False,True)) for body in ("a","b") for axis in ("y","z")]
    case.loads=[Load(id="compress",region_id="b:xmax",vector=(-100,0,0),source="Test compression")]
    an=[n for n,p in a.nodes.items() if p[0]==10]
    bn=[n+offset for n,p in b.nodes.items() if p[0]==10.1]
    pairs=[(na,next(nb for nb in bn if nodes[nb][1:]==nodes[na][1:])) for na in an]
    study.interfaces=[Interface(id="joint",kind="separation",node_pairs=pairs,normal=(1,0,0),initial_gap_mm=.1,stiffness_n_mm=1e8,source="Specified finite penalty stiffness reference")]
    system=System(mesh,study,original.materials)
    result=static_solve(system,case)
    contact=[i for i in result["interfaces"] if "interface_id" in i]
    assert all(i["active"] and i["gap_mm"]<0 for i in contact)
    assert sum(i["normal_force_n"] for i in contact)==pytest.approx(100,rel=1e-7)
    assert result["checks"]["residual"]<1e-7
    assert result["checks"]["force_balance_error"]<1e-7
    tip=[result["field"]["displacements_mm"][n][0] for n,p in nodes.items() if p[0]==20.1]
    assert np.mean(tip)==pytest.approx(-.1025,rel=1e-5)
    case.loads[0].vector=(100,0,0)
    with pytest.raises(ValueError,match="rigid|singular"):static_solve(system,case)
