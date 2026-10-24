"""Measured exchange/preparation workflows using disposable project assets."""
import base64
import hashlib
import threading

import numpy as np
import pytest
import trimesh

from o3d.domains import interop
from o3d.model import Command, Project
from o3d.store import Store


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path)
    store.init(Project(name="Disposable exchange acceptance"))
    return store


def apply_result(store, result):
    assert result["changed"] is True
    before = store.load()
    return store.apply(Command(expected_revision=before.revision, actor="human",
                               project=Project.model_validate(result["project"]), summary="Exchange test"))


def upload(store, content, name, units="mm"):
    return interop.execute(store, store.load(), "import", {
        "content_base64": base64.b64encode(content).decode(), "name": name, "units": units})


def measure(store):
    from o3d.native_document import query_native
    return query_native(store.load(), "inspect.measure", {}, store=store)


def test_asset_hash_scope_and_missing_source_units(store):
    source = interop._put_asset(store, b"example", "example.obj", "obj")
    assert interop._asset_path(store, source).read_bytes() == b"example"
    with pytest.raises(ValueError, match="content-addressed"):
        interop.Asset.model_validate(source.model_dump() | {"file": "../outside.obj"})
    (store.root / source.file).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash"):
        interop._asset_path(store, source)
    with pytest.raises(ValueError, match="source units"):
        upload(store, b"solid\nendsolid", "part.stl", "file")
    with pytest.raises(ValueError, match="declared file units"):
        upload(store, b"ISO-10303-21;", "part.step", "in")


def test_mesh_topology_repair_reports_real_boundary_change(store):
    mesh = trimesh.creation.box(extents=[10, 20, 30])
    mesh.update_faces(np.arange(len(mesh.faces) - 1))
    before = interop.mesh_diagnostics(mesh)
    assert before["boundary_edges"] == 3
    assert before["watertight"] is False
    assert before["volume_mm3"] is None
    asset = interop._put_asset(store, mesh.export(file_type="stl"), "open.stl", "stl")
    project = interop._with_assets(store.load(), asset)
    result = interop.execute(store, project, "mesh.repair", {"asset_id": asset.id, "insert": False})
    after = result["result"]["after"]
    repaired = interop._asset_path(store, interop.Asset.model_validate(result["result"]["asset"]))
    # Independently reopen the actual derived exchange artifact.
    loaded = trimesh.load(repaired, force="mesh")
    assert loaded.is_watertight
    assert loaded.volume == pytest.approx(6000)
    assert after["boundary_edges"] == 0
    assert after["watertight"] is True
    assert after["volume_mm3"] == pytest.approx(6000)
    assert after["limits"]  # Topology is not a self-intersection guarantee.


def test_cancelled_operation_and_unavailable_form_are_explicit(store):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        interop.execute(store, store.load(), "export", {}, cancel)
    for operation in ["form.box", "form.convert", "surface.fillet"]:
        with pytest.raises(ValueError, match="(no public|no geometric)"):
            interop.execute(store, store.load(), operation, {})
    assert store.load().revision == 1
    assert not interop.capabilities()["form"]["available"]


def test_dxf_declared_units_and_no_silent_unitless_assumption():
    dxf = "0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n1\n0\nENDSEC\n0\nEOF\n"
    assert interop._drawing_scale(dxf, "file") == ("in", 25.4)
    assert interop._drawing_scale(dxf, "mm") == ("mm", 1)
    with pytest.raises(ValueError, match="source units explicitly"):
        interop._drawing_scale("0\nEOF\n", "file")


def test_inch_stl_import_native_reopen_and_obj_roundtrip(store):
    box = trimesh.creation.box(extents=[1, 2, 3])
    result = upload(store, box.export(file_type="stl"), "inch-box.stl", "in")
    apply_result(store, result)
    original = next(a for a in store.load().domains["interop"]["assets"] if a["source_units"] == "in")
    assert hashlib.sha256((store.root / original["file"]).read_bytes()).hexdigest() == original["sha256"]
    first = measure(store)
    body = first["bodies"][0]
    assert np.subtract(body["bbox"]["max"], body["bbox"]["min"]) == pytest.approx([25.4, 50.8, 76.2])
    assert store.load().native_document is not None
    out = interop.execute(store, store.load(), "export", {"format": "obj"})
    exported = store.artifact(out["artifact"]).read_bytes()
    apply_result(store, upload(store, exported, "roundtrip.obj"))
    reopened = Store(store.root)
    measured = measure(reopened)
    assert measured["body_count"] == 2
    assert measured["bodies"][1]["bbox"] == body["bbox"]
    # Revisioned import is undoable, including provenance and native geometry.
    reopened.apply(Command(expected_revision=reopened.load().revision, actor="human", action="undo"))
    assert measure(reopened)["body_count"] == 1


def test_dxf_inches_to_editable_sketch_extrude_export_and_reopen(store):
    from o3d.native_document import prepare_native_command
    from o3d.native import NativeSession
    with NativeSession() as session:
        session.execute("sketch.create", {"name": "Source", "plane": "XY"})
        session.execute("sketch.rectangle.two_point", {"p0": [0, 0], "p1": [1, 2]})
        session.execute("sketch.finish")
        dxf = session.execute("sketch.export_dxf", {"sketch": "Source"})["dxf"]
    dxf = dxf.replace("$INSUNITS\n70\n4", "$INSUNITS\n70\n1")
    imported = upload(store, dxf.encode(), "Outline.dxf", "file")
    apply_result(store, imported)
    sketch_id = imported["result"]["native"]["sketch"]
    proposal, _ = prepare_native_command(store.load(), "solid.extrude", {
        "sketch": sketch_id, "distance": "5 mm", "body_name": "DXF part"}, store=store)
    apply_result(store, {"changed": True, "project": proposal.model_dump(mode="json")})
    assert measure(store)["total"]["volume_mm3"] == pytest.approx(25.4 * 50.8 * 5)
    result = interop.execute(store, store.load(), "export", {"format": "dxf", "sketch": sketch_id})
    text = store.artifact(result["artifact"]).read_text()
    assert text.count("$INSUNITS") == 1
    assert "$INSUNITS\n70\n4" in text
    assert "50.800000000" in text


def test_surface_trim_thicken_and_construction_persist(store):
    from o3d.native_document import prepare_native_command
    project = store.load()
    commands = [
        ("construct.plane.offset", {"base": "XY", "offset": "10 mm", "name": "Raised"}),
        ("construct.plane.midplane", {"a": {"plane": "XY"}, "b": {"plane": "Raised"}, "name": "Middle"}),
        ("construct.axis.two_points", {"a": {"point": [0, 0, 0]}, "b": {"point": [0, 0, 10]}, "name": "Axis"}),
        ("construct.point.three_planes", {"a": {"plane": "XY"}, "b": {"plane": "YZ"}, "c": {"plane": "XZ"}, "name": "Origin"}),
        ("sketch.create", {"name": "Outline", "plane": "Middle"}),
        ("sketch.rectangle.two_point", {"p0": [0, 0], "p1": [10, 20]}),
        ("sketch.finish", {}),
        ("surface.patch", {"sketch": "Outline", "body_name": "Sheet"}),
        ("surface.trim", {"body": "Sheet", "plane": {"origin": [3, 0, 0], "normal": [1, 0, 0]}, "keep": [8, 5, 5]}),
        ("surface.thicken", {"bodies": ["Sheet"], "thickness": "2 mm", "body_name": "Slab"}),
    ]
    for command, params in commands:
        project, _ = prepare_native_command(project, command, params, store=store)
    apply_result(store, {"changed": True, "project": project.model_dump(mode="json")})
    measured = measure(Store(store.root))
    assert next(b for b in measured["bodies"] if b["name"] == "Slab")["volume_mm3"] == pytest.approx(7 * 20 * 2)
    assert any(body["bbox"]["min"][2] == pytest.approx(5) for body in measured["bodies"])
    # Upstream plane edits recompute dependent native sketch and surface features.
    project, _ = prepare_native_command(store.load(), "timeline.edit", {
        "feature": "Raised", "set": {"plane.distance": "20 mm"}}, store=store)
    apply_result(store, {"changed": True, "project": project.model_dump(mode="json")})
    moved = measure(store)
    assert next(b for b in moved["bodies"] if b["name"] == "Slab")["volume_mm3"] == pytest.approx(280)
    assert any(body["bbox"]["min"][2] == pytest.approx(10) for body in moved["bodies"])
    exported = interop.execute(store, store.load(), "export", {"format": "step"})
    assert store.artifact(exported["artifact"]).read_bytes().startswith(b"ISO-10303-21;")


def test_step_import_healing_and_native_followup_edit(store):
    import build123d as bd
    source = store.root / "fixture.step"
    bd.export_step(bd.Box(10, 20, 30), source)
    apply_result(store, upload(store, source.read_bytes(), "box.step", "file"))
    asset = store.load().domains["interop"]["assets"][0]
    checked = interop.execute(store, store.load(), "step.analyze", {"asset_id": asset["id"]})
    assert checked["diagnostics"]["valid"]
    assert checked["diagnostics"]["volume_mm3"] == pytest.approx(6000)
    # The imported source is an editable native base body, not a display reference.
    edited = interop._native(store.load(), store, "solid.shell", {
        "faces": [[0, 0, 15]], "thickness": "1 mm"})
    apply_result(store, {"changed": True, "project": edited[0].model_dump(mode="json")})
    assert measure(store)["total"]["volume_mm3"] == pytest.approx(1824)
    assert interop.execute(store, store.load(), "step.analyze", {"asset_id": asset["id"]})["diagnostics"]["volume_mm3"] == pytest.approx(6000)
    healed = interop.execute(store, store.load(), "step.heal", {"asset_id": asset["id"]})
    assert healed["result"]["after"]["valid"]
    assert healed["result"]["volume_change_mm3"] == pytest.approx(0)
    apply_result(store, healed)
    assert measure(store)["body_count"] == 2


def test_mesh_worker_edits_and_section_keep_source_immutable(store):
    box = trimesh.creation.box(extents=[10, 20, 30])
    content = box.export(file_type="stl")
    apply_result(store, upload(store, content, "box.stl"))
    source = store.load().domains["interop"]["assets"][0]
    subdivided = interop.execute(store, store.load(), "mesh.subdivide", {"asset_id": source["id"]})
    assert subdivided["result"]["after"]["triangles"] == 48
    assert subdivided["result"]["after"]["volume_mm3"] == pytest.approx(6000)
    apply_result(store, subdivided)
    transformed = interop.execute(store, store.load(), "mesh.transform", {
        "asset_id": source["id"], "scale": 2, "translate": [2, 0, 0]})
    assert transformed["result"]["after"]["extents_mm"] == pytest.approx([20, 40, 60])
    assert transformed["result"]["after"]["volume_mm3"] == pytest.approx(48000)
    section = interop.execute(store, store.load(), "mesh.section", {"asset_id": source["id"]})
    assert section["segments"] == 8
    assert "$INSUNITS" in store.artifact(section["artifact"]).read_text()
    assert (store.root / source["file"]).read_bytes() == content


def test_surface_stitch_closes_shell_with_measured_volume(store):
    from o3d.native_document import prepare_native_command
    project = store.load()
    commands = [
        ("sketch.create", {"name": "R", "plane": "XY"}),
        ("sketch.rectangle.two_point", {"p0": [0, 0], "p1": [10, 20]}),
        ("sketch.finish", {}),
        ("surface.extrude", {"sketch": "R", "distance": 3, "body_name": "Walls"}),
        ("surface.patch", {"body": "Walls", "edges": [[5, 0, 0], [10, 10, 0], [5, 20, 0], [0, 10, 0]], "body_name": "Bottom"}),
        ("surface.patch", {"body": "Walls", "edges": [[5, 0, 3], [10, 10, 3], [5, 20, 3], [0, 10, 3]], "body_name": "Top"}),
        ("surface.stitch", {"bodies": ["Walls", "Bottom", "Top"]}),
    ]
    for command, params in commands:
        project, _ = prepare_native_command(project, command, params, store=store)
    apply_result(store, {"changed": True, "project": project.model_dump(mode="json")})
    assert measure(store)["body_count"] == 1
    assert measure(store)["total"]["volume_mm3"] == pytest.approx(600)


def test_mesh_convert_closed_mechanical_shape_and_reject_open_source(store):
    box = trimesh.creation.box(extents=[10, 20, 30])
    reply = upload(store, box.export(file_type="stl"), "box.stl")
    apply_result(store, reply)
    body = reply["result"]["native"]["bodies"][0]
    converted = interop.execute(store, store.load(), "mesh.convert", {"body": body, "tolerance": "0.05 mm"})
    apply_result(store, converted)
    result = measure(Store(store.root))
    assert result["bodies"][0]["faces"] == 6
    assert result["total"]["volume_mm3"] == pytest.approx(6000)
    exported = interop.execute(store, store.load(), "export", {"format": "step"})
    assert store.artifact(exported["artifact"]).stat().st_size > 1000
    box.update_faces(np.arange(11))
    opened = upload(store, box.export(file_type="stl"), "open.stl")
    apply_result(store, opened)
    revision = store.load().revision
    with pytest.raises(ValueError):
        interop.execute(store, store.load(), "mesh.convert", {"body": opened["result"]["native"]["bodies"][0]})
    assert store.load().revision == revision


def test_mesh_cluster_smooth_and_open_cut_report_measured_changes(store):
    sphere = trimesh.creation.icosphere(subdivisions=2, radius=5)
    apply_result(store, upload(store, sphere.export(file_type="stl"), "sphere.stl"))
    asset = store.load().domains["interop"]["assets"][0]
    reduced = interop.execute(store, store.load(), "mesh.cluster", {
        "asset_id": asset["id"], "cell_size_mm": 2, "insert": False})["result"]
    assert 0 < reduced["after"]["triangles"] < reduced["before"]["triangles"]
    assert 0 < reduced["maximum_vertex_displacement_mm"] < 2 * 3 ** 0.5
    assert reduced["limits"]
    smoothed = interop.execute(store, store.load(), "mesh.smooth", {
        "asset_id": asset["id"], "iterations": 2, "strength": 0.2, "insert": False})["result"]
    assert smoothed["after"]["triangles"] == smoothed["before"]["triangles"]
    assert smoothed["after"]["volume_mm3"] < smoothed["before"]["volume_mm3"]
    assert smoothed["maximum_vertex_displacement_mm"] > 0
    cut = interop.execute(store, store.load(), "mesh.cut", {
        "asset_id": asset["id"], "origin": [0, 0, 0], "normal": [0, 0, 1], "insert": False})["result"]
    assert cut["after"]["boundary_edges"] > 0
    assert cut["after"]["volume_mm3"] is None
    assert cut["after"]["bounds_mm"][0][2] == pytest.approx(0)


def test_svg_declared_physical_dimensions_are_not_scaled_twice(store):
    from o3d.native_document import prepare_native_command
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1in" height="2in" viewBox="0 0 1 2"><rect x="0" y="0" width="1" height="2"/></svg>'
    result = upload(store, svg, "Outline.svg", "file")
    apply_result(store, result)
    proposal, _ = prepare_native_command(store.load(), "solid.extrude", {
        "sketch": result["result"]["native"]["sketch"], "distance": "5 mm"}, store=store)
    apply_result(store, {"changed": True, "project": proposal.model_dump(mode="json")})
    assert measure(store)["total"]["volume_mm3"] == pytest.approx(25.4 * 50.8 * 5)
