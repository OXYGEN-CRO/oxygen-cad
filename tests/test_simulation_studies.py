import json
import math

import numpy as np
import pytest

from o3d.analysis import capabilities, validate_study
from o3d.analysis_mesh import validate_mesh
from o3d.model import Command, HeatSource, Requirement, TemperatureBoundary, study_key
from o3d.solver import simulate


@pytest.mark.parametrize("mesher", ["netgen", "gmsh"])
def test_static_displacement_and_nonsingular_stress_against_theory(store, mesher):
    p=store.load()
    p.study.mesher=mesher
    p.study.convergence_tolerance=.02
    result=simulate(store,p)
    inertia=10*4**3/12
    expected_displacement=10*80**3/(3*200000*inertia)
    expected_stress=10*(80-40)*1.5/inertia
    assert result["max_displacement_mm"]==pytest.approx(expected_displacement,rel=.02)
    assert result["iterations"][-1]["stress_probe_mpa"]==pytest.approx(expected_stress,rel=.05)
    assert len(result["iterations"])==3
    assert result["force_balance_error"]<.01
    assert result["moment_balance_error"]<.01
    mapping=json.loads(store.artifact(result["artifacts"]["region_map"]).read_text())
    assert mapping["aliases"]["fixed_0"]!=mapping["aliases"]["load_0"]
    assert all("bounds" in region and "normal" in region for region in mapping["regions"])


@pytest.mark.parametrize("mesher", ["netgen", "gmsh"])
def test_modal_frequency_against_cantilever_theory(store, mesher):
    p = store.load()
    p.study.type = "modal"
    p.study.mesher = mesher
    p.study.forces = []
    p.study.stress_probe_mm = None
    p.study.mode_count = 2
    p.study.mesh_size_mm = 4
    p.requirements = [Requirement(id="first_mode", kind="min_frequency", value=400)]
    result = simulate(store, p)
    # Euler-Bernoulli cantilever, first bending mode, tonne/mm^3 mass units.
    inertia = 10*4**3/12
    expected = 1.8751040687**2/(2*math.pi*80**2)*math.sqrt(200000*inertia/(7.85e-9*40))
    assert result["frequencies_hz"][0] == pytest.approx(expected, rel=.03)
    assert result["state"] == "passed"
    assert result["physical_validation"] == "not_performed"
    field = json.loads(store.artifact(result["field_artifact"]).read_text())
    assert len(field["modes"]) == 2
    assert len(field["modes"][0]["vectors"]) == len(field["nodes"])
    assert "arbitrary" in field["mode_amplitude"]
    assert result["mode_tracking"]["passed"]
    assert result["mode_tracking"]["quadrature_sensitivity"] < .005
    assert len(field["guard_modes"]) == 4
    for iteration in result["iterations"][1:]:
        assert iteration["mode_tracking"]["passed"]
        assert min(g["minimum_squared_cosine"] for g in iteration["mode_tracking"]["groups"]) >= .98
    assert result["input_deck_sha256"]


def thermal_setup(store, coupled=False):
    p = store.load()
    p.study.type = "thermal_stress" if coupled else "thermal"
    p.study.forces = []
    p.study.fixed_faces = ["part:xmin"] if coupled else []
    p.study.stress_probe_mm = None
    p.study.mesh_size_mm = 4
    p.study.probe_point_mm = (40,0,0)
    p.study.temperatures = [TemperatureBoundary(face="part:xmin", temperature_c=0)]
    p.study.heat_sources = [HeatSource(face="part:xmax", power_w=1)]
    p.material.thermal_conductivity_w_m_k = 20
    p.material.thermal_expansion_per_k = 12e-6
    p.study.reference_temperature_c = 0
    p.requirements = []
    return p


@pytest.mark.parametrize("mesher", ["netgen", "gmsh"])
def test_steady_thermal_power_units_energy_balance_and_probe(store, mesher):
    p = thermal_setup(store)
    p.study.mesher = mesher
    p = store.apply(Command(expected_revision=p.revision, actor="human", project=p))
    result = simulate(store,p)
    # One watt through 80 mm length, 40 mm^2 area, k=20 W/(m K).
    expected = 1*.08/(20*40e-6)
    assert result["max_temperature_c"] == pytest.approx(expected,rel=.005)
    assert result["probe_temperature_c"] == pytest.approx(expected/2,rel=.005)
    assert result["heat_balance_error"] < .01
    assert sum(result["heat_reactions_w"].values()) == pytest.approx(-1,rel=.005)
    assert result["state"] == "passed"
    field=json.loads(store.artifact(result["field_artifact"]).read_text())
    assert len(field["temperature_c"]) == len(field["nodes"])
    from o3d.artifacts import export_bundle
    import zipfile
    release=export_bundle(store,p)
    with zipfile.ZipFile(store.artifact(release["artifact"])) as archive:
        import hashlib
        manifest = json.loads(archive.read("release-manifest.json"))
        assert set(archive.namelist()) == set(manifest["files"]) | {"release-manifest.json"}
        for name, metadata in manifest["files"].items():
            content = archive.read(name)
            assert metadata == {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
        package=json.loads(archive.read("analysis/study-package.json"))
        assert package["regions"]["step"]=="geometry/part.step"
        assert archive.read("analysis/geometry/part.step").startswith(b"ISO-10303-21;")
        assert archive.read("analysis/field.json")
        assert any(n.endswith("thermal.inp") for n in archive.namelist())
        assert any(n.endswith("thermal.dat") for n in archive.namelist())


def test_sequential_thermal_stress_matches_free_expansion(store):
    p=thermal_setup(store,True)
    p.study.probe_point_mm=(80,0,0)
    result=simulate(store,p)
    # Linear 0..100 C gradient; mean temperature rise 50 C.
    assert result["max_displacement_mm"] == pytest.approx(12e-6*50*80,rel=.02)
    assert result["force_balance_error"] < .01
    assert result["moment_balance_error"] < .01
    assert result["state"] == "passed"
    assert any("thermal.inp" in name for name in result["artifacts"])
    assert any("analysis.inp" in name for name in result["artifacts"])


def test_buckling_factor_against_euler_column(store):
    p=store.load()
    p.study.type="buckling"
    p.study.stress_probe_mm=None
    p.study.forces[0].vector_n=(-1,0,0)
    p.study.mode_count=2
    p.study.mesh_size_mm=4
    p.requirements=[Requirement(id="buckling",kind="min_buckling_factor",value=4000)]
    result=simulate(store,p)
    expected=math.pi**2*200000*(10*4**3/12)/(4*80**2)
    assert result["buckling_factors"][0]==pytest.approx(expected,rel=.03)
    assert result["state"]=="passed"
    assert result["mode_tracking"]["passed"]


def test_missing_inputs_unused_physics_and_unsupported_studies_refuse(store):
    p=thermal_setup(store)
    p.material.thermal_conductivity_w_m_k=None
    assert any("conductivity" in s for s in validate_study(p))
    with pytest.raises(ValueError,match="conductivity"):simulate(store,p)
    p=store.load();p.study.type="modal"
    assert any("forces" in s for s in validate_study(p))
    p.study.type="dynamic_event"
    with pytest.raises(ValueError,match="Unsupported"):simulate(store,p)
    cap=capabilities()
    assert len(cap["studies"])==11
    assert next(s for s in cap["studies"] if s["type"]=="dynamic_event")["status"]=="unavailable"


def test_thermal_edit_invalidates_result_and_appearance_does_not(store):
    p=thermal_setup(store);before=study_key(p)
    p.material.thermal_conductivity_w_m_k=30
    assert study_key(p)!=before
    p=thermal_setup(store);before=study_key(p)
    p.study.heat_sources[0].power_w=2
    assert study_key(p)!=before


def test_degenerate_and_disconnected_meshes_refuse():
    nodes={1:[0,0,0],2:[1,0,0],3:[0,1,0],4:[1,1,0],5:[.5,0,0],6:[.5,.5,0],7:[0,.5,0],8:[.5,.5,0],9:[1,.5,0],10:[.5,1,0]}
    with pytest.raises(ValueError,match="Degenerate"):
        validate_mesh({"nodes":nodes,"elements":[list(nodes)],"surfaces":{}})
