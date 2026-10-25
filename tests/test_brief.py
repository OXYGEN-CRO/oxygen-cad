import json
from pathlib import Path
import shutil

import pytest
from pydantic import ValidationError

from o3d.brief import bind_evidence, evaluate, load_brief, validate_brief
from o3d.brief_models import BriefInput, BriefLink, EngineeringBrief
from o3d.geometry import check
from o3d.model import Command, Parameter, Project, Requirement, Study, geometry_key, study_key
from o3d.solver import simulate
from o3d.store import Conflict, Store, write_json


def example_brief():
    return EngineeringBrief(task="Support the explicitly specified benchmark load inside the envelope", inputs=[
        BriefInput(id="envelope", kind="envelope", description="Beam length", value=80, unit="mm",
                   provenance="Analytical cantilever fixture", parameter="length"),
        BriefInput(id="applied_load", kind="load", description="Load magnitude; direction and face remain in the study",
                   value=10, unit="N", provenance="Analytical cantilever fixture"),
        BriefInput(id="payload", kind="payload", description="Physical payload not yet specified"),
    ], links=[BriefLink(id="fit", requirement_id="length_limit", parameter_refs=["length"], input_refs=["envelope"]),
              BriefLink(id="mass", requirement_id="mass_limit")])


@pytest.fixture(scope="module")
def measured_project(tmp_path_factory):
    project = Project.model_validate_json((Path(__file__).resolve().parents[1] / "examples/cantilever.json").read_text())
    project.parameters["length"] = Parameter(value=80)
    project.features[1].distance = "$length"
    project.requirements += [Requirement(id="length_limit", kind="max_dimension", value=90, axis="X"),
                             Requirement(id="mass_limit", kind="max_mass", value=40)]
    project.engineering_brief = example_brief()
    store = Store(tmp_path_factory.mktemp("measured-brief") / "project")
    store.init(project)
    result = check(store, project)
    assert result["state"] == "passed"
    return store.root


@pytest.fixture
def measured_store(tmp_path, measured_project):
    shutil.copytree(measured_project, tmp_path / "project")
    return Store(tmp_path / "project")


def save(store, project):
    return store.apply(Command(expected_revision=store.load().revision, actor="human", project=project))


def bind_and_save(store):
    project = store.load()
    project.engineering_brief = bind_evidence(store)
    return save(store, project)


def test_old_project_and_standalone_roundtrip(beam, tmp_path):
    document = beam.model_dump(exclude={"engineering_brief"})
    assert Project.model_validate(document).engineering_brief is None
    brief = example_brief()
    path = tmp_path / "brief.json"
    path.write_text(brief.model_dump_json())
    assert load_brief(path) == load_brief(path.read_text()) == load_brief(brief.model_dump()) == brief
    assert "EngineeringBrief" in Project.model_json_schema()["$defs"]


@pytest.mark.parametrize("change", [
    {"inputs": [{"id": "load", "kind": "load", "description": "Load", "value": float("inf"), "unit": "N", "provenance": "test"}]},
    {"inputs": [{"id": "load", "kind": "load", "description": "Load", "value": 10}]},
    {"inputs": [{"id": "load", "kind": "load", "description": "Load", "value": 10, "unit": "N"}]},
    {"schema_version": 2},
])
def test_schema_rejects_nonfinite_unqualified_and_unversioned_inputs(change):
    with pytest.raises(ValidationError):
        load_brief(example_brief().model_dump() | change)


def test_duplicate_ids_and_refs_rejected():
    brief = example_brief().model_dump()
    for field in ("inputs", "links"):
        with pytest.raises(ValidationError, match="Duplicate"):
            load_brief(brief | {field: [brief[field][0], brief[field][0]]})
    with pytest.raises(ValidationError, match="Duplicate"):
        BriefLink(id="x", requirement_id="r", parameter_refs=["a", "a"])


def test_reference_units_and_ambiguity_validation(measured_store):
    project = measured_store.load()
    brief = project.engineering_brief
    brief.inputs[0].unit = "g"
    brief.links += [BriefLink(id="broken", requirement_id="deleted", parameter_refs=["deleted"], input_refs=["deleted"]),
                    BriefLink(id="simulation", requirement_id="deflection"),
                    BriefLink(id="missing_study", requirement_id="deflection", study_id="deleted")]
    project.requirements.append(project.requirements[1].model_copy())
    codes = {i["code"] for i in validate_brief(project)["issues"]}
    assert {"unit_mismatch", "missing_requirement", "missing_parameter", "missing_input", "ambiguous_requirement", "ambiguous_study", "missing_study"} <= codes
    with pytest.raises(ValueError, match="Repair brief references"):
        bind_evidence(measured_store, brief)


def test_native_measurements_bound_store_reopen_revision_and_label(measured_store):
    before = measured_store.load()
    assert evaluate(measured_store)["state"] == "unknown"
    project = bind_and_save(measured_store)
    assert project.revision == 2 and geometry_key(project) == geometry_key(before)
    reopened = Store(measured_store.root)
    report = evaluate(reopened)
    assert report["state"] == "passed"
    assert report["links"][0]["value"] == pytest.approx(80)
    assert report["links"][1]["value"] == pytest.approx(25.12)
    assert report["links"][0]["unit"] == "mm"
    assert report["links"][0]["evidence"]["requirement_signature"]
    with pytest.raises(Conflict, match="Revision conflict"):
        measured_store.apply(Command(expected_revision=1, project=project))
    project.engineering_brief.title = "Renamed label"
    save(measured_store, project)
    assert evaluate(measured_store)["state"] == "passed"
    project = measured_store.load()
    project.requirements[0].value = 10
    with pytest.raises(Conflict, match="user-owned"):
        measured_store.apply(Command(expected_revision=project.revision, actor="agent", project=project))


@pytest.mark.parametrize("change", ["geometry", "material", "limit", "input", "task", "parameter_units"])
def test_changes_invalidate_bound_identity(measured_store, change):
    project = bind_and_save(measured_store)
    if change == "geometry":
        project.features[0].height = 5
    elif change == "material":
        project.material.source = "Changed material provenance"
    elif change == "limit":
        project.requirements[1].value = 100
    elif change == "input":
        project.engineering_brief.inputs[1].value = 11
    elif change == "task":
        project.engineering_brief.task = "Changed use"
    else:
        project.parameters["length"].unit = "g"
    save(measured_store, project)
    report = evaluate(measured_store)
    assert report["state"] in ("stale", "unknown")
    assert all(row["status"] != "passed" for row in report["links"])


def test_disabled_deleted_and_unmeasured_evidence_never_passes(measured_store):
    project = bind_and_save(measured_store)
    project.requirements[1].enabled = False
    save(measured_store, project)
    assert evaluate(measured_store)["links"][0]["status"] == "unknown"
    project.requirements = [r for r in project.requirements if r.id != "length_limit"]
    save(measured_store, project)
    assert evaluate(measured_store)["links"][0]["status"] == "unknown"
    assert not evaluate(measured_store)["valid"]


def test_missing_changed_and_nonfinite_native_evidence(measured_store):
    project = bind_and_save(measured_store)
    path = measured_store.cache / "builds" / geometry_key(project) / "checks.json"
    measured = json.loads(path.read_text())
    path.unlink()
    assert evaluate(measured_store)["state"] == "unknown"
    measured["findings"] = []
    write_json(path, measured)
    assert evaluate(measured_store)["state"] == "stale"
    assert all(link.evidence is None for link in bind_evidence(measured_store).links)
    measured["mass_g"] = float("nan")
    path.write_text(json.dumps(measured))
    assert evaluate(measured_store)["state"] == "unknown"


def test_actual_failed_requirement_is_failed_and_no_other_pass(measured_store):
    project = measured_store.load()
    project.requirements[1].value = 40
    project = save(measured_store, project)
    result = check(measured_store, project)
    assert result["state"] == "failed"
    bind_and_save(measured_store)
    report = evaluate(measured_store)
    assert report["state"] == "failed"
    assert report["links"][0]["status"] == "failed"
    assert report["links"][1]["status"] == "unknown"


def test_unknown_linked_input_and_assumptions_are_not_certified(measured_store):
    project = bind_and_save(measured_store)
    project.engineering_brief.links[0].input_refs.append("payload")
    save(measured_store, project)
    assert evaluate(measured_store)["links"][0]["status"] == "unknown"
    assert bind_evidence(measured_store).links[0].evidence is None
    project.engineering_brief.inputs.append(BriefInput(id="condition", kind="assumption", description="Operating temperature unknown", unit="degC"))
    save(measured_store, project)
    assert all(row["status"] == "unknown" for row in evaluate(measured_store)["links"])


def test_real_missing_material_checks_do_not_bind(measured_store):
    project = measured_store.load()
    project.material = None
    project = save(measured_store, project)
    checks = check(measured_store, project)
    assert checks["state"] == "needs_input"
    assert any(f["code"] == "missing_material" and f["feature"] == "mass_limit" for f in checks["findings"])
    assert all(link.evidence is None for link in bind_evidence(measured_store).links)


def test_actual_simulation_measured_link_and_changed_study_material_and_limit(measured_store):
    project = measured_store.load()
    project.engineering_brief.links = [BriefLink(id="deflection_link", requirement_id="deflection", study_id="default", input_refs=["applied_load"])]
    project = save(measured_store, project)
    result = simulate(measured_store, project)
    assert result["state"] == "passed" and result["converged"]
    baseline = bind_and_save(measured_store)
    report = evaluate(measured_store)
    assert report["state"] == "passed"
    assert report["links"][0]["value"] == result["max_displacement_mm"]
    for change in ("force", "material", "limit", "mesh"):
        modified = baseline.model_copy(deep=True)
        if change == "force":
            modified.study.forces[0].vector_n = (0, 0, -20)
        elif change == "material":
            modified.material.youngs_modulus_mpa = 100000
        elif change == "limit":
            modified.requirements[0].value = .3
        else:
            modified.study.mesh_size_mm = 2
        save(measured_store, modified)
        assert evaluate(measured_store)["state"] == "stale"
    save(measured_store, baseline)
    assert evaluate(measured_store)["state"] == "passed"
    path = measured_store.cache / "studies" / study_key(baseline, "default") / "result.json"
    actual = json.loads(path.read_text())
    for incomplete in ({"converged": False, "state": "inconclusive"},
                       {"findings": [{"id": "deflection", "status": "not_evaluated", "value": None, "limit": .2, "passed": False}]}):
        write_json(path, actual | incomplete)
        assert evaluate(measured_store)["state"] == "stale"
        assert bind_evidence(measured_store).links[0].evidence is None
    write_json(path, actual)
    assert evaluate(measured_store)["state"] == "passed"


def test_scoped_study_deleted_missing_and_wrong_scope(measured_store):
    project = measured_store.load()
    project.named_studies["alternate"] = Study(name="Alternate")
    project.requirements[0].study_id = "alternate"
    project.engineering_brief.links = [BriefLink(id="simulation", requirement_id="deflection", study_id="default")]
    assert validate_brief(project)["issues"][0]["code"] == "study_scope_mismatch"
    project.engineering_brief.links[0].study_id = "alternate"
    assert validate_brief(project)["valid"]
    # A deleted linked study is preserved as an explicit unresolved brief reference.
    project.requirements[0].study_id = None
    project.named_studies = {}
    assert validate_brief(project)["issues"][0]["code"] == "missing_study"


def test_deleted_canonical_parameter_mirror_cannot_validate_or_rebind(tmp_path):
    from o3d.native_document import execute_native
    store = Store(tmp_path / "deleted-native-parameter")
    store.init(Project(parameters={"spare": Parameter(value=5)}, requirements=[
        Requirement(id="width_limit", kind="max_dimension", axis="X", value=25)],
        engineering_brief=EngineeringBrief(task="Explicit parameter reference", links=[
            BriefLink(id="fit", requirement_id="width_limit", parameter_refs=["spare"])])))
    execute_native(store, 1, "human", "solid.box", {"length": 20, "width": 10, "height": 5})
    check(store, store.load())
    bind_and_save(store)
    assert evaluate(store)["state"] == "passed"
    execute_native(store, store.load().revision, "human", "parameters.delete", {"name": "spare"})
    project = store.load()
    assert "spare" in project.parameters and not any(p["name"] == "spare" for p in project.native_document["params"])
    check(store, project)
    assert evaluate(store)["state"] == "unknown"
    assert validate_brief(project)["issues"][0]["code"] == "missing_parameter"
    with pytest.raises(ValueError, match="Canonical parameter spare no longer exists"):
        bind_evidence(store)


def test_canonical_parameter_alias_mirrors_preserve_values_and_detect_ambiguity(tmp_path):
    from o3d.native_document import execute_native
    store = Store(tmp_path / "canonical-aliases")
    store.init(Project(parameters={"reach": Parameter(value=20)},
        requirements=[Requirement(id="limit", kind="max_dimension", value=25)],
        engineering_brief=EngineeringBrief(task="Check a mirrored native parameter", inputs=[
            BriefInput(id="reach_input", kind="envelope", description="Reach", value=20, unit="mm", provenance="Numerical fixture", parameter="reach")],
            links=[BriefLink(id="fit", requirement_id="limit", parameter_refs=["reach"])])))
    execute_native(store, 1, "human", "solid.box", {"length": 20, "width": 10, "height": 5})
    execute_native(store, 2, "human", "parameters.rename", {"name": "reach", "new_name": "span"})
    baseline = store.load()
    assert baseline.domains["modeling"]["legacy_parameter_map"]["reach"] == "span"
    assert validate_brief(baseline)["valid"]
    divergent = baseline.model_copy(deep=True)
    divergent.parameters["reach"].value = 21
    assert "parameter_mirror_mismatch" in {i["code"] for i in validate_brief(divergent)["issues"]}
    ambiguous = baseline.model_copy(deep=True)
    ambiguous.native_document["params"].append(next(p.copy() for p in ambiguous.native_document["params"] if p["name"] == "span"))
    assert "ambiguous_parameter" in {i["code"] for i in validate_brief(ambiguous)["issues"]}
    aliases = baseline.model_copy(deep=True)
    aliases.parameters["other"] = Parameter(value=99)
    aliases.domains["modeling"]["legacy_parameter_map"]["other"] = "span"
    assert "ambiguous_parameter" in {i["code"] for i in validate_brief(aliases)["issues"]}
    execute_native(store, 3, "human", "parameters.add", {"name": "native_only", "expression": "20 mm"})
    unmatched = store.load()
    unmatched.engineering_brief.links[0].parameter_refs = ["native_only"]
    assert validate_brief(unmatched)["issues"][0]["code"] == "unresolved_parameter"
    formula = baseline.model_copy(deep=True)
    next(p for p in formula.native_document["params"] if p["name"] == "span")["expr"] = "10 + 10"
    assert "unresolved_parameter" in {i["code"] for i in validate_brief(formula)["issues"]}
    wrong_units = baseline.model_copy(deep=True)
    next(p for p in wrong_units.native_document["params"] if p["name"] == "span")["unit"] = ""
    assert "unit_mismatch" in {i["code"] for i in validate_brief(wrong_units)["issues"]}
    current = store.load()
    check(store, current)
    bind_and_save(store)
    assert evaluate(store)["state"] == "passed"
    retargeted = store.load()
    retargeted.domains["modeling"]["legacy_parameter_map"]["reach"] = "native_only"
    before = geometry_key(retargeted)
    retargeted = save(store, retargeted)
    assert geometry_key(retargeted) == before and validate_brief(retargeted)["valid"]
    assert evaluate(store)["state"] == "stale"  # Equal numeric values do not preserve target identity.
    # A source-level native edit may retain a divergent host mirror; saving the
    # snapshot must never let this link rebind to an unrelated passing check.
    divergent_snapshot = store.load()
    next(p for p in divergent_snapshot.native_document["params"] if p["name"] == "native_only")["expr"] = "21 mm"
    divergent_snapshot = save(store, divergent_snapshot)
    check(store, divergent_snapshot)
    assert evaluate(store)["state"] == "unknown"
    with pytest.raises(ValueError, match="differs from canonical"):
        bind_evidence(store)
