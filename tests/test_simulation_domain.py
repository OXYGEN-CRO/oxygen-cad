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
    # Independent SI reference: Pa, kg/m³ and metres, rather than deck units.
    wave_m_s=math.sqrt(200e9/7800)
    expected=wave_m_s/(4*.010)
    assert result["values"]["min_frequency_hz"]==pytest.approx(expected,rel=.01)
    assert result["values"]["mass_g"]==pytest.approx(7800*(.010*.002*.002)*1000,rel=1e-12)
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
    mass_kg=7800*(.010*.002*.002)
    assert acceleration[0]/1000==pytest.approx(100/mass_kg)
    assert result["checks"]["residual"]<1e-8
    assert result["checks"]["force_balance_error"]<1e-8


def test_mass_weight_and_support_reactions_against_si():
    _,_,_,case,system=fixture(divisions=(2,1,1))
    # Independent SI calculation for the 10 x 2 x 2 mm synthetic steel block.
    mass_kg=7800*(.010*.002*.002)
    gravity_m_s2=np.array([0.,0.,-9.80665])
    case.loads=[Load(id="weight",kind="gravity",vector=tuple(gravity_m_s2*1000),source="SI dimensional reference")]
    force=system.loads(case).reshape(-1,3)
    expected_weight_n=mass_kg*gravity_m_s2
    assert force.sum(axis=0)==pytest.approx(expected_weight_n,abs=1e-15)
    result=static_solve(system,case)
    assert result["values"]["mass_g"]==pytest.approx(mass_kg*1000,rel=1e-12)
    reaction=np.array(list(result["field"]["reactions_n"].values())).sum(axis=0)
    assert reaction==pytest.approx(-expected_weight_n,abs=1e-14)
    # The volume centroid is (5,1,1) mm; check moment as well as resultant.
    assert np.cross(system.xyz,force).sum(axis=0)==pytest.approx(np.cross([5,1,1],expected_weight_n),abs=1e-15)


def test_free_flight_transient_kinetic_energy_against_si():
    _,_,study,case,system=fixture(divisions=(2,1,1),kind="dynamic_event")
    case.supports=[]
    velocity_m_s=np.array([1.,-2.,.5])
    acceleration_m_s2=np.array([0.,0.,-9.80665])
    case.initial_velocity_mm_s=tuple(velocity_m_s*1000)
    case.loads=[Load(id="gravity",kind="gravity",vector=tuple(acceleration_m_s2*1000),source="SI free-flight reference")]
    duration=1e-5
    case.history=[(0,1),(duration,1)]
    study.settings.duration_s=duration
    result=event_solve(system,case)
    mass_kg=7800*(.010*.002*.002)
    initial_j=.5*mass_kg*np.dot(velocity_m_s,velocity_m_s)
    final_velocity=velocity_m_s+acceleration_m_s2*duration
    final_j=.5*mass_kg*np.dot(final_velocity,final_velocity)
    expected_displacement_m=velocity_m_s*duration+.5*acceleration_m_s2*duration**2
    assert result["time_history"][0]["kinetic_energy_n_mm"]==pytest.approx(initial_j*1000,rel=1e-12)
    assert result["time_history"][-1]["kinetic_energy_n_mm"]==pytest.approx(final_j*1000,rel=1e-10)
    assert result["time_history"][-1]["external_work_n_mm"]==pytest.approx((final_j-initial_j)*1000,rel=1e-7)
    for displacement in result["field"]["displacements_mm"].values():
        assert np.array(displacement)/1000==pytest.approx(expected_displacement_m,abs=1e-14)
    assert result["checks"]["energy_balance_error"]<1e-9
    assert result["checks"]["mass_scaling"] is False


@pytest.mark.parametrize("final_factor",[1.,.4,0.,-1.])
def test_calculix_gravity_and_density_deck_against_si(tmp_path,final_factor):
    from o3d.extended_solver import calculix_solve
    _,_,_,case,system=fixture(divisions=(2,1,1))
    case.loads=[Load(id="gravity",kind="gravity",vector=(0,0,-9806.65),source="SI dimensional reference")]
    case.history=[(0,0),(1,1),(2,final_factor)]
    result=calculix_solve(system,case,tmp_path,nonlinear=False)
    deck=(tmp_path/"analysis.inp").read_text().splitlines()
    # 7800 kg/m³ / (1000 kg/tonne) / (1e9 mm³/m³).
    assert float(deck[deck.index("*DENSITY")+1])==pytest.approx(7800/1000/1e9,rel=1e-12)
    reactions=np.array(list(result["field"]["support_reactions_n"].values())).sum(axis=0)
    expected_weight_n=7800*(.010*.002*.002)*9.80665*final_factor
    assert reactions==pytest.approx([0,0,expected_weight_n],abs=1e-9)
    assert result["checks"]["force_balance_error"]<1e-6
    assert result["checks"]["residual"]<1e-6
    assert result["checks"]["moment_balance_error"]<1e-6
    # The partially restrained fixture exercises loaded, constrained and free
    # components on the same node. Retain and check the raw correction audit.
    fields=result["field"]
    for support in case.supports:
        for i in system.region_nodes(support.region_id):
            n=system.ids[i]
            for axis,constrained in enumerate(support.components):
                if constrained:
                    assert fields["support_reactions_n"][n][axis]==pytest.approx(fields["solver_external_forces_n"][n][axis]-fields["applied_nodal_forces_n"][n][axis],abs=1e-15)


def test_mass_limit_uses_physical_grams(tmp_path):
    project,domain,study,_,_=fixture(divisions=(2,1,1))
    domain.models[0].refinements=[block_mesh(geometry_key(project),divisions=(4,2,2))]
    study.limits=[Limit(id="mass-budget",quantity="mass_g",operator="le",value=.1,source="Synthetic .1 g budget")]
    request={"data":domain.model_dump(mode="json"),"study_id":study.id,"case_id":"axial","input_key":input_key(project,domain,study),"geometry_key":geometry_key(project),"revision":project.revision}
    result=solve(request,tmp_path)
    assert result["numerical_state"]=="verified_numerically"
    assert result["values"]["mass_g"]==pytest.approx(.312,rel=1e-12)
    assert result["requirements_state"]=="not_satisfied"
    assert result["findings"][0]["satisfied"] is False
    assert result["physical_validation"]=="not_supplied"


def test_unit_source_changes_invalidate_previous_results(tmp_path,monkeypatch):
    import o3d.simulation_provenance as provenance
    from o3d.domains.simulation import ADAPTER_VERSION as domain_adapter
    from o3d.simulation_worker import ADAPTER_VERSION as worker_adapter
    project,domain,study,_,_=fixture(divisions=(2,1,1))
    before=provenance.software_fingerprint(study.type)
    assert "simulation_units.py" in before["source_sha256"]
    key=input_key(project,domain,study)
    actual_hash=provenance.fingerprint_file
    def changed(path):
        return "0"*64 if Path(path).name=="simulation_units.py" else actual_hash(path)
    monkeypatch.setattr(provenance,"fingerprint_file",changed)
    assert input_key(project,domain,study)!=key
    assert domain_adapter==worker_adapter=="oxygen-canonical-9"
    request={"data":domain.model_dump(mode="json"),"study_id":study.id,"case_id":"axial","input_key":key,"geometry_key":geometry_key(project),"revision":project.revision,"software":before}
    with pytest.raises(ValueError,match="software changed after snapshot"):
        solve(request,tmp_path)


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


def test_calculix_unloading_leaves_expected_permanent_strain(tmp_path):
    from o3d.extended_solver import calculix_solve
    _,domain,study,case,system=fixture(divisions=(2,1,1),kind="nonlinear_static")
    material=domain.materials[0];material.plastic_curve=[(250,0),(350,.01)];material.plastic_source="Synthetic reference only"
    case.loads[0].vector=(1200,0,0)
    case.history=[(0,0),(1,1),(2,0)]
    result=calculix_solve(system,case,tmp_path)
    assert max(u[0] for u in result["field"]["displacements_mm"].values())==pytest.approx(.05,rel=1e-5)
    assert result["values"]["max_equivalent_plastic_strain"]==pytest.approx(.005,rel=1e-5)
    assert result["checks"]["increments"][-1]["total_time"]==2
    assert result["load_displacement_history"][-1]["time"]==2
    assert result["values"]["max_displacement_mm"]>result["values"]["final_displacement_mm"]
    assert result["checks"]["force_balance_error"]<1e-8


def test_large_mesh_single_point_fixture_refuses_free_rotations():
    from o3d.simulation_models import Region
    _,_,study,case,original=fixture()
    mesh=block_mesh(original.mesh.geometry_key,divisions=(7,7,7))
    mesh.regions.append(Region(id="point",node_ids=[1]))
    system=System(mesh,study,original.materials)
    case.supports=[Support(id="point",region_id="point")]
    case.loads=[Load(id="pull",kind="remote",region_id="part:xmax",vector=(100,0,0),source="Test")]
    with pytest.raises(ValueError,match="rigid|singular|conditioned"):static_solve(system,case)


def test_event_historical_extrema_cannot_decrease_when_duration_extends():
    _,_,study,case,system=fixture(divisions=(2,1,1),kind="dynamic_event")
    study.settings.time_step_s=4e-9
    case.history=[(0,0),(1e-6,1),(2e-6,0),(5e-5,0)]
    study.settings.duration_s=5e-6
    short=event_solve(system,case)
    study.settings.duration_s=1e-5
    long=event_solve(system,case)
    for quantity in ("max_displacement_mm","max_von_mises_mpa"):
        assert long["values"][quantity]>=short["values"][quantity]*(1-1e-10)
    assert long["field"]["field_state"]=="final"
    assert max(long["field"]["element_von_mises_envelope_mpa"].values())==long["values"]["max_von_mises_mpa"]


def test_filtered_simp_evaluates_density_and_preserves_load_regions():
    from o3d.simulation_search import optimize
    _,_,study,case,original=fixture()
    mesh=block_mesh(original.mesh.geometry_key,size=(30,6,4),divisions=(6,3,2))
    case.supports=[Support(id="root",region_id="part:xmin")]
    case.loads[0].vector=(0,0,-10)
    boundary={n for r in mesh.regions if r.id in {"part:xmin","part:xmax"} for t in r.triangles for n in t}
    preserve=[e.id for e in mesh.elements if set(e.nodes)&boundary]
    study.search=SearchSettings(volume_fraction=.8,iterations=6,filter_radius_mm=4,preserved_element_ids=preserve)
    result=optimize(System(mesh,study,original.materials),case)
    assert len(result["optimization_history"])==6
    assert all(result["field"]["element_density"][e]==1 for e in preserve)
    assert result["checks"]["density_volume_fraction"]<=.8+1e-10
    assert all(h["compliance_n_mm"]>0 and h["residual"]<1e-7 for h in result["optimization_history"])
    assert result["checks"]["threshold_reanalysis_available"]
    assert result["reanalysis"]["checks"]["force_balance_error"]<1e-7
    assert result["reanalysis"]["checks"]["energy_balance_error"]<1e-7
    study.search.volume_fraction=.65
    disconnected=optimize(System(mesh,study,original.materials),case)
    assert not disconnected["checks"]["threshold_reanalysis_available"]
    assert "rigid" in disconnected["reanalysis"]["error"]


def test_native_step_netgen_transfer_has_real_volume_and_selected_regions(tmp_path):
    from o3d.model import Profile,Extrude
    from o3d.simulation_fem import tetra
    _,domain,study,case,_=fixture(divisions=(2,1,1))
    project=Project(geometry_engine="occt",features=[Profile(id="profile",width=2,height=2,plane="YZ"),Extrude(id="part",profile="profile",distance=10)])
    domain.models[0].geometry_key=geometry_key(project);domain.models[0].mesh=None
    case.supports=[Support(id="root",region_id="part:xmin")]
    store=Store(tmp_path/"mesh-project");store.init(project)
    result=execute(store,project,"mesh",{"data":domain.model_dump(mode="json"),"mesh_size_mm":1.5,"refinements":2,"element_family":"C3D4"})
    generated=SimulationDomain.model_validate(result["data"]).models[0]
    assert result["changed"] and generated.refinements[0].geometry_key==geometry_key(project)
    assert len(generated.refinements[0].elements)>len(generated.mesh.elements)
    for mesh in [generated.mesh,*generated.refinements]:
        assert {r.id for r in mesh.regions}=={"part:xmin","part:xmax"}
        volume=sum(tetra([mesh.nodes[n] for n in e.nodes])[0] for e in mesh.elements)
        assert volume==pytest.approx(40,rel=1e-8)
        for r in mesh.regions:
            expected=0 if r.id.endswith("min") else 10
            assert all(mesh.nodes[n][0]==pytest.approx(expected,abs=1e-8) for t in r.triangles for n in t)


def test_cancelling_simulation_worker_cleans_nested_solver_process(tmp_path):
    import os,signal,subprocess,sys,time
    project,domain,study,_,_=fixture(divisions=(2,1,1),kind="nonlinear_static")
    study.settings.large_deformation=True
    pidfile=tmp_path/"solver.pid"
    executable=tmp_path/"ccx-blocking"
    executable.write_text(f"#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(60)\n")
    executable.chmod(0o755)
    request={"data":domain.model_dump(mode="json"),"study_id":study.id,"case_id":"axial","input_key":input_key(project,domain,study),"geometry_key":geometry_key(project),"revision":1}
    path=tmp_path/"request.json";path.write_text(json.dumps(request))
    environment=dict(os.environ,CCX_BINARY=str(executable))
    process=subprocess.Popen([sys.executable,"-m","o3d.simulation_worker",str(path),str(tmp_path/"result.json")],env=environment,start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    child=None
    try:
        deadline=time.monotonic()+10
        while not pidfile.exists() and time.monotonic()<deadline:time.sleep(.02)
        assert pidfile.exists(),"Nested solver did not start"
        child=int(pidfile.read_text())
        os.killpg(process.pid,signal.SIGTERM)
        process.wait(timeout=5)
        with pytest.raises(ProcessLookupError):os.kill(child,0)
        assert not (tmp_path/"result.json").exists()
    finally:
        if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait()
        if child:
            try:os.kill(child,signal.SIGKILL)
            except ProcessLookupError:pass


def test_calculix_independent_buckling_on_identical_discretization(tmp_path):
    import re
    from o3d.extended_solver import write_calculix
    from o3d.solver import run_process,solver_binary
    _,_,study,case,original=fixture(kind="buckling")
    case.supports=[Support(id="root",region_id="part:xmin")];case.loads[0].vector=(-100,0,0)
    mesh=block_mesh(original.mesh.geometry_key,size=(40,2,2),divisions=(10,2,2))
    system=System(mesh,study,original.materials)
    write_calculix(tmp_path/"analysis.inp",system,case,nonlinear=False)
    header=(tmp_path/"analysis.inp").read_text().split("*STEP,")[0]
    lines=["*STEP","*BUCKLE","6","*CLOAD"]
    f=system.loads(case)
    for i,n in enumerate(system.ids):
        for axis in range(3):
            if abs(f[i*3+axis])>1e-14:lines.append(f"{n},{axis+1},{f[i*3+axis]}")
    (tmp_path/"analysis.inp").write_text(header+"\n".join(lines+["*NODE FILE","U","*END STEP"])+"\n")
    run_process([solver_binary(),"analysis"],tmp_path,tmp_path/"solver.log")
    factors=[float(value) for _,value in re.findall(r"^\s+(\d+)\s+([\d.]+E[+-]\d+)\s*$",(tmp_path/"analysis.dat").read_text(),re.M)]
    assert len(factors)==6
    actual=buckling_solve(system,case)
    assert actual["values"]["min_buckling_factor"]==pytest.approx(min(factors),rel=1e-6)


def test_fully_restrained_thermal_expansion_has_hydrostatic_stress():
    from o3d.simulation_models import Region
    _,_,study,case,system=fixture(kind="thermal_stress")
    system.mesh.regions.append(Region(id="all",node_ids=system.ids));system.regions["all"]=system.mesh.regions[-1]
    case.supports=[Support(id="all",region_id="all")];case.loads=[]
    study.stress_free_temperature_k=300
    result=static_solve(system,case,temperatures=np.full(len(system.ids),400.))
    for stress in result["field"]["stress_mpa"].values():assert stress==pytest.approx([-600,-600,-600,0,0,0],abs=1e-9)
    assert result["values"]["max_displacement_mm"]==0
    assert result["checks"]["force_balance_error"]<1e-10


def test_generative_material_alternatives_are_actually_evaluated():
    from o3d.simulation_search import generative
    _,_,study,case,original=fixture()
    mesh=block_mesh(original.mesh.geometry_key,size=(30,6,4),divisions=(6,3,2))
    case.supports=[Support(id="root",region_id="part:xmin")];case.loads[0].vector=(0,0,-10)
    boundary={n for r in mesh.regions if r.id in {"part:xmin","part:xmax"} for t in r.triangles for n in t}
    preserve=[e.id for e in mesh.elements if set(e.nodes)&boundary]
    alternate=original.materials["test"].model_copy(update={"id":"alternate","youngs_modulus_mpa":70000,"density_g_cm3":2.7,"source":"Second synthetic numerical reference"})
    materials=original.materials|{"alternate":alternate}
    study.search=SearchSettings(volume_fraction=.8,iterations=4,filter_radius_mm=4,preserved_element_ids=preserve,candidate_material_ids=["test","alternate"])
    study.limits=[Limit(id="screen",quantity="max_displacement_mm",value=1,source="Numerical reference screening limit")]
    result=generative(System(mesh,study,materials),case)
    assert result["values"]["evaluated_outcomes"]==2
    first,second=result["outcomes"]
    assert second["result"]["values"]["mass_g"]<first["result"]["values"]["mass_g"]
    assert second["result"]["values"]["max_displacement_mm"]>first["result"]["values"]["max_displacement_mm"]
    assert all(any(f["id"]=="target-volume" for f in o["findings"]) for o in result["outcomes"])
