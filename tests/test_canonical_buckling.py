"""SI instability, reference-load convention and full-node field verification."""
import json
import math
import numpy as np
import pytest

from o3d.model import geometry_key
from o3d.simulation_mesh import block_mesh
from o3d.simulation_models import Support,Load
from o3d.simulation_eigen import QuadraticElasticSystem
from o3d.simulation_buckling import buckling_solve
from o3d.simulation_worker import solve
from .test_canonical_quadratic import reference as structural_reference,request


def reference(divisions=(8,2,2),refinements=(12,3,3),size=(40,2,2),count=2):
    project,domain,study,case=structural_reference(kind="buckling")
    study.settings.probe_point_mm=None;study.settings.probe_body_id=None;study.settings.mode_count=count
    domain.materials[0].density_g_cm3=None
    case.supports=[Support(id="root",region_id="part:xmin")]
    case.loads=[Load(id="compression",region_id="part:xmax",vector=(-100,0,0),source="Declared SI elastic-column reference, not measured hardware")]
    model=domain.models[0];model.mesh=block_mesh(geometry_key(project),size=size,divisions=divisions,element_family="C3D10")
    model.refinements=[block_mesh(geometry_key(project),size=size,divisions=refinements,element_family="C3D10")] if refinements else []
    return project,domain,study,case


def test_quadratic_buckling_converges_near_SI_Euler_without_inventing_density(tmp_path):
    project,domain,study,_=reference()
    result=solve(request(project,domain,study),tmp_path)
    expected=math.pi**2*200e9*(.002*.002**3/12)/(4*.040**2)/100
    assert result["values"]["min_buckling_factor"]==pytest.approx(expected,rel=.02)
    assert result["checks"]["prestress"]["residual"]<1e-7
    assert result["checks"]["prestress"]["force_balance_error"]<1e-7
    assert result["checks"]["prestress"]["moment_balance_error"]<1e-7
    assert result["checks"]["residual"]<1e-7
    assert result["checks"]["stiffness_orthogonality_error"]<1e-7
    assert result["mode_tracking"]["passed"],result["mode_tracking"]
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]
    assert domain.materials[0].density_g_cm3 is None
    assert "mass_g" not in result["values"]
    for mode in result["modes"]:
        assert mode["load_factor"]>0
        assert max(np.linalg.norm(v) for v in mode["shape"].values())==pytest.approx(1)
    saved=json.loads((tmp_path/"mesh-1/buckling/all-modes.json").read_text())
    assert len(saved["modes"])==study.settings.mode_count+4
    assert (tmp_path/"mesh-1/buckling/geometric-stiffness.npz").exists()
    assert (tmp_path/"mesh-1/buckling/reference-load.json").exists()
    assert "integration_point_stress_mpa" not in result["reference_load"]
    assert result["reference_load_evidence"]["bytes"]==(tmp_path/"mesh-1/buckling/reference-load.json").stat().st_size
    assert "body_volume_mm3" in result["mode_tracking"]["quadrature_checks"][-1]


def test_reference_load_scaling_changes_factor_without_changing_critical_force(tmp_path):
    _,domain,study,case=reference(divisions=(4,1,1),refinements=None)
    mesh=domain.models[0].mesh;system=QuadraticElasticSystem(mesh,study,{m.id:m for m in domain.materials})
    first=buckling_solve(system,case,tmp_path/"one")
    case.loads[0].vector=(-200,0,0)
    second=buckling_solve(system,case,tmp_path/"two")
    assert second["values"]["min_buckling_factor"]==pytest.approx(first["values"]["min_buckling_factor"]/2,rel=1e-9)
    assert sum(v[0] for v in json.loads((tmp_path/"two/reference-load.json").read_text())["support_reactions_n"].values())==pytest.approx(200,rel=1e-9)
    a=np.array(list(first["modes"][0]["shape"].values()));b=np.array(list(second["modes"][0]["shape"].values()))
    # A square section has a repeated pair: compare full subspaces in other
    # tests rather than requiring an arbitrary orientation for an eigenvector.
    assert a.shape==b.shape


def test_tension_does_not_become_a_positive_compression_instability(tmp_path):
    project,domain,study,case=reference(divisions=(4,1,1),refinements=None)
    case.loads[0].vector=(100,0,0)
    # Zero Poisson contraction makes this a uniform uniaxial tensile state.
    # A laterally clamped nu=.3 end can have local compressive fixture stress.
    domain.materials[0].poisson_ratio=0
    with pytest.raises(ValueError,match="compressive reference prestress"):
        solve(request(project,domain,study),tmp_path)


def test_repeated_buckling_family_cut_by_window_is_inconclusive(tmp_path):
    project,domain,study,_=reference(divisions=(4,2,2),refinements=(8,3,3),count=1)
    result=solve(request(project,domain,study),tmp_path)
    assert not result["mode_tracking"]["passed"]
    assert any(g["requested_window_cut"] for g in result["mode_tracking"]["groups"])
    assert result["numerical_state"]=="inconclusive"


def test_declared_yield_below_critical_stress_prevents_elastic_qualification(tmp_path):
    project,domain,study,_=reference(divisions=(4,2,2),refinements=(8,3,3))
    domain.materials[0].yield_strength_mpa=.1
    result=solve(request(project,domain,study),tmp_path)
    assert result["constitutive_screening"]["part"]["exceeds_declared_yield"]
    assert result["numerical_state"]=="inconclusive"
    assert any("declared elastic yield" in x for x in result["verification_gaps"])


@pytest.mark.parametrize("problem",["gravity-density","unsupported","velocity","zero-load"])
def test_missing_or_untranslated_buckling_inputs_refuse(tmp_path,problem):
    project,domain,study,case=reference(divisions=(2,1,1),refinements=None)
    if problem=="gravity-density":case.loads=[Load(id="gravity",kind="gravity",vector=(-9806.65,0,0),source="Declared missing-density refusal reference")]
    if problem=="unsupported":case.supports=[]
    if problem=="velocity":case.initial_velocity_mm_s=(1,0,0)
    if problem=="zero-load":case.loads=[]
    with pytest.raises(ValueError):solve(request(project,domain,study),tmp_path)


def test_native_curved_tube_buckling_retains_actual_geometry_and_reference_fields(tmp_path,mesh_families):
    from o3d.geometry import build
    from o3d.store import Store
    from o3d.domains.simulation import execute
    from o3d.simulation_models import Region,SimulationDomain
    project,domain,study,case=reference(refinements=None)
    project=mesh_families["tube"].model_copy(update={"geometry_engine":"solidcraft"})
    store=Store(tmp_path/"native");store.init(project)
    body=build(store,project)["scene"]["bodies"][0]
    root=next(f for f in body["faces"] if f.get("normal") and f["normal"][0]<-.99)
    tip=next(f for f in body["faces"] if f.get("normal") and f["normal"][0]>.99)
    model=domain.models[0];model.geometry_key=geometry_key(project);model.body_ids=[body["id"]];model.mesh=None
    model.regions=[Region(id="clamp",body_id=body["id"],selection=root["id"]),Region(id="load",body_id=body["id"],selection=tip["id"])]
    study.assignments[0].body_id=body["id"];case.supports=[Support(id="clamp",region_id="clamp")]
    case.loads=[Load(id="compression",region_id="load",vector=(-100,0,0),source="Declared synthetic curved-tube dead load")]
    meshed=execute(store,project,"mesh",{"data":domain.model_dump(mode="json"),"mesh_size_mm":4,"refinements":2})
    saved=SimulationDomain.model_validate(meshed["data"])
    assert saved.models[0].mesh.element_family=="C3D10"
    result=solve(request(project,saved,saved.studies[0]),tmp_path/"solve")
    assert result["mesh_quality"]["curved_jacobian_checked"]
    assert result["checks"]["residual"]<1e-7
    assert result["checks"]["prestress"]["residual"]<1e-7
    assert result["mode_tracking"]["passed"],result["mode_tracking"]
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]
    assert result["values"]["min_buckling_factor"]>0
    fields=json.loads((tmp_path/"solve/mesh-1/buckling/reference-load.json").read_text())
    assert sum(v[0] for v in fields["support_reactions_n"].values())==pytest.approx(100,rel=1e-7)
    assert len(fields["integration_point_stress_mpa"])==len(saved.models[0].refinements[-1].elements)
    assert (tmp_path/"solve/mesh-1/buckling/geometric-stiffness.npz").exists()


def test_same_mesh_buckling_agrees_with_actual_CalculiX_and_field_pairing(tmp_path):
    from o3d.extended_solver import write_calculix
    from o3d.analysis import parse_eigenvalues,parse_frd,eigenmode_fields
    from o3d.solver import run_process,solver_binary,calculix_number
    from o3d.simulation_mode_tracking import track_modes
    _,domain,study,case=reference(divisions=(4,2,2),refinements=None)
    domain.materials[0].poisson_ratio=0  # Uniform prestress makes quadrature comparable.
    system=QuadraticElasticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    own=buckling_solve(system,case,tmp_path/"canonical");bundle=own.pop("_mode_bundle")
    deck=tmp_path/"analysis.inp"
    force=write_calculix(deck,system,case,nonlinear=False)
    header=deck.read_text().split("*STEP,")[0]
    lines=["*STEP","*BUCKLE",f"{study.settings.mode_count+4},1e-10","*CLOAD"]
    for i,n in enumerate(system.ids):
        for axis in range(3):
            if force[3*i+axis]:lines.append(f"{n},{axis+1},{calculix_number(force[3*i+axis])}")
    deck.write_text(header+"\n".join(lines+["*NODE FILE","U","*END STEP"])+"\n")
    run_process([solver_binary(),"analysis"],tmp_path,tmp_path/"solver.log",timeout=180)
    factors=parse_eigenvalues(tmp_path/"analysis.dat","buckling")
    fields=eigenmode_fields(parse_frd(tmp_path/"analysis.frd"),factors,"buckling")
    compared={**bundle,"values":factors,"fields":fields}
    correlation=track_modes(bundle,compared,study.settings.mode_count)
    relative=float(np.max(abs(np.array(factors)-np.array(bundle["values"]))/np.array(factors)))
    from o3d.store import write_json
    write_json(tmp_path/"comparison.json",{"maximum_factor_relative_error":relative,"correlation":correlation,"calculix_factors":factors})
    assert len(factors)==study.settings.mode_count+4
    assert relative<1e-6
    assert correlation["passed"],correlation


def test_independent_material_bodies_keep_their_load_paths_and_volume_pairing(tmp_path):
    from o3d.simulation_models import BodyAssignment
    from .test_canonical_quadratic import two_body_mesh
    project,domain,study,case=reference(divisions=(4,2,2),refinements=(8,3,3))
    model=domain.models[0];model.body_ids=["part","second"]
    model.mesh=two_body_mesh(geometry_key(project),(4,2,2));model.refinements=[two_body_mesh(geometry_key(project),(8,3,3))]
    domain.materials.append(domain.materials[0].model_copy(update={"id":"second-material","youngs_modulus_mpa":400000,"source":"Declared second elastic reference, no measured hardware"}))
    study.assignments.append(BodyAssignment(body_id="second",material_id="second-material"))
    case.supports.append(Support(id="second-root",region_id="second:xmin"))
    case.loads.append(Load(id="second-load",region_id="second:xmax",vector=(-100,0,0),source="Declared independent body reference"))
    result=solve(request(project,domain,study),tmp_path)
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]
    assert result["mode_tracking"]["passed"]
    assert result["mode_tracking"]["quadrature_checks"][-1]["body_volume_mm3"]==pytest.approx({"part":40.,"second":40.},rel=1e-12)
    for body,region in [("part","part:xmin"),("second","second:xmin")]:
        selected=next(r for r in model.refinements[0].regions if r.id==region)
        nodes=set(selected.node_ids)|{n for t in selected.triangles for n in t}
        assert sum(json.loads((tmp_path/"mesh-1/buckling/reference-load.json").read_text())["support_reactions_n"][str(n)][0] for n in nodes)==pytest.approx(100,rel=1e-7)


def test_reference_load_on_supported_nodes_is_subtracted_from_reactions(tmp_path):
    _,domain,study,case=reference(divisions=(4,1,1),refinements=None)
    system=QuadraticElasticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    baseline=buckling_solve(system,case,tmp_path/"baseline")
    case.loads.append(Load(id="loaded-support",region_id="part:xmin",vector=(30,0,0),source="Declared support-load reaction reference"))
    result=buckling_solve(system,case,tmp_path/"loaded")
    assert result["values"]["min_buckling_factor"]==pytest.approx(baseline["values"]["min_buckling_factor"],rel=1e-10)
    fields=json.loads((tmp_path/"loaded/reference-load.json").read_text())
    assert sum(v[0] for v in fields["applied_nodal_forces_n"].values())==pytest.approx(-70,abs=1e-10)
    assert sum(v[0] for v in fields["support_reactions_n"].values())==pytest.approx(70,rel=1e-9)
    assert result["checks"]["prestress"]["force_balance_error"]<1e-9
