"""Bounded registration mathematics and real Rust/independent STEP adapter acceptance."""
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from o3d.compatibility import compare, propose_adapter, source_signature, validate, validate_adapter
from o3d.model import Extrude, Parameter, Profile, Project


@pytest.fixture
def adapter_request():
    return json.loads((Path(__file__).resolve().parents[1] / "examples/component-interfaces.json").read_text())


def direct(adapter_request):
    return {"a": deepcopy(adapter_request["a"]), "b": deepcopy(adapter_request["a"]), "fastener_diameter_mm": 4}


def test_evaluated_scope_never_approves_overall_fit(adapter_request):
    result = compare(direct(adapter_request))
    assert result.status == "compatible"
    assert result.overall_status == "unknown"
    assert result.scope == "mechanical_mounting_registration_only"
    assert result.reasons[0].measured_mm == pytest.approx(0.1)
    assert result.reasons[0].limit_mm == pytest.approx(0.1)
    assert any("electrical" in limit for limit in result.limits)


def test_sourced_pattern_mismatch_is_measured(adapter_request):
    result = compare({"a": adapter_request["a"], "b": adapter_request["b"], "fastener_diameter_mm": 4})
    assert result.status == "incompatible"
    assert result.reasons[0].measured_mm == pytest.approx(20.1)


@pytest.mark.parametrize("field", ["asset_sha256", "evidence", "frame", "holes", "tolerance"])
def test_missing_input_is_unknown_not_a_fit(adapter_request, field):
    value = direct(adapter_request)
    value["b"].pop(field)
    result = compare(value)
    assert result.status == "unknown"
    assert result.reasons[0].code == "missing_data"


def test_missing_fastener_and_unqualified_electrical_fields(adapter_request):
    value = direct(adapter_request)
    value.pop("fastener_diameter_mm")
    assert compare(value).status == "unknown"
    value["a"]["bus"] = "I2C compatible"
    with pytest.raises(ValidationError):
        compare(value)


def test_correspondence_must_be_explicit(adapter_request):
    value = direct(adapter_request)
    value["b"]["holes"][0]["label"] = "not-corresponding"
    assert compare(value).status == "unknown"
    value["b"]["holes"].pop()
    assert compare(value).status == "incompatible"


def test_exact_units_and_rotated_frame_are_applied(adapter_request):
    value = direct(adapter_request)
    b = value["b"]
    b["units"] = "in"
    b["frame"]["x_axis"] = [0, 1, 0]
    b["frame"]["y_axis"] = [-1, 0, 0]
    b["frame"]["origin"] = [2, 3, 0]
    for ah, bh in zip(value["a"]["holes"], b["holes"]):
        x, y = ah["center"]
        bh["center"] = [y/25.4-3, -(x/25.4-2)]
        bh["diameter"] /= 25.4
    b["tolerance"] = {key: val/25.4 for key, val in b["tolerance"].items()}
    result = compare(value)
    assert result.status == "compatible"
    assert all(r.measured_mm == pytest.approx(0.1) for r in result.reasons)


def test_opposite_normals_on_same_plane_supported(adapter_request):
    value = direct(adapter_request)
    value["b"]["frame"]["y_axis"] = [0, -1, 0]
    for hole in value["b"]["holes"]:
        hole["center"][1] *= -1
    assert compare(value).status == "compatible"


@pytest.mark.parametrize("change", ["frame_name", "nonparallel", "noncoplanar"])
def test_unsupported_common_frames_return_unknown_and_adapter_refuses(adapter_request, change):
    value = direct(adapter_request)
    frame = value["b"]["frame"]
    if change == "frame_name": frame["assembly_frame"] = "unregistered"
    if change == "nonparallel": frame["y_axis"] = [0, 0, 1]
    if change == "noncoplanar": frame["origin"] = [0, 0, 0.001]
    assert compare(value).status == "unknown"
    adapter_request["b"] = value["b"]
    with pytest.raises(ValueError):
        propose_adapter(adapter_request)


@pytest.mark.parametrize("field,val", [("units", "cm"), ("units", "m"), ("asset_sha256", "bad")])
def test_bad_units_and_identity_rejected(adapter_request, field, val):
    adapter_request["a"][field] = val
    with pytest.raises(ValidationError): validate(adapter_request["a"])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), 1e308])
def test_nonfinite_or_overflowing_geometry_rejected(adapter_request, value):
    adapter_request["a"]["holes"][0]["center"][0] = value
    with pytest.raises(ValidationError): validate(adapter_request["a"])


@pytest.mark.parametrize("value", [True, "4.2"])
def test_numeric_inputs_are_not_coerced_from_bool_or_text(adapter_request, value):
    interface = deepcopy(adapter_request["a"])
    interface["holes"][0]["diameter"] = value
    with pytest.raises(ValidationError): validate(interface)
    adapter_request["thickness_mm"] = value
    with pytest.raises(ValidationError): propose_adapter(adapter_request)


def test_bad_frame_and_duplicate_labels_rejected(adapter_request):
    value = deepcopy(adapter_request["a"])
    value["frame"]["x_axis"] = [2, 0, 0]
    with pytest.raises(ValidationError): validate(value)
    value = deepcopy(adapter_request["a"])
    value["holes"][1]["label"] = value["holes"][0]["label"]
    with pytest.raises(ValidationError): validate(value)


def test_tolerance_boundary_and_small_negative_margin(adapter_request):
    value = direct(adapter_request)
    assert compare(value).status == "compatible"
    value["b"]["holes"][0]["center"][0] += 0.00001
    assert compare(value).status == "incompatible"
    value = direct(adapter_request)
    value["fastener_diameter_mm"] = 4.10001
    assert compare(value).status == "incompatible"
    value["a"]["tolerance"]["diameter"] = 4.2
    with pytest.raises(ValidationError): compare(value)


def test_source_hash_and_geometry_changes_change_interface_signature(adapter_request):
    a = adapter_request["a"]
    original = source_signature(a)
    for key, change in [("asset_sha256", "e"*64), ("sku", "another-sku"), ("interface_id", "mount-2")]:
        changed = deepcopy(a)
        changed[key] = change
        assert source_signature(changed).signature != original.signature
    changed = deepcopy(a)
    changed["evidence"]["sha256"] = "f"*64
    assert source_signature(changed).signature != original.signature
    changed = deepcopy(a)
    changed["holes"][0]["center"][0] += 0.1
    assert source_signature(changed).signature != original.signature


def test_editable_adapter_dimensions_sources_and_acceptance(adapter_request):
    proposal = propose_adapter(adapter_request)
    assert proposal.project.features == []
    assert proposal.project.native_document["format"]
    assert [f["type"] for f in proposal.project.native_document["features"]] == ["sketch", "extrude"]*5
    assert proposal.project.domains["modeling"]["native_context"]["active_sketch"] is None
    assert proposal.project.parameters["adapter_thickness"].value == 3
    assert proposal.acceptance.width_mm == pytest.approx(30.6)
    assert proposal.acceptance.height_mm == pytest.approx(30.6)
    assert proposal.acceptance.minimum_web_mm == pytest.approx(15.4)
    assert min(h.edge_clearance_mm for h in proposal.acceptance.holes) == pytest.approx(3)
    assert proposal.acceptance.expected_volume_mm3 == pytest.approx((30.6**2-4*math.pi*2.2**2)*3)
    assert proposal.acceptance.measured_volume_mm3 == pytest.approx(proposal.acceptance.expected_volume_mm3)
    assert proposal.acceptance.method == "independent_native_step_geometry"
    assert validate_adapter(proposal) == proposal.acceptance
    assert propose_adapter(adapter_request).model_dump() == proposal.model_dump()
    assert proposal.sources == [source_signature(adapter_request["a"]), source_signature(adapter_request["b"])]


def test_adapter_units_and_transforms_are_numeric(adapter_request):
    original = propose_adapter(adapter_request)
    a = adapter_request["a"]
    a["units"] = "in"
    a["frame"]["origin"] = [1, 0, 0]
    for hole in a["holes"]:
        hole["center"] = [hole["center"][0]/25.4-1, hole["center"][1]/25.4]
        hole["diameter"] /= 25.4
    a["tolerance"] = {k: v/25.4 for k, v in a["tolerance"].items()}
    transformed = propose_adapter(adapter_request)
    assert transformed.acceptance.width_mm == pytest.approx(original.acceptance.width_mm)
    for ah, bh in zip(original.acceptance.holes, transformed.acceptance.holes):
        assert ah.center_mm == pytest.approx(bh.center_mm)
        assert ah.diameter_mm == pytest.approx(bh.diameter_mm)


def test_adapter_rejects_impossible_geometry_and_small_plate(adapter_request):
    adapter_request["width_mm"] = 20
    with pytest.raises(ValueError, match="outside plate|edge clearance"): propose_adapter(adapter_request)
    adapter_request.pop("width_mm")
    adapter_request["b"]["holes"][0]["center"] = [-10, -10]
    with pytest.raises(ValueError, match="Overlapping"): propose_adapter(adapter_request)


def test_adapter_never_overwrites_geometry_or_parameter_locks(adapter_request):
    populated = Project(features=[Profile(id="existing"), Extrude(id="solid", profile="existing")])
    with pytest.raises(ValueError, match="empty modeled project"): propose_adapter(adapter_request, populated)
    empty = Project(parameters={"adapter_width": Parameter(value=77, locked=True)})
    with pytest.raises(ValueError, match="conflict"): propose_adapter(adapter_request, empty)
    assert empty.parameters["adapter_width"].value == 77
    empty = Project(revision=7, parameters={"kept": Parameter(value=12, locked=True)})
    proposal = propose_adapter(adapter_request, empty)
    assert proposal.project.revision == 7
    assert proposal.project.parameters["kept"].locked
    assert empty.features == []


@pytest.mark.parametrize("change", ["center", "diameter", "thickness", "suppressed", "source", "placement", "parameter_unit"])
def test_adapter_numeric_recheck_detects_changes(adapter_request, change):
    proposal = propose_adapter(adapter_request)
    if change == "center": proposal.project.native_document["features"][2]["plane"]["plane"]["origin"][0] += 1
    if change == "diameter": proposal.project.parameters["adapter_a_1_diameter"].value += 0.01
    if change == "thickness": proposal.project.parameters["adapter_thickness"].value += 0.01
    if change == "suppressed": proposal.project.native_document["features"][3]["suppressed"] = True
    if change == "source": proposal.request.a.asset_sha256 = "f"*64
    if change == "placement": proposal.project.placement.translation = (1, 0, 0)
    if change == "parameter_unit": proposal.project.parameters["adapter_thickness"].unit = "deg"
    with pytest.raises(ValueError): validate_adapter(proposal)


def test_real_rust_adapter_and_independent_step_hole_measurements(adapter_request, tmp_path):
    import build123d as bd
    from o3d.native import compile_native
    proposal = propose_adapter(adapter_request)
    shape, metadata = compile_native(proposal.project, tmp_path)
    assert metadata["independent_volume_relative_error"] < 1e-3
    # STEP is reimported independently via OCCT, rather than trusting a native response.
    exported = bd.import_step(tmp_path / "part.step")
    assert exported.is_valid and len(exported.solids()) == 1
    assert exported.volume == pytest.approx(proposal.acceptance.expected_volume_mm3, rel=1e-7)
    assert list(exported.bounding_box().size) == pytest.approx([30.6, 30.6, 3], abs=1e-6)
    cylinders = [f for f in exported.faces() if f.geom_type == bd.GeomType.CYLINDER]
    assert len(cylinders) == 4
    actual = sorted(((f.axis_of_rotation.position.X, f.axis_of_rotation.position.Y, 2*f.radius) for f in cylinders), key=lambda row: tuple(round(v, 6) for v in row))
    expected = sorted((*hole.center_mm[:2], hole.diameter_mm) for hole in proposal.acceptance.holes)
    for a, b in zip(actual, expected):
        assert a == pytest.approx(b, abs=1e-6)
    # The real engine rebuilds a user edit from editable parameters.
    from o3d.native_document import prepare_native_command
    edited_project, _ = prepare_native_command(proposal.project, "parameters.change", {"name": "adapter_thickness", "expression": "5 mm"})
    assert edited_project.parameters["adapter_thickness"].value == pytest.approx(5)
    edited_dir = tmp_path / "edited"
    edited_dir.mkdir()
    compile_native(edited_project, edited_dir)
    edited = bd.import_step(edited_dir / "part.step")
    assert edited.volume == pytest.approx(proposal.acceptance.expected_volume_mm3*5/3, rel=1e-7)
    assert list(edited.bounding_box().size) == pytest.approx([30.6, 30.6, 5], abs=1e-6)


def test_existing_canonical_multibody_project_is_never_overwritten(adapter_request):
    from o3d.native import NativeSession
    from o3d.brief_models import EngineeringBrief
    from o3d.model import Requirement, Study
    with NativeSession() as session:
        session.execute("solid.box", {"length": 10, "width": 12, "height": 3, "body_name": "First"})
        session.execute("solid.box", {"length": 8, "width": 6, "height": 2, "corner": [40, 0, 0], "body_name": "Second"})
        snapshot = session.execute("oxygen.document.get")
    joint_data = json.loads((Path(__file__).resolve().parents[1]/"src/o3d/data/joint-example.json").read_text())
    project = Project(native_document=snapshot["document"],
        domains={"modeling": {"schema_version": 1, "native_context": snapshot["context"], "parameter_locks": {"user_owned": True}}},
        named_studies={"alternate": Study(name="Preserved")}, active_study_id="alternate",
        requirements=[Requirement(id="mass_limit", kind="max_mass", value=100)],
        engineering_brief=EngineeringBrief.model_validate(joint_data))
    before = project.model_dump(mode="json")
    with pytest.raises(ValueError, match="no native document"):
        propose_adapter(adapter_request, project)
    assert project.model_dump(mode="json") == before
    # Fresh generation must also leave the active design and its selections untouched.
    propose_adapter(adapter_request)
    assert project.model_dump(mode="json") == before


def test_empty_project_metadata_and_store_guards_survive_canonical_proposal(adapter_request, tmp_path):
    from o3d.brief_models import EngineeringBrief
    from o3d.model import Command, Study
    from o3d.native_document import execute_native
    from o3d.store import Conflict, Store
    joint_data = json.loads((Path(__file__).resolve().parents[1]/"src/o3d/data/joint-example.json").read_text())
    project = Project(parameters={"kept": Parameter(value=12, locked=True)},
        named_studies={"alternate": Study(name="Preserved")}, active_study_id="alternate",
        engineering_brief=EngineeringBrief.model_validate(joint_data))
    proposal = propose_adapter(adapter_request, project)
    assert proposal.project.engineering_brief == project.engineering_brief
    assert proposal.project.named_studies == project.named_studies
    assert proposal.project.active_study_id == "alternate"
    assert proposal.project.domains["modeling"]["parameter_locks"]["kept"]
    store = Store(tmp_path)
    store.init(project)
    applied = store.apply(Command(expected_revision=1, actor="agent", project=proposal.project))
    assert applied.native_document is not None
    with pytest.raises(Conflict, match="Revision conflict"):
        store.apply(Command(expected_revision=1, actor="agent", project=proposal.project))
    with pytest.raises(Conflict, match="locked"):
        execute_native(store, 2, "agent", "parameters.change", {"name": "kept", "expression": "13 mm"})
    assert store.load().parameters["kept"].value == 12


def test_canonical_parameter_edit_invalidates_request_acceptance(adapter_request):
    from o3d.native_document import prepare_native_command
    proposal = propose_adapter(adapter_request)
    original_signature = proposal.acceptance.native_geometry_signature
    proposal.project, _ = prepare_native_command(proposal.project, "parameters.change", {"name": "adapter_a_1_diameter", "expression": "5 mm"})
    from o3d.model import geometry_key
    assert geometry_key(proposal.project) != original_signature
    with pytest.raises(ValueError, match="dimensions or units changed"):
        validate_adapter(proposal)


def test_cached_acceptance_cannot_substitute_for_actual_geometry(adapter_request):
    proposal = propose_adapter(adapter_request)
    proposal.acceptance.measured_volume_mm3 = 1
    checked = validate_adapter(proposal)
    assert checked.measured_volume_mm3 == pytest.approx(checked.expected_volume_mm3)
    # Mutating only the canonical parameter bypasses the mirror but must fail the
    # independent export check, rather than accepting an inactive legacy graph.
    param = next(p for p in proposal.project.native_document["params"] if p["name"] == "adapter_a_1_diameter")
    param["expr"] = "5"
    with pytest.raises(ValueError, match="Exported adapter"):
        validate_adapter(proposal)


def test_adapter_refuses_external_links_before_native_loading(adapter_request, monkeypatch):
    proposal = propose_adapter(adapter_request)
    proposal.project.native_document["links"] = {"external": {"path": "/tmp/unsupported-private-source"}}
    def forbidden(*args, **kwargs):
        pytest.fail("Untrusted adapter links reached native compilation")
    monkeypatch.setattr("o3d.native.compile_native", forbidden)
    with pytest.raises(ValueError, match="external links"):
        validate_adapter(proposal)
