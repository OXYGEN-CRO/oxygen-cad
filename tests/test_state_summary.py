"""Native polling stays bounded without replacing the editable source or scene."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from o3d.api import create_app
from o3d.build_summary import load_build_summary, publish_build_summary
from o3d.geometry import build
from o3d.model import Command, Project, geometry_key
from o3d.native import NativeSession
from o3d.native_document import execute_native
from o3d.store import Store, write_json


def native_store(tmp_path):
    with NativeSession() as session:
        for name, x in [("Base", 0), ("Arm", 30)]:
            session.execute("component.create", {"name": name, "parent": 0, "grounded": name == "Base"})
            session.execute("solid.box", {"length": 20, "width": 10, "height": 5,
                                          "corner": [x, 0, 0], "body_name": name + "Body"})
        snapshot = session.execute("oxygen.document.get")
    project = Project(name="Native state acceptance", native_document=snapshot["document"],
                      domains={"modeling": {"schema_version": 1, "native_context": snapshot["context"]}})
    store = Store(tmp_path)
    store.init(project)
    return store


def test_native_api_metadata_preserves_source_scene_and_stale_state(tmp_path):
    store = native_store(tmp_path)
    original = store.load()
    full = build(store, original)
    with TestClient(create_app(store.root, project_windows=False)) as client:
        response = client.get("/api/state")
        assert response.status_code == 200
        state = response.json()
        assert state["project"]["native_document"] == original.native_document
        assert state["geometry_state"] == "current"
        assert state["geometry_key"] == full["key"]
        assert state["build"]["bounds"] == full["bounds"]
        assert state["build"]["volume_mm3"] == pytest.approx(2000)
        assert state["build"]["faces"] == []
        assert state["build"]["scene_summary"]["body_count"] == 2
        assert state["last_build"] is None
        assert "scene" not in state["build"]
        scene = client.get("/api/native/scene").json()["scene"]
        assert len(scene["bodies"]) == 2
        assert all(body["mesh"] for body in scene["bodies"])
        assert store.load() == original
        # Geometry-affecting source changes cannot keep the old cache current.
        execute_native(store, original.revision, "agent", "solid.box", {
            "length": 3, "width": 3, "height": 3, "corner": [70, 0, 0], "body_name": "NewBody"})
        changed = client.get("/api/state").json()
        assert changed["geometry_state"] == "stale"
        assert changed["build"] is None
        assert changed["last_build"]["key"] == full["key"]
        assert changed["geometry_key"] != full["key"]
    # The default Python/CLI consumer still gets the original complete build.
    saved = json.loads((store.cache / "builds" / full["key"] / "build.json").read_text())
    assert saved["scene"] == full["scene"]


def test_large_old_cache_migrates_once_and_drops_all_triangle_descriptors(tmp_path, monkeypatch):
    source = tmp_path / "build.json"
    # Synthetic scale fixture, never claimed as an actual geometry result.
    full = {"key": "fixture", "revision": 2, "dimensions_mm": [10, 20, 30],
            "bounds": [[0, 0, 0], [10, 20, 30]], "artifacts": {},
            "faces": [{"id": f"f{i}", "vertices": [[0, 0, 0]] * 3,
                       "triangles": [[0, 1, 2]]} for i in range(50000)],
            "scene": {"bodies": [{"id": "b", "geometry_kind": "mesh",
                                    "mesh": {"faces": [{"id": f"triangle:{i}"} for i in range(50000)]}}]}}
    write_json(source, full)
    result = load_build_summary(source, expected_key="fixture")
    assert source.stat().st_size > 10_000_000
    assert len(json.dumps(result)) < 1000
    assert result["faces"] == [] and "scene" not in result
    assert result["scene_summary"] == {"body_count": 1, "geometry_kinds": {"mesh": 1}}
    original_read = Path.read_text
    def guarded(path, *a, **kw):
        if path == source:
            raise AssertionError("A warmed state request reread the full geometry")
        return original_read(path, *a, **kw)
    monkeypatch.setattr(Path, "read_text", guarded)
    assert load_build_summary(source, expected_key="fixture") == result


def test_summary_is_invalidated_by_changed_full_cache_and_wrong_key_is_refused(tmp_path):
    source = tmp_path / "build.json"
    first = {"key": "same", "revision": 1, "dimensions_mm": [1, 2, 3]}
    write_json(source, first)
    publish_build_summary(source, first)
    second = first | {"revision": 2, "dimensions_mm": [4, 5, 6]}
    write_json(source, second)
    assert load_build_summary(source, expected_key="same")["dimensions_mm"] == [4, 5, 6]
    with pytest.raises(ValueError, match="geometry key"):
        load_build_summary(source, expected_key="different")
    # Damaged derived caches are rebuilt from the source, never used as a pass.
    source.with_name("build-state.json").write_text("{invalid")
    assert load_build_summary(source, expected_key="same")["revision"] == 2


def test_legacy_api_keeps_full_geometry_and_duplicate_compatibility(store):
    full = build(store, store.load())
    with TestClient(create_app(store.root, project_windows=False)) as client:
        state = client.get("/api/state").json()
    assert state["build"]["faces"] == full["faces"]
    assert any(face["vertices"] for face in state["build"]["faces"])
    assert state["last_build"]["key"] == full["key"]
    assert state["geometry_state"] == "current"


def test_native_legacy_study_overlay_uses_full_geometry(tmp_path):
    store = native_store(tmp_path)
    project = store.load()
    project.study.fixed_faces = ["part:xmin"]
    store.apply(Command(expected_revision=project.revision, actor="human", project=project))
    full = build(store, store.load())
    state = store.state(compact_geometry=True)
    assert state["build"]["faces"] == full["faces"]
    assert state["build"]["scene"] == full["scene"]
    assert state["last_build"] is not None


def test_native_reference_overlay_retains_actual_reference_faces(tmp_path):
    import build123d as bd
    source = tmp_path / "reference.step"
    bd.export_step(bd.Box(3, 4, 5), source)
    store = native_store(tmp_path / "project")
    store.import_step(source.read_bytes(), "Reference", store.load().revision)
    full = build(store, store.load())
    state = store.state(compact_geometry=True)
    assert state["build"]["references"] == full["references"]
    assert state["build"]["references"][0]["faces"][0]["vertices"]
    assert state["build"]["faces"] == full["faces"]


def test_compact_scene_transport_keeps_exact_triangles_picking_and_full_default():
    from o3d.native_routes import compact_native_scene
    mesh = {"positions": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            "triangles": [[0, 1, 2]], "tri_face": [7], "normals": [[0, 0, 1]] * 3,
            "faces": [{"id": "source:face:7", "vertices": [[0, 0, 0]], "triangles": [[0, 1, 2]]}]}
    full = {"bodies": [{"id": "mesh", "geometry_kind": "mesh", "mesh": mesh},
                       {"id": "solid", "geometry_kind": "solid", "mesh": mesh}]}
    compact = compact_native_scene(full)
    assert "faces" not in compact["bodies"][0]["mesh"]
    assert "faces" in compact["bodies"][1]["mesh"]
    for name in ("positions", "triangles", "tri_face"):
        assert compact["bodies"][0]["mesh"][name] is mesh[name]
    assert "faces" in full["bodies"][0]["mesh"]
    assert "normals" in full["bodies"][0]["mesh"]
    assert "normals" not in compact["bodies"][0]["mesh"]
    unmapped = {"bodies": [{"id": "unknown", "geometry_kind": "mesh",
                            "mesh": dict(mesh, tri_face=[])}]}
    assert "faces" in compact_native_scene(unmapped)["bodies"][0]["mesh"]
