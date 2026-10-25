"""Consistent-mass modal physics, rigid spaces and real cross-mesh pairing."""
import json
import numpy as np
import pytest

from o3d.model import geometry_key
from o3d.simulation_mesh import block_mesh
from o3d.simulation_models import Region,Support,BodyAssignment,Load
from o3d.simulation_eigen import QuadraticEigenSystem
from o3d.simulation_worker import solve
from .test_canonical_quadratic import reference as structural_reference,request,two_body_mesh


def reference(divisions=(2,1,1),refinements=(4,2,2),rod=False,free=False,count=2):
    project,domain,study,case=structural_reference(divisions=divisions,kind="modal")
    case.loads=[];study.settings.probe_point_mm=None;study.settings.probe_body_id=None;study.settings.mode_count=count
    model=domain.models[0]
    if refinements:model.refinements=[block_mesh(geometry_key(project),divisions=refinements,element_family="C3D10")]
    if free:case.supports=[]
    elif rod:
        domain.materials[0].poisson_ratio=0
        for mesh in [model.mesh,*model.refinements]:mesh.regions.append(Region(id="all",node_ids=list(mesh.nodes)))
        case.supports=[Support(id="root",region_id="part:xmin",kind="partial",components=(True,False,False)),Support(id="axial-only",region_id="all",kind="partial",components=(False,True,True))]
    else:case.supports=[Support(id="root",region_id="part:xmin")]
    return project,domain,study,case


def test_quadratic_consistent_mass_and_affine_elastic_energy_are_SI_correct():
    _,domain,study,_=reference(refinements=None)
    system=QuadraticEigenSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    translation=np.tile([1.,0,0],len(system.ids))
    expected_tonne=7800*(.010*.002*.002)/1000
    assert translation@(system.m@translation)==pytest.approx(expected_tonne,rel=1e-12)
    field=system.xyz*np.array([1,-.3,-.3])
    # Strain (1,-nu,-nu) is uniaxial stress E in independent SI units.
    expected_energy=.5*200e9*(.010*.002*.002)*1000
    assert .5*field.ravel()@(system.k@field.ravel())==pytest.approx(expected_energy,rel=1e-12)
    assert np.linalg.norm(system.k@translation)/np.linalg.norm(system.k.data)<1e-14
    assert np.linalg.eigvalsh(system.m.toarray()).min()>0
    # Consistent P2 mass is positive definite despite signed row sums.
    assert np.min(system.m@translation)<0
    assert system.integration["mass_model"].startswith("full consistent")


def test_axial_frequencies_modes_and_mesh_correlation_match_SI_rod(tmp_path):
    project,domain,study,_=reference(rod=True,count=1)
    result=solve(request(project,domain,study),tmp_path)
    analytical=(200e9/7800)**.5/(4*.010)
    assert result["values"]["min_frequency_hz"]==pytest.approx(analytical,rel=.001)
    assert result["values"]["mass_g"]==pytest.approx(.312,rel=1e-12)
    assert result["checks"]["rigid_mode_count"]==0
    assert result["checks"]["mass_orthogonality_error"]<1e-10
    assert result["checks"]["residual"]<1e-7
    assert result["mode_tracking"]["passed"],result["mode_tracking"]
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]
    assert (tmp_path/"mesh-1/modal/consistent-mass.npz").exists()
    assert result["modal_metadata"]["computed_guard_modes"]==4
    shape=np.array(list(result["modes"][0]["shape"].values()))
    assert np.linalg.norm(shape,axis=1).max()==pytest.approx(1)


def test_free_body_has_six_analytic_rigid_modes_and_elastic_mass_orthogonality(tmp_path):
    project,domain,study,_=reference(divisions=(4,2,2),refinements=(8,3,3),free=True,count=2)
    result=solve(request(project,domain,study),tmp_path)
    rigid=[m for m in result["modes"] if m["rigid_body"]]
    elastic=[m for m in result["modes"] if not m["rigid_body"]]
    assert len(rigid)==6 and len(elastic)==2
    assert all(m["frequency_hz"]==0 for m in rigid)
    assert result["values"]["min_frequency_hz"]>0
    assert result["checks"]["rigid_stiffness_error"]<1e-12
    assert result["checks"]["mass_orthogonality_error"]<1e-9
    assert result["checks"]["residual"]<1e-7
    for axis in range(3):
        assert sum(m["participation"][axis]["effective_mass_fraction"] for m in rigid)==pytest.approx(1,abs=1e-10)
        assert max(m["participation"][axis]["effective_mass_fraction"] for m in elastic)<1e-12
    assert result["mode_tracking"]["passed"],result["mode_tracking"]
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]


def test_partial_constraints_preserve_only_the_remaining_rigid_space(tmp_path):
    project,domain,study,case=reference(refinements=None,free=True,count=2)
    mesh=domain.models[0].mesh;mesh.regions.append(Region(id="all",node_ids=list(mesh.nodes)))
    case.supports=[Support(id="only-x",region_id="all",kind="partial",components=(True,False,False))]
    result=solve(request(project,domain,study),tmp_path)
    assert result["checks"]["rigid_mode_count"]==3
    assert len([m for m in result["modes"] if not m["rigid_body"]])==2
    assert result["numerical_state"]=="inconclusive"  # one mesh is not convergence


def test_two_material_bodies_keep_density_in_mass_pairing_and_participation(tmp_path):
    project,domain,study,case=reference(rod=True,count=2)
    model=domain.models[0];model.body_ids=["part","second"]
    model.mesh=two_body_mesh(geometry_key(project),(2,1,1));model.refinements=[two_body_mesh(geometry_key(project),(4,2,2))]
    for mesh in [model.mesh,*model.refinements]:
        for body,region in [("part","all"),("second","second-all")]:
            nodes={n for e in mesh.elements if e.body_id==body for n in e.nodes}
            mesh.regions.append(Region(id=region,body_id=body,node_ids=sorted(nodes)))
    case.supports.append(Support(id="second-axial-only",region_id="second-all",kind="partial",components=(False,True,True)))
    domain.materials.append(domain.materials[0].model_copy(update={"id":"second_material","youngs_modulus_mpa":100000,"density_g_cm3":2.7,"source":"Second declared SI modal benchmark"}))
    study.assignments.append(BodyAssignment(body_id="second",material_id="second_material"))
    case.supports.append(Support(id="second-root",region_id="second:xmin",kind="partial",components=(True,False,False)))
    result=solve(request(project,domain,study),tmp_path)
    assert result["values"]["mass_g"]==pytest.approx(.42,rel=1e-12)
    assert result["modes"][1]["frequency_hz"]/result["modes"][0]["frequency_hz"]==pytest.approx(((100e9/2700)/(200e9/7800))**.5,rel=.001)
    masses=result["mode_tracking"]["quadrature_checks"][-1]["body_mass_tonne"]
    assert masses==pytest.approx({"part":.312/1e6,"second":.108/1e6},rel=1e-12)
    assert result["mode_tracking"]["passed"],result["mode_tracking"]
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]


def test_a_repeated_family_cut_by_requested_window_cannot_claim_convergence(tmp_path):
    project,domain,study,_=reference(divisions=(4,2,2),refinements=(8,3,3),count=1)
    result=solve(request(project,domain,study),tmp_path)
    assert not result["mode_tracking"]["passed"]
    assert any(g["requested_window_cut"] for g in result["mode_tracking"]["groups"])
    assert result["numerical_state"]=="inconclusive"


@pytest.mark.parametrize("problem",["density","preload","velocity"])
def test_missing_and_untranslated_modal_inputs_refuse(tmp_path,problem):
    project,domain,study,case=reference(refinements=None)
    if problem=="density":domain.materials[0].density_g_cm3=None
    if problem=="preload":case.loads=[Load(id="not-unloaded",region_id="part:xmax",vector=(1,0,0),source="Declared unsupported preload")]
    if problem=="velocity":case.initial_velocity_mm_s=(1,0,0)
    with pytest.raises(ValueError):solve(request(project,domain,study),tmp_path)


def test_native_curved_tube_modal_study_preserves_body_geometry_and_evidence(tmp_path,mesh_families):
    from o3d.geometry import build
    from o3d.store import Store
    from o3d.domains.simulation import execute
    from o3d.simulation_models import SimulationDomain
    project,domain,study,case=reference(refinements=None,count=2)
    project=mesh_families["tube"].model_copy(update={"geometry_engine":"solidcraft"})
    store=Store(tmp_path/"native");store.init(project)
    body=build(store,project)["scene"]["bodies"][0]
    root=next(f for f in body["faces"] if f.get("normal") and f["normal"][0]<-.99)
    model=domain.models[0];model.geometry_key=geometry_key(project);model.body_ids=[body["id"]];model.mesh=None
    model.regions=[Region(id="clamp",body_id=body["id"],selection=root["id"])]
    study.assignments[0].body_id=body["id"];case.supports=[Support(id="clamp",region_id="clamp")]
    meshed=execute(store,project,"mesh",{"data":domain.model_dump(mode="json"),"mesh_size_mm":4,"refinements":2})
    saved=SimulationDomain.model_validate(meshed["data"])
    assert saved.models[0].mesh.element_family=="C3D10"
    result=solve(request(project,saved,saved.studies[0]),tmp_path/"solve")
    expected_g=7800*np.pi*(.008**2-.006**2)*.040*1000
    assert result["values"]["mass_g"]==pytest.approx(expected_g,rel=.01)
    assert result["mesh_quality"]["curved_jacobian_checked"]
    assert result["checks"]["residual"]<1e-7
    assert result["checks"]["mass_orthogonality_error"]<1e-7
    assert result["checks"]["rigid_mode_count"]==0
    assert result["numerical_state"]=="verified_numerically",result.get("mode_tracking")
    assert (tmp_path/"solve/mesh-1/modal/all-modes.json").is_file()
