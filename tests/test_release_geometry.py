"""Re-read actual mixed-geometry handoffs instead of inferring success from ZIP names."""
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile

import build123d as bd
import numpy as np
from PIL import Image
import pytest
import trimesh

from o3d.artifacts import export_bundle, render
from o3d.geometry import build
from o3d.model import Command, Project
from o3d.native import NativeSession
from o3d.native_document import query_native
from o3d.projects import restore_archive
from o3d.store import Store


def canonical_store(root, kind):
    store = Store(root)
    domains = {}
    source = None
    if kind in {"mesh", "mixed"}:
        mesh = trimesh.creation.box(extents=[4, 5, 6])
        mesh.apply_translation([102, 2.5, 3])
        content = mesh.export(file_type="stl")
        digest = hashlib.sha256(content).hexdigest()
        source = root / "assets" / f"{digest}.stl"
        source.parent.mkdir(parents=True)
        source.write_bytes(content)
        domains["interop"] = {"schema_version": 1, "assets": [
            {"id": "source_mesh", "sha256": digest, "file": f"assets/{digest}.stl"}]}
    with NativeSession() as session:
        if kind in {"two-solid", "mixed"}:
            session.execute("solid.box", {"length": 20, "width": 10, "height": 5, "body_name": "Block"})
        if kind == "two-solid":
            session.execute("solid.box", {"length": 8, "width": 6, "height": 4, "corner": [40, 0, 0], "body_name": "Second"})
        if kind in {"sheet", "mixed"}:
            session.execute("sketch.create", {"plane": {"origin": [0, 0, 20], "x_dir": [1, 0, 0], "y_dir": [0, 1, 0]}})
            session.execute("sketch.rectangle.two_point", {"p0": [0, 0], "p1": [20, 7]})
            session.execute("sketch.finish")
            session.execute("surface.patch", {"body_name": "Sheet"})
        if source:
            session.execute("file.insert_mesh", {"path": str(source)})
        snapshot = session.execute("oxygen.document.get")
    domains["modeling"] = {"schema_version": 1, "native_context": snapshot["context"]}
    store.init(Project(name=f"Release {kind}", native_document=snapshot["document"], domains=domains))
    return store


def open_release(store):
    project = store.load()
    built = build(store, project)
    result = export_bundle(store, project)
    content = store.artifact(result["artifact"]).read_bytes()
    archive = zipfile.ZipFile(io.BytesIO(content))
    manifest = json.loads(archive.read("release-manifest.json"))
    assert set(archive.namelist()) == set(manifest["files"]) | {"release-manifest.json"}
    for name, metadata in manifest["files"].items():
        content = archive.read(name)
        assert metadata == {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    report = json.loads(archive.read("report.json"))
    combined = trimesh.load(io.BytesIO(archive.read("part.stl")), file_type="stl", force="mesh", process=False)
    native_meshes = [trimesh.Trimesh(vertices=b["mesh"]["positions"], faces=b["mesh"]["triangles"], process=False)
                     for b in built["scene"]["bodies"]]
    assert len(combined.faces) == sum(len(mesh.faces) for mesh in native_meshes)
    assert combined.area == pytest.approx(sum(mesh.area for mesh in native_meshes), rel=1e-6)
    assert combined.bounds == pytest.approx(np.array(report["geometry"]["bounds"]))
    assert report["scope"]["aggregate_exports"]["stl"]["body_ids"] == [b["id"] for b in built["scene"]["bodies"]]
    return built, result, archive, report


def assert_rendered(store, built):
    result = render(store, store.load())
    with Image.open(store.artifact(result["artifact"])) as image:
        assert image.size == (1280, 900)
        pixels = np.array(image)
    # Exclude captions. Actual geometry must occupy a visible area of the frame.
    foreground = np.any(pixels[100:850, 100:1180] != [238, 241, 244], axis=2)
    assert foreground.sum() > 10000
    return pixels


def reread_step(archive, name, directory):
    target = directory / Path(name).name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(archive.read(name))
    return bd.import_step(target)


def test_mesh_only_release_preserves_real_triangles_sources_and_editable_document(tmp_path):
    store = canonical_store(tmp_path / "source", "mesh")
    built, result, archive, report = open_release(store)
    with archive:
        assert not {"part.step", "part.brep"}.intersection(archive.namelist())
        assert report["volume_mm3"] is None and report["mass_g"] is None
        assert report["geometry_checks"]["state"] == "inconclusive"
        body = report["geometry"]["bodies"][0]
        assert body["geometry_kind"] == "mesh" and "step" not in body["artifacts"]
        reread = trimesh.load(io.BytesIO(archive.read(body["artifacts"]["stl"])), file_type="stl", force="mesh")
        assert len(reread.faces) == 12
        assert reread.is_watertight and reread.is_winding_consistent
        assert reread.area == pytest.approx(148)
        assert reread.bounds.tolist() == [[100, 0, 0], [104, 5, 6]]
        raw = json.loads(archive.read(body["artifacts"]["mesh"]))
        assert raw == built["scene"]["bodies"][0]["mesh"]
        scene = json.loads(archive.read("scene.json"))
        assert scene["bodies"][0]["artifacts"] == body["artifacts"]
        for name in report["scope"]["source_assets"]:
            assert hashlib.sha256(archive.read(name)).hexdigest() == Path(name).stem
        assert len(report["scope"]["source_assets"]) == 1
        assert not any(row["source"].endswith(".step") for row in csv.DictReader(io.StringIO(archive.read("bom.csv").decode())))
    pixels = assert_rendered(store, built)
    # The top face occludes the rear walls; painting near triangles first would
    # make this closed box look like an open container.
    assert pixels[270, 640].tolist() == [86, 133, 162]
    restored = Store(restore_archive(store.artifact(result["artifact"]), tmp_path / "restored")["path"])
    assert restored.load() == store.load()
    assert query_native(restored.load(), "inspect.measure", store=restored)["bodies"][0]["geometry_kind"] == "mesh"
    rebuilt = build(restored, restored.load())
    assert rebuilt["scene"]["bodies"][0]["mesh"] == built["scene"]["bodies"][0]["mesh"]


def test_open_sheet_exports_exact_area_without_inventing_volume_or_mass(tmp_path):
    store = canonical_store(tmp_path / "source", "sheet")
    built, _result, archive, report = open_release(store)
    with archive:
        assert report["volume_mm3"] is None and report["mass_g"] is None
        assert report["geometry"]["solid_volume_mm3"] is None
        assert report["geometry_checks"]["state"] == "inconclusive"
        body = report["geometry"]["bodies"][0]
        assert body["geometry_kind"] == "surface" and body["volume_mm3"] is None
        sheet = reread_step(archive, body["artifacts"]["step"], tmp_path / "reread")
        assert not sheet.solids()
        assert sheet.area == pytest.approx(140)
        assert [sheet.bounding_box().size.X, sheet.bounding_box().size.Y, sheet.bounding_box().size.Z] == pytest.approx([20, 7, 0])
        assert report["scope"]["solid_engineering_checks"] == "not_applicable_to_complete_scene"
    assert_rendered(store, built)


def test_two_solid_release_preserves_separate_exact_bodies_and_solid_checks(tmp_path):
    store = canonical_store(tmp_path / "source", "two-solid")
    built, _result, archive, report = open_release(store)
    with archive:
        exact = reread_step(archive, "part.step", tmp_path / "aggregate")
        assert len(exact.solids()) == 2
        assert exact.volume == pytest.approx(1192)
        assert report["volume_mm3"] == pytest.approx(exact.volume)
        assert report["geometry_checks"]["state"] == "passed"
        volumes = [reread_step(archive, body["artifacts"]["step"], tmp_path / f"body-{i}").volume
                   for i, body in enumerate(report["geometry"]["bodies"])]
        assert volumes == pytest.approx([1000, 192])
        assert report["geometry"]["solid_volume_mm3"] == pytest.approx(sum(volumes))
    assert_rendered(store, built)


def test_mixed_release_covers_every_body_and_complete_scene_bounds(tmp_path):
    store = canonical_store(tmp_path / "source", "mixed")
    built, _result, archive, report = open_release(store)
    with archive:
        assert report["geometry"]["geometry_kinds"] == {"solid": 1, "surface": 1, "mesh": 1}
        assert not {"part.step", "part.brep"}.intersection(archive.namelist())
        assert report["dimensions_mm"] == pytest.approx([104, 10, 20])
        assert report["volume_mm3"] is None and report["mass_g"] is None
        assert report["geometry"]["solid_volume_mm3"] == pytest.approx(1000)
        assert report["geometry_checks"]["state"] == "inconclusive"
        assert not report["scope"]["solid_engineering_checks"] == "performed"
        for index, body in enumerate(report["geometry"]["bodies"]):
            raw = json.loads(archive.read(body["artifacts"]["mesh"]))
            assert raw == built["scene"]["bodies"][index]["mesh"]
            if body["geometry_kind"] == "mesh":
                assert "step" not in body["artifacts"]
            else:
                shape = reread_step(archive, body["artifacts"]["step"], tmp_path / f"body-{index}")
                assert shape.area == pytest.approx(700 if body["geometry_kind"] == "solid" else 140)
    assert_rendered(store, built)


def test_release_is_byte_reproducible_and_stale_publication_retains_previous_zip(tmp_path, monkeypatch):
    store = canonical_store(tmp_path / "source", "mesh")
    project = store.load()
    build(store, project)
    result = export_bundle(store, project)
    published = store.artifact(result["artifact"]).read_bytes()
    assert store.artifact(export_bundle(store, project)["artifact"]).read_bytes() == published
    from o3d import projects
    original = projects.portable_archive
    def advance_after_snapshot(*args, **kwargs):
        archived = original(*args, **kwargs)
        current = store.load()
        store.apply(Command(expected_revision=current.revision, actor="human", project=current.model_copy(update={"name": "Concurrent edit"})))
        return archived
    monkeypatch.setattr(projects, "portable_archive", advance_after_snapshot)
    with pytest.raises(ValueError, match="changed during export"):
        export_bundle(store, project)
    assert store.artifact(result["artifact"]).read_bytes() == published
    assert not list((store.cache / "exports").glob(".release-*"))


@pytest.mark.parametrize("kind", ["mesh", "sheet", "mixed", "two-solid"])
def test_stale_source_never_publishes_geometry_release(tmp_path, kind):
    store = canonical_store(tmp_path / "source", kind)
    project = store.load()
    build(store, project)
    store.apply(Command(expected_revision=project.revision, actor="human", project=project.model_copy(update={"name": "Advanced"})))
    with pytest.raises(ValueError, match="changed during export"):
        export_bundle(store, project)
    assert not (store.cache / "exports" / str(project.revision) / "release.zip").exists()
