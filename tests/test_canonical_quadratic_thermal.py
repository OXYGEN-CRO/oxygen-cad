"""Full quadratic solid conduction against independent SI analytical models."""
import numpy as np
import pytest

from o3d.model import Project, geometry_key
from o3d.simulation_mesh import block_mesh
from o3d.simulation_models import (AnalysisModel, BodyAssignment, LoadCase,
    MaterialRecord, SimulationDomain, StudyRecord, ThermalBoundary, Interface, Support)
from o3d.simulation_quadratic_thermal import QuadraticThermalSystem, thermal_solve
from o3d.simulation_worker import solve
from o3d.domains.simulation import input_key, preflight


def reference(kind="steady_thermal", refinements=True):
    project = Project()
    key = geometry_key(project)
    model = AnalysisModel(id="solid", name="Declared 10 by 2 by 2 mm numerical solid", geometry_key=key,
        mesh=block_mesh(key, divisions=(2, 1, 1), element_family="C3D10"),
        refinements=[block_mesh(key, divisions=(4, 2, 2), element_family="C3D10")] if refinements else [])
    material = MaterialRecord(id="thermal", name="Conductivity-only numerical material", source="SI analytical benchmark; no physical calibration", conductivity_w_mk=20)
    case = LoadCase(id="heat", name="Explicit thermal boundaries", thermal=[
        ThermalBoundary(id="cold", kind="temperature", region_id="part:xmin", value=300, source="Declared cold thermostat"),
        ThermalBoundary(id="hot", kind="temperature", region_id="part:xmax", value=400, source="Declared hot thermostat")])
    study = StudyRecord(id="thermal", name="Full ten-node solid conduction", type=kind, model_id=model.id,
        assignments=[BodyAssignment(body_id="part", material_id=material.id)], cases=[case], assumptions_confirmed=True)
    domain = SimulationDomain(models=[model], materials=[material], studies=[study], active_study_id=study.id)
    return project, domain, study, case


def request(project, domain, study):
    return {"data": domain.model_dump(mode="json"), "study_id": study.id, "case_id": study.cases[0].id,
            "input_key": input_key(project, domain, study), "geometry_key": geometry_key(project), "revision": project.revision}


def test_conductivity_only_quad_temperature_flux_and_shared_worker(tmp_path):
    project, domain, study, _ = reference()
    assert not preflight(project, domain, study, domain.models[0])
    result = solve(request(project, domain, study), tmp_path)
    assert result["numerical_state"] == "verified_numerically", result["verification_gaps"]
    assert result["values"]["min_temperature_k"] == pytest.approx(300, abs=1e-9)
    assert result["values"]["max_temperature_k"] == pytest.approx(400, abs=1e-9)
    for n, temperature in result["field"]["temperature_k"].items():
        x = result["field"]["nodes"][n][0]/1000
        assert temperature == pytest.approx(300+100*x/.010, abs=1e-9)
    for flux in result["field"]["element_heat_flux_w_mm2"].values():
        assert np.array(flux)*1e6 == pytest.approx([-20*100/.010, 0, 0], abs=1e-6)
    assert result["checks"]["heat_balance_error"] < 1e-10
    assert result["checks"]["residual"] < 1e-10
    assert result["mesh_quality"]["curved_jacobian_checked"]
    assert result["field_location"]["heat_flux"] == "element_volume_average"
    assert len(result["field"]["boundary_triangles"]) == 4*len(result["field"]["boundary_faces"])
    assert (tmp_path/"mesh-1/thermal/thermal-solution.json").is_file()
    assert result["physical_validation"] == "not_supplied"


def test_quadratic_body_heat_is_exact_parabola_and_signed_consistent_source(tmp_path):
    _, domain, study, case = reference(refinements=False)
    case.thermal = [case.thermal[0], ThermalBoundary(id="heat", kind="body_heat", body_id="part", value=.08, source="Declared 0.08 W total volume heat")]
    system = QuadraticThermalSystem(domain.models[0].mesh, study, {m.id:m for m in domain.materials})
    result = thermal_solve(system, case, tmp_path)
    # SI: q'''=P/(A L), insulated tip, T(x)=T0+q'''(L*x-x²/2)/k.
    power, area, length, conductivity = .08, .002**2, .010, 20
    generated = power/(area*length)
    for n, actual in result["field"]["temperature_k"].items():
        x = system.mesh.nodes[n][0]/1000
        assert actual == pytest.approx(300+generated*(length*x-x*x/2)/conductivity, abs=2e-9)
    assert result["values"]["applied_heat_w"] == pytest.approx(power, abs=1e-13)
    assert result["values"]["thermostat_heat_w"] == pytest.approx(-power, abs=1e-12)
    assert result["values"]["max_temperature_k"] == pytest.approx(305, abs=1e-9)
    assert result["checks"]["temperature_bernstein_bounds_k"][1] >= max(result["field"]["temperature_k"].values())-1e-10


@pytest.mark.parametrize("kind,coefficient", [("convection", 1000), ("radiation", .8)])
@pytest.mark.parametrize("power", [.02, -0.001])
def test_consistent_face_heat_exchange_matches_SI_and_negative_heat(tmp_path, kind, coefficient, power):
    project, domain, study, case = reference(kind="electronics_cooling")
    ambient = 300.
    case.thermal = [ThermalBoundary(id="source", kind="heat_power", region_id="part:xmin", value=power, source="Declared signed heat source"),
                    ThermalBoundary(id="sink", kind=kind, region_id="part:xmax", value=coefficient, ambient_k=ambient, source="Declared prescribed-surroundings model")]
    result = solve(request(project, domain, study), tmp_path)
    area, length, conductivity = .002**2, .010, 20
    tip = ambient+power/(coefficient*area) if kind == "convection" else (ambient**4+power/(coefficient*5.670374419e-8*area))**.25
    root = tip+power*length/(conductivity*area)
    for n, actual in result["field"]["temperature_k"].items():
        x = result["field"]["nodes"][n][0]/1000
        assert actual == pytest.approx(root+(tip-root)*x/length, abs=3e-7)
    assert result["values"]["heat_rejected_w"] == pytest.approx(power, rel=2e-8, abs=1e-12)
    assert result["values"]["thermostat_heat_w"] == 0
    assert result["checks"]["heat_balance_error"] < 1e-7
    assert result["numerical_state"] == "verified_numerically", result["verification_gaps"]
    assert "No air domain" in " ".join(result["warnings"])


def test_uniform_thermal_reference_has_no_fabricated_source_or_elasticity(tmp_path):
    project, domain, study, case = reference()
    case.thermal = [case.thermal[0]]
    result = solve(request(project, domain, study), tmp_path)
    assert all(t == 300 for t in result["field"]["temperature_k"].values())
    assert result["values"]["applied_heat_w"] == result["values"]["heat_rejected_w"] == result["values"]["thermostat_heat_w"] == 0
    assert result["numerical_state"] == "verified_numerically", result["verification_gaps"]
    assert domain.materials[0].youngs_modulus_mpa is None


@pytest.mark.parametrize("problem", ["conductivity", "supports", "zero_exchange", "conflict", "interface"])
def test_missing_and_untranslated_inputs_refuse(tmp_path, problem):
    project, domain, study, case = reference(refinements=False)
    expected = {"conductivity":"conductivity", "supports":"mechanical supports", "zero_exchange":"anchor", "conflict":"Conflicting", "interface":"unavailable"}[problem]
    if problem == "conductivity":domain.materials[0].conductivity_w_mk = None
    if problem == "supports":case.supports = [Support(id="fixed", region_id="part:xmin")]
    if problem == "zero_exchange":case.thermal = [ThermalBoundary(id="insulated", kind="convection", region_id="part:xmax", value=0, ambient_k=300, source="Explicit zero film")]
    if problem == "conflict":case.thermal.append(ThermalBoundary(id="contradiction", kind="temperature", region_id="part:xmin", value=350, source="Conflicting explicit temperature"))
    if problem == "interface":study.interfaces = [Interface(id="not_translated", kind="disabled", source="Explicit unsupported interface")]
    with pytest.raises(ValueError, match=expected):solve(request(project, domain, study), tmp_path)


def test_temperature_limits_use_interpolated_bounds_not_just_nodal_samples():
    _, domain, study, _ = reference(refinements=False)
    system = QuadraticThermalSystem(domain.models[0].mesh, study, {m.id:m for m in domain.materials})
    # A quadratic polynomial can overshoot between positive nodal samples.
    temperature = np.full(len(system.ids), 300.)
    element = system.mesh.elements[0]
    temperature[system.index[element.nodes[4]]] = 1000
    low, high = system.temperature_bounds(temperature)
    assert high > temperature.max()
    assert low > 0


def test_thermal_convergence_scales_to_rise_and_conservative_extrema(tmp_path):
    project, domain, study, case = reference()
    model = domain.models[0]
    model.mesh = block_mesh(geometry_key(project), divisions=(1, 1, 1), element_family="C3D10")
    model.refinements = [block_mesh(geometry_key(project), divisions=(3, 3, 3), element_family="C3D10")]
    case.thermal[1].value = 300
    case.thermal.append(ThermalBoundary(id="heat", kind="body_heat", body_id="part", value=.32, source="Declared internal heat"))
    result = solve(request(project, domain, study), tmp_path)
    # Exact P2 parabola has an interior maximum; conservative Bernstein bounds
    # tighten with refinement even though Kelvin-relative change is under 5%.
    coarse, fine = result["mesh_iterations"]
    absolute_kelvin_change = abs(coarse["values"]["max_temperature_k"]-fine["values"]["max_temperature_k"])/fine["values"]["max_temperature_k"]
    assert absolute_kelvin_change < study.settings.convergence_tolerance
    assert fine["relative_changes"]["max_temperature_k"] > study.settings.convergence_tolerance
    assert not result["checks"]["mesh_converged"]
    assert result["numerical_state"] == "inconclusive"


def test_tiny_heat_source_is_not_normalized_against_absolute_ambient(tmp_path):
    _, domain, study, case = reference(refinements=False)
    case.thermal = [case.thermal[0], ThermalBoundary(id="tiny", kind="heat_power", region_id="part:xmax", value=1e-6, source="Explicit small source")]
    system = QuadraticThermalSystem(domain.models[0].mesh, study, {m.id:m for m in domain.materials})
    result = thermal_solve(system, case, tmp_path)
    assert result["values"]["max_temperature_k"]-300 == pytest.approx(1e-6*.010/(20*.002**2), rel=1e-7)
    assert result["checks"]["residual"] < study.settings.residual_tolerance


def test_heat_below_temperature_precision_cannot_be_certified(tmp_path):
    project, domain, study, case = reference(refinements=False)
    case.thermal = [case.thermal[0], ThermalBoundary(id="unresolvable", kind="heat_power", region_id="part:xmax", value=1e-20, source="Declared precision refusal benchmark")]
    with pytest.raises(ValueError, match="precision may be insufficient"):
        solve(request(project, domain, study), tmp_path)


def test_native_curved_tube_thermal_transfer_uses_all_nodes_and_no_elasticity(tmp_path, mesh_families):
    from o3d.geometry import build
    from o3d.store import Store
    from o3d.domains.simulation import execute
    from o3d.simulation_models import Region
    project, domain, study, case = reference(refinements=False)
    project = mesh_families["tube"].model_copy(update={"geometry_engine":"solidcraft"})
    store = Store(tmp_path/"curved-native");store.init(project)
    body = build(store, project)["scene"]["bodies"][0]
    left = next(f for f in body["faces"] if f.get("normal") and f["normal"][0] < -.99)
    right = next(f for f in body["faces"] if f.get("normal") and f["normal"][0] > .99)
    model = domain.models[0];model.geometry_key = geometry_key(project);model.body_ids = [body["id"]];model.mesh = None
    model.regions = [Region(id="left", body_id=body["id"], selection=left["id"]), Region(id="right", body_id=body["id"], selection=right["id"])]
    study.assignments[0].body_id = body["id"]
    case.thermal[0].region_id = "left";case.thermal[1].region_id = "right"
    meshed = execute(store, project, "mesh", {"data":domain.model_dump(mode="json"), "element_family":"C3D10", "mesh_size_mm":4, "refinements":2})
    domain = SimulationDomain.model_validate(meshed["data"])
    result = solve(request(project, domain, domain.studies[0]), tmp_path/"curved-solve")
    # The curved CAD is approximated by isoparametric cells, unlike the exact
    # straight reference. Bound error relative to the 100 K rise and require
    # actual refinement to reduce the ideal-cylinder solution error.
    errors = []
    for i in (0, 1):
        import json
        field = json.loads((tmp_path/f"curved-solve/mesh-{i}/thermal/thermal-solution.json").read_text())["field"]
        errors.append(max(abs(t-(300+100*field["nodes"][n][0]/40)) for n,t in field["temperature_k"].items()))
    assert errors[1]/100 < 2e-6
    assert errors[1] < errors[0]*.5
    assert result["checks"]["heat_balance_error"] < 1e-7
    assert result["mesh_quality"]["curved_jacobian_checked"]
    assert result["numerical_state"] == "verified_numerically", result["verification_gaps"]


@pytest.mark.parametrize("condition", ["distinct_ambient", "different_conductivity", "missing_anchor"])
def test_disconnected_thermal_bodies_keep_distinct_materials_and_anchors(tmp_path, condition):
    from .test_canonical_quadratic import two_body_mesh
    project, domain, study, case = reference(refinements=False)
    model = domain.models[0];model.body_ids = ["part", "second"]
    model.mesh = two_body_mesh(geometry_key(project), (2, 1, 1))
    domain.materials.append(MaterialRecord(id="second_material", name="Second conductivity-only material", source="Explicit SI numerical material", conductivity_w_mk=40))
    study.assignments.append(BodyAssignment(body_id="second", material_id="second_material"))
    case.thermal = [case.thermal[0]]
    if condition != "missing_anchor":
        case.thermal.append(ThermalBoundary(id="second_cold", kind="temperature", region_id="second:xmin", value=400, source="Separate thermostat"))
    if condition == "different_conductivity":
        for body in model.body_ids:
            case.thermal.append(ThermalBoundary(id=body+"_heat", kind="heat_power", region_id=body+":xmax", value=.08, source="Declared equal 0.08 W sources"))
    if condition == "missing_anchor":
        with pytest.raises(ValueError, match="Every disconnected thermal solid"):
            solve(request(project, domain, study), tmp_path)
        return
    result = solve(request(project, domain, study), tmp_path)
    for e in model.mesh.elements:
        origin, base, conductivity = (0, 300, 20) if e.body_id == "part" else (20, 400, 40)
        for n in e.nodes:
            expected = base
            if condition == "different_conductivity":expected += .08*((model.mesh.nodes[n][0]-origin)/1000)/(conductivity*.002**2)
            assert result["field"]["temperature_k"][n] == pytest.approx(expected, abs=1e-9)
    assert result["checks"]["heat_balance_error"] < 1e-8
    assert result["checks"]["residual"] < 1e-8
