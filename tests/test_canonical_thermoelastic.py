"""Actual coupled canonical thermal/CalculiX references, with SI expectations."""
import hashlib
import json
import zipfile

import numpy as np
import pytest

from o3d.model import geometry_key
from o3d.simulation_models import Region, Support, ThermalBoundary, BodyAssignment
from o3d.simulation_quadratic import QuadraticSystem
from o3d.simulation_thermoelastic import prepare_transfer
from o3d.simulation_worker import solve, numerical_checks
from o3d.domains.simulation import execute, input_key
from o3d.store import Store
from .test_canonical_quadratic import reference as structural_reference, request, two_body_mesh
from o3d.simulation_mesh import block_mesh


def reference(refinements=True):
    project,domain,study,case=structural_reference(kind="thermal_stress")
    material=domain.materials[0];material.conductivity_w_mk=20;material.expansion_per_k=12e-6
    study.stress_free_temperature_k=300
    case.loads=[]
    case.thermal=[ThermalBoundary(id="uniform",kind="temperature",region_id="part:xmin",value=400,source="Declared synthetic 400 K thermostat")]
    if refinements:domain.models[0].refinements=[block_mesh(geometry_key(project),divisions=(4,2,2),element_family="C3D10")]
    return project,domain,study,case


def test_uniform_free_expansion_solves_both_phases_and_verifies_exact_transfer(tmp_path):
    project,domain,study,case=reference()
    result=solve(request(project,domain,study),tmp_path)
    # Independent SI alpha * deltaT * length.
    expected=12e-6*(400-300)*np.array([.010,.001,.001])*1000
    assert result["displacement_probe"]["final"]["displacement_mm"]==pytest.approx(expected,rel=2e-6)
    assert result["values"]["max_von_mises_mpa"]<1e-6
    assert result["values"]["max_temperature_k"]==400
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]
    assert result["checks"]["thermal_residual"]<1e-9
    assert result["checks"]["residual"]<1e-7
    assert result["thermal_transfer"]["phase_split"]["structural_load_ids"]==[]
    deck=(tmp_path/"mesh-1/thermal-stress/analysis.inp").read_text()
    assert "*INITIAL CONDITIONS,TYPE=TEMPERATURE" in deck and "*EXPANSION,ZERO=" in deck
    assert "*TEMPERATURE,OP=NEW" in deck and "*CLOAD" not in deck
    thermal=json.loads((tmp_path/"mesh-1/thermal/thermal-solution.json").read_text())
    transfer=json.loads((tmp_path/"mesh-1/thermal-stress/temperature-transfer.json").read_text())
    assert transfer["temperatures_k"]==thermal["field"]["temperature_k"]
    assert transfer["audit"]["node_count"]==len(result["field"]["nodes"])
    assert result["physical_validation"]=="not_supplied"


def test_fully_restrained_uniform_heating_retains_hydrostatic_stress_tensor(tmp_path):
    project,domain,study,case=reference(refinements=False)
    mesh=domain.models[0].mesh
    mesh.regions.append(Region(id="all",node_ids=list(mesh.nodes)))
    case.supports=[Support(id="fully-fixed",region_id="all")]
    result=solve(request(project,domain,study),tmp_path)
    pressure=-200e9*12e-6*100/(1-2*.3)/1e6
    assert result["values"]["max_displacement_mm"]==0
    for tensor in result["field"]["nodal_stress_mpa"].values():
        assert tensor==pytest.approx([pressure,pressure,pressure,0,0,0],rel=3e-5,abs=1e-8)
    for points in result["field"]["integration_point_stress_mpa"].values():
        for tensor in points.values():assert tensor==pytest.approx([pressure,pressure,pressure,0,0,0],abs=1e-6)
    assert result["values"]["max_abs_mean_stress_mpa"]==pytest.approx(600,abs=1e-6)
    assert result["values"]["max_von_mises_mpa"]<1e-6
    assert result["checks"]["force_balance_error"]<1e-7
    assert result["checks"]["moment_balance_error"]<1e-7
    # Zero Mises does not mean zero thermal stress or physical qualification.
    assert result["field"]["stress_tensor_components"]==["xx","yy","zz","xy","yz","zx"]


def test_axial_thermal_restraint_and_loaded_support_RF_are_separate(tmp_path):
    project,domain,study,case=reference()
    from o3d.simulation_models import Load
    case.supports.append(Support(id="tip-x",region_id="part:xmax",kind="partial",components=(True,False,False)))
    case.loads=[Load(id="at-supported-tip",region_id="part:xmax",vector=(100,0,0),source="Declared load on thermally restrained face")]
    result=solve(request(project,domain,study),tmp_path)
    axial_stress=-200e9*12e-6*100/1e6
    assert result["values"]["max_von_mises_mpa"]==pytest.approx(abs(axial_stress),rel=3e-5)
    for tensor in result["field"]["nodal_stress_mpa"].values():
        assert tensor==pytest.approx([axial_stress,0,0,0,0,0],rel=3e-5,abs=1e-8)
    assert np.sum(list(result["field"]["support_reactions_n"].values()),axis=0)==pytest.approx([-100,0,0],abs=1e-6)
    assert result["checks"]["force_balance_error"]<1e-7
    assert result["checks"]["moment_balance_error"]<1e-7
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]


def test_linear_temperature_gradient_produces_compatible_quadratic_free_expansion(tmp_path):
    project,domain,study,case=reference(refinements=False)
    mesh=domain.models[0].mesh
    for name,point in [("origin",(0,0,0)),("xaxis",(10,0,0)),("yaxis",(0,2,0))]:
        ids=[n for n,p in mesh.nodes.items() if tuple(p)==point];assert len(ids)==1
        mesh.regions.append(Region(id=name,node_ids=ids))
    case.supports=[Support(id="origin",region_id="origin"),Support(id="axis",region_id="xaxis",kind="partial",components=(False,True,True)),Support(id="plane",region_id="yaxis",kind="partial",components=(False,False,True))]
    case.thermal[0].value=300
    case.thermal.append(ThermalBoundary(id="hot",kind="temperature",region_id="part:xmax",value=400,source="Declared linear temperature gradient"))
    result=solve(request(project,domain,study),tmp_path)
    for n,(x,y,z) in result["field"]["nodes"].items():
        expected=12e-6*10*np.array([.5*(x*x-y*y-z*z),x*y,x*z])
        assert result["field"]["displacements_mm"][n]==pytest.approx(expected,abs=2e-8)
    assert result["values"]["max_von_mises_mpa"]<1e-5
    assert result["checks"]["residual"]<1e-7


def test_distinct_body_temperature_material_and_probe_are_preserved(tmp_path):
    project,domain,study,case=reference()
    model=domain.models[0];model.body_ids=["part","second"]
    model.mesh=two_body_mesh(geometry_key(project),(2,1,1));model.refinements=[two_body_mesh(geometry_key(project),(4,2,2))]
    domain.materials.append(domain.materials[0].model_copy(update={"id":"second_material","youngs_modulus_mpa":100000,"expansion_per_k":23e-6,"source":"Second declared synthetic thermal/elastic material"}))
    study.assignments.append(BodyAssignment(body_id="second",material_id="second_material"))
    for axis in range(3):case.supports.append(Support(id=f"second-{axis}",region_id=f"second:{'xyz'[axis]}min",kind="partial",components=tuple(i==axis for i in range(3))))
    case.thermal.append(ThermalBoundary(id="second-hot",kind="temperature",region_id="second:xmin",value=450,source="Separate declared 450 K thermostat"))
    study.settings.probe_point_mm=(30,1,1);study.settings.probe_body_id="second"
    result=solve(request(project,domain,study),tmp_path)
    assert result["displacement_probe"]["final"]["displacement_mm"][0]==pytest.approx(23e-6*150*10,rel=2e-6)
    assert result["values"]["max_temperature_k"]==450
    assert result["values"]["min_temperature_k"]==400
    assert result["numerical_state"]=="verified_numerically",result["verification_gaps"]


@pytest.mark.parametrize("missing",["expansion_per_k","conductivity_w_mk","youngs_modulus_mpa","reference"])
def test_missing_thermoelastic_input_refuses_instead_of_inferring(tmp_path,missing):
    project,domain,study,_=reference(refinements=False)
    if missing=="reference":study.stress_free_temperature_k=None
    else:setattr(domain.materials[0],missing,None)
    with pytest.raises(ValueError):solve(request(project,domain,study),tmp_path)


def test_thermal_transfer_refuses_missing_nodes_and_unresolvable_delta_temperature():
    _,domain,study,_=reference(refinements=False)
    system=QuadraticSystem(domain.models[0].mesh,study,{m.id:m for m in domain.materials})
    temperatures={n:400. for n in system.ids}
    del temperatures[system.ids[-1]]
    with pytest.raises(ValueError,match="every exact structural node"):prepare_transfer(system,temperatures)
    with pytest.raises(ValueError,match="serialization cannot resolve"):
        prepare_transfer(system,{n:300+4e-11 for n in system.ids})


def test_large_thermal_eigenstrain_and_unverified_thermal_phase_cannot_pass(tmp_path):
    project,domain,study,_=reference(refinements=False)
    domain.materials[0].expansion_per_k=.001
    result=solve(request(project,domain,study),tmp_path)
    assert result["checks"]["maximum_free_thermal_strain_bound"]==pytest.approx(.1)
    assert "linear thermal-strain screening limit exceeded" in result["verification_gaps"]
    assert "thermal equation residual" in numerical_checks({"checks":{"residual":0,"thermal_residual":.1}},study)


def test_thermal_stress_handoff_preserves_both_phases_and_exact_executed_deck(tmp_path):
    project,domain,study,_=reference()
    project.domains["simulation"]=domain.model_dump(mode="json")
    store=Store(tmp_path/"project");store.init(project)
    solved=execute(store,project,"solve",{"study_id":study.id})
    assert solved["results"][0]["numerical_state"]=="verified_numerically"
    exported=execute(store,project,"handoff",{"study_id":study.id})
    with zipfile.ZipFile(store.artifact(exported["artifact"])) as package:
        manifest=json.loads(package.read("manifest.json"))
        provenance=manifest["deck_provenance"][study.cases[0].id]
        assert provenance["kind"]=="exact executed finest-mesh deck"
        deck=next(name for name in package.namelist() if name.startswith("calculix/"))
        assert package.read(deck)==package.read(provenance["source"])
        assert b"*TEMPERATURE,OP=NEW" in package.read(deck)
        assert any(name.endswith("thermal/thermal-solution.json") for name in package.namelist())
        assert any(name.endswith("thermal-stress/temperature-transfer.json") for name in package.namelist())
        for row in manifest["evidence_files"]:
            assert hashlib.sha256(package.read(row["path"])).hexdigest()==row["sha256"]


def test_declared_elastic_yield_exceeded_is_not_verified_as_elastic(tmp_path):
    project,domain,study,case=reference()
    domain.materials[0].yield_strength_mpa=200
    case.supports.append(Support(id="restrained-tip",region_id="part:xmax",kind="partial",components=(True,False,False)))
    result=solve(request(project,domain,study),tmp_path)
    assert result["constitutive_screening"]["part"]["integration_point_von_mises_peak_mpa"]==pytest.approx(240,abs=1e-6)
    assert result["constitutive_screening"]["part"]["exceeds_declared_yield"]
    assert result["numerical_state"]=="inconclusive"
    assert any("exceeds declared elastic yield" in gap for gap in result["verification_gaps"])
