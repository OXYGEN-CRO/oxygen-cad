"""Actual native material mass must survive check/bind/save/reopen consistently."""
import pytest

from o3d.brief import bind_evidence, evaluate
from o3d.brief_models import EngineeringBrief, BriefLink
from o3d.geometry import check
from o3d.model import Command, Material, Project, Requirement, digest, geometry_key
from o3d.native_document import execute_native, query_native
from o3d.store import Store, write_json


def save(store, project):
    return store.apply(Command(expected_revision=store.load().revision, actor="human", project=project))


def native(store, command, params):
    return execute_native(store, store.load().revision, "human", command, params)["project"]


def canonical_plate(tmp_path, material="Aluminum", limit=2):
    store = Store(tmp_path / "canonical")
    store.init(Project(material=Material(name="Legacy polymer fixture", density_g_cm3=1.2,
        source="Declared numerical fixture; no physical certificate")))
    native(store, "solid.box", {"length": 20, "width": 10, "height": 5, "body_name": "Plate"})
    if material is not None:
        native(store, "material.assign", {"bodies": ["Plate"], "material": material})
    project = store.load()
    project.requirements = [Requirement(id="mass", kind="max_mass", value=limit)]
    project.engineering_brief = EngineeringBrief(task="Check the explicitly assigned canonical plate material",
        links=[BriefLink(id="mass_link", requirement_id="mass")])
    save(store, project)
    return store


def bind_and_save(store):
    project = store.load()
    project.engineering_brief = bind_evidence(store)
    return save(store, project)


def test_aluminum_2_7g_cannot_pass_with_legacy_1_2g_density(tmp_path):
    store = canonical_plate(tmp_path)
    project = store.load()
    measured = query_native(project, "inspect.measure", store=store)
    assert measured["total"]["mass_g"] == pytest.approx(2.7)
    result = check(store, project)
    assert result["state"] == "failed"
    assert result["mass_g"] == pytest.approx(2.7)
    finding = next(f for f in result["findings"] if f["code"] == "max_mass")
    assert finding["value"] == pytest.approx(2.7) and finding["severity"] == "error" and finding["limit"] == 2
    assert result["mass_evidence"]["native_document_sha256"] == digest(project.native_document)
    assert result["mass_evidence"]["measurements_sha256"]
    body = result["mass_evidence"]["bodies"][0]
    assert body["material"] == "Aluminum" and body["density_g_cm3"] == 2.7
    assert body["body_id"] and body["assignment_source"] and body["density_source"]
    before = geometry_key(project)
    project = bind_and_save(store)
    assert geometry_key(project) == before
    reopened = Store(store.root)
    report = evaluate(reopened)
    assert report["state"] == report["links"][0]["status"] == "failed"
    assert report["links"][0]["value"] == pytest.approx(2.7)
    assert reopened.load().engineering_brief.links[0].evidence is not None


def test_unassigned_default_density_is_unknown_despite_legacy_material(tmp_path):
    store = canonical_plate(tmp_path, material=None, limit=10)
    measured = query_native(store.load(), "inspect.measure", store=store)
    assert measured["bodies"][0]["material"] == "Default"
    assert measured["total"]["mass_g"] > 0  # Kernel appearance default cannot become engineering input.
    result = check(store, store.load())
    assert result["state"] == "needs_input" and result["mass_g"] is None
    assert result["mass_evidence"]["state"] == "needs_input"
    assert any(f["code"] == "missing_material" and f["feature"] == "mass" for f in result["findings"])
    bind_and_save(store)
    assert store.load().engineering_brief.links[0].evidence is None
    assert evaluate(store)["state"] == "unknown"


def test_explicit_native_user_material_works_without_legacy_material(tmp_path):
    store = canonical_plate(tmp_path, material=None)
    native(store, "material.create", {"name": "Declared density fixture", "density_g_cm3": 1.2})
    native(store, "material.assign", {"bodies": ["Plate"], "material": "Declared density fixture"})
    project = store.load()
    project.material = None
    project = save(store, project)
    result = check(store, project)
    assert result["state"] == "passed" and result["mass_g"] == pytest.approx(1.2)
    bind_and_save(store)
    assert evaluate(store)["state"] == "passed"
    assert evaluate(store)["links"][0]["value"] == pytest.approx(1.2)


def test_mixed_material_bodies_are_summed_and_partial_assignments_never_pass(tmp_path):
    store = canonical_plate(tmp_path, limit=4)
    native(store, "solid.box", {"length": 10, "width": 10, "height": 10, "corner": [40, 0, 0], "body_name": "Second"})
    partial = check(store, store.load())
    assert partial["state"] == "needs_input" and partial["mass_g"] is None
    assert bind_evidence(store).links[0].evidence is None
    native(store, "material.create", {"name": "Second fixture density", "density_g_cm3": 1.2})
    native(store, "material.assign", {"bodies": ["Second"], "material": "Second fixture density"})
    result = check(store, store.load())
    assert result["state"] == "passed" and result["mass_g"] == pytest.approx(3.9)
    evidence = result["mass_evidence"]
    assert len({body["body_id"] for body in evidence["bodies"]}) == 2
    assert {body["material"] for body in evidence["bodies"]} == {"Aluminum", "Second fixture density"}
    bind_and_save(store)
    assert evaluate(store)["links"][0]["value"] == pytest.approx(3.9)


def test_changed_native_material_stales_evidence_and_rechecks_actual_mass(tmp_path):
    store = canonical_plate(tmp_path, limit=3)
    check(store, store.load())
    baseline = bind_and_save(store)
    assert evaluate(store)["state"] == "passed"
    native(store, "material.assign", {"bodies": ["Plate"], "material": "Steel"})
    assert store.load().material == baseline.material  # Legacy host material was not the native edit.
    assert evaluate(store)["state"] == "stale"
    assert bind_evidence(store).links[0].evidence is None
    current = query_native(store.load(), "inspect.measure", store=store)
    result = check(store, store.load())
    assert result["mass_g"] == pytest.approx(current["total"]["mass_g"])
    assert result["mass_g"] > 3 and result["state"] == "failed"
    bind_and_save(store)
    assert evaluate(store)["state"] == "failed"


def test_old_canonical_mass_check_and_receipt_without_material_basis_are_not_passes(tmp_path):
    store = canonical_plate(tmp_path, limit=3)
    result = check(store, store.load())
    project = bind_and_save(store)
    assert evaluate(store)["state"] == "passed"
    # Simulate a previously cached check contract, with its corresponding receipt.
    legacy = {key: value for key, value in result.items() if key != "mass_evidence"}
    path = store.cache / "builds" / geometry_key(project) / "checks.json"
    write_json(path, legacy)
    project.engineering_brief.links[0].evidence.source_sha256 = digest(legacy)
    save(store, project)
    assert evaluate(store)["state"] == "unknown"
    assert bind_evidence(store).links[0].evidence is None
    assert "Rerun checks" in evaluate(store)["links"][0]["reason"]


def test_native_user_density_edit_stales_mass_without_using_cached_measurements(tmp_path):
    store = canonical_plate(tmp_path, material=None, limit=3)
    native(store, "material.create", {"name": "Editable density", "density_g_cm3": 1.2})
    native(store, "material.assign", {"bodies": ["Plate"], "material": "Editable density"})
    first = check(store, store.load())
    bind_and_save(store)
    assert evaluate(store)["state"] == "passed"
    native(store, "material.edit", {"material": "Editable density", "density_g_cm3": 4.0})
    assert evaluate(store)["state"] == "stale"
    changed = check(store, store.load())
    assert changed["state"] == "failed" and changed["mass_g"] == pytest.approx(4)
    assert first["mass_evidence"]["measurements_sha256"] != changed["mass_evidence"]["measurements_sha256"]
    bind_and_save(store)
    assert evaluate(store)["state"] == "failed"


def test_canonical_component_instances_each_contribute_their_assigned_mass(tmp_path):
    store = Store(tmp_path / "instances")
    store.init(Project())
    made = execute_native(store, 1, "human", "component.create", {"name": "PlatePart", "grounded": False})["result"]
    native(store, "solid.box", {"length": 20, "width": 10, "height": 5, "body_name": "PlateBody"})
    native(store, "material.assign", {"bodies": ["PlateBody"], "material": "Aluminum"})
    native(store, "occurrence.copy", {"occurrence": made["occurrence"], "translate": [40, 0, 0]})
    project = store.load()
    project.requirements = [Requirement(id="mass", kind="max_mass", value=6)]
    project.engineering_brief = EngineeringBrief(task="Both physical occurrences count", links=[BriefLink(id="mass_link", requirement_id="mass")])
    project = save(store, project)
    result = check(store, project)
    assert result["state"] == "passed" and result["mass_g"] == pytest.approx(5.4)
    assert len(result["mass_evidence"]["bodies"]) == 2
    assert len({b["body_id"] for b in result["mass_evidence"]["bodies"]}) == 2
    assert {b["definition_name"] for b in result["mass_evidence"]["bodies"]} == {"PlateBody"}
    bind_and_save(store)
    assert evaluate(store)["state"] == "passed"
