"""Real Rust/STEP integration; no mocked geometry or silent compatibility fallback."""
import json
import zipfile

import build123d as bd
import pytest
from pydantic import ValidationError

from o3d.geometry import build, compile_shape
from o3d.model import Command, NativeFeature, Parameter, Profile, Extrude, Project, geometry_key
from o3d.native import NativeSession, UPSTREAM_COMMIT
from o3d.store import Store, Conflict


def enclosure():
    return Project(name="Native enclosure", parameters={"length": Parameter(value=30, locked=True)}, features=[
        NativeFeature(id="block", name="Block", command="solid.box", params={"length":"$length","width":20,"height":10}),
        NativeFeature(id="shell", name="Shell", command="solid.shell", params={"faces":[[15,10,10]],"thickness":2}),
    ])


def test_source_built_engine_native_shell_and_export(tmp_path):
    store=Store(tmp_path);store.init(enclosure())
    result=build(store,store.load())
    assert result["engine"]["engine"]=="solidcraft"
    assert result["engine"]["upstream_commit"]==UPSTREAM_COMMIT
    assert result["volume_mm3"]==pytest.approx(6000-26*16*8,rel=1e-7)
    assert result["engine"]["independent_volume_relative_error"]<1e-3
    shape=bd.import_step(store.artifact(result["artifacts"]["step"]))
    assert shape.is_valid and len(shape.solids())==1
    with zipfile.ZipFile(store.artifact(result["artifacts"]["native_design"])) as native:
        assert "document.json" in native.namelist()
    recipe=json.loads(store.artifact(result["artifacts"]["engine_script"]).read_text())
    assert any(c["command"]=="solid.shell" for c in recipe["commands"])


def test_rotated_profile_and_placement_match_explicit_occt_reference():
    project=Project(features=[Profile(id="p",width=20,height=10,plane="XZ",origin=(3,4,5),rotation=(10,20,30)),
        Extrude(id="e",profile="p",distance=4)],placement={"translation":[7,-5,2],"rotation":[25,15,-12]})
    rust=compile_shape(project)
    project.geometry_engine="occt"
    reference=compile_shape(project)
    assert rust.volume==pytest.approx(reference.volume,rel=1e-7)
    assert list(rust.bounding_box().min)==pytest.approx(list(reference.bounding_box().min),abs=1e-5)
    assert list(rust.bounding_box().max)==pytest.approx(list(reference.bounding_box().max),abs=1e-5)


def test_nested_native_bindings_remain_user_owned(tmp_path):
    store=Store(tmp_path);store.init(enclosure())
    project=store.load();project.features[0].params["length"]=50
    with pytest.raises(Conflict,match="binding"):
        store.apply(Command(expected_revision=1,actor="agent",project=project))
    assert store.load().features[0].params["length"]=="$length"


def test_saved_native_features_cannot_access_files_and_engine_keys_differ(monkeypatch):
    for command, params in [("file.export",{"path":"/tmp/part.step"}), ("sketch.insert_svg",{"path":"/tmp/private.svg"}),
                            ("solid.box",{"length":float("nan")}), ("solid.box",{"nested":{"font":"/tmp/private"}})]:
        with pytest.raises((ValidationError, ValueError)):
            NativeFeature(id="bad",command=command,params=params)
    project=enclosure();old=geometry_key(project);project.geometry_engine="occt"
    assert geometry_key(project)!=old
    with pytest.raises(ValueError,match="Native command features require"):
        compile_shape(project)
    project.geometry_engine="solidcraft"
    monkeypatch.setenv("O3D_ENGINE_BINARY","/definitely/missing/oxygen-engine")
    with pytest.raises(RuntimeError,match="Rust engine is not built"):
        compile_shape(project)


def test_native_session_errors_do_not_terminate_protocol():
    with NativeSession() as engine:
        with pytest.raises(ValueError,match="unknown command"):
            engine.execute("not.a.command")
        assert engine.execute("oxygen.version")["upstream_commit"]==UPSTREAM_COMMIT
        result=engine.execute("solid.box",{"length":30,"width":20,"height":10})
        assert engine.execute("inspect.measure")["total"]["volume_mm3"]==pytest.approx(6000)


def test_actual_mounting_bracket_matches_independent_engine():
    from pathlib import Path
    project=Project.model_validate_json(Path("examples/bracket.json").read_text())
    native=compile_shape(project)
    project.geometry_engine="occt"
    reference=compile_shape(project)
    assert native.volume==pytest.approx(reference.volume,rel=1e-7)
    assert list(native.bounding_box().min)==pytest.approx(list(reference.bounding_box().min),abs=1e-5)
    assert list(native.bounding_box().max)==pytest.approx(list(reference.bounding_box().max),abs=1e-5)
