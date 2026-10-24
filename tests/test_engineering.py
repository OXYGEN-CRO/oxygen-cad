import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import build123d as bd
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from o3d.api import create_app
from o3d.artifacts import export_bundle, render
from o3d.geometry import build, check, compile_shape, face_map, resolve_face
from o3d.model import Command, Hole, Parameter, Profile, Extrude, Project, geometry_key, study_key
from o3d.solver import Cancelled, load_boundary, run_process, simulate, preflight
from o3d.store import Conflict, Store


def test_concurrency_and_failed_edits_do_not_change_history(store):
    p = store.load()
    p.parameters["thickness"] = Parameter(value=4, locked=True)
    p = store.apply(Command(expected_revision=1, actor="human", project=p))
    rejected = p.model_copy(deep=True)
    rejected.parameters["thickness"].value = 5
    before = store.history()
    with pytest.raises(Conflict, match="locked"):
        store.apply(Command(expected_revision=2, actor="agent", project=rejected))
    assert store.history() == before
    def apply(name):
        candidate = p.model_copy(deep=True)
        candidate.name = name
        try:
            return store.apply(Command(expected_revision=2, project=candidate)).revision
        except Conflict:
            return "conflict"
    with ThreadPoolExecutor(2) as pool:
        values = list(pool.map(apply, ["a", "b"]))
    assert sorted(str(v) for v in values) == ["3", "conflict"]
    assert store.load().revision == 3


def test_undo_redo_are_new_revisions(store):
    for name in ["First", "Second"]:
        p = store.load(); p.name = name
        store.apply(Command(expected_revision=p.revision, actor="human", project=p))
    p = store.apply(Command(expected_revision=3, actor="human", action="undo"))
    assert p.name == "First" and p.revision == 4
    p = store.apply(Command(expected_revision=4, actor="human", action="undo"))
    assert p.name == "Analytical cantilever reference"
    p = store.apply(Command(expected_revision=5, actor="human", action="redo"))
    assert p.name == "First"
    p = store.apply(Command(expected_revision=6, actor="human", action="redo"))
    assert p.name == "Second"
    assert len(store.history()) == 7


def test_units_invalid_documents_and_executable_input(beam):
    for mutate in [lambda d:d.update(units="inch"), lambda d:d["features"][0].update(width="__import__('os')"), lambda d:d["features"][0].update(width=float("nan")), lambda d:d["features"].append(d["features"][0])]:
        value = beam.model_dump(); mutate(value)
        with pytest.raises(ValidationError):
            Project.model_validate(value)


def test_planes_volume_holes_and_invalid_treatments(beam):
    for plane, expected in [("XY", [20, 10, 3]), ("XZ", [20, 3, 10]), ("YZ", [3, 20, 10])]:
        p = Project(features=[Profile(id="profile", plane=plane, width=20, height=10, origin=(5, 6, 7)), Extrude(id="part", profile="profile", distance=3)])
        shape = compile_shape(p)
        assert shape.volume == pytest.approx(600)
        assert list(shape.bounding_box().size) == pytest.approx(expected)
    p = Project(features=[Profile(id="p", width=20, height=10), Extrude(id="e", profile="p", distance=4), Hole(id="h", diameter=2, center=(0, 0, 0))])
    assert compile_shape(p).volume == pytest.approx(800 - math.pi * 4)
    value = p.model_dump()
    value["features"].append({"id": "bad_fillet", "type": "fillet", "size": 1000, "selection": "part:all_edges"})
    with pytest.raises(ValueError, match="bad_fillet"):
        compile_shape(Project.model_validate(value))


def test_semantic_and_strict_references(beam):
    shape = compile_shape(beam)
    old = face_map(shape)
    strict = next(k for k, v in old.items() if k.startswith("face:") and v.is_same(resolve_face(shape, "part:xmax")))
    beam.features[1].distance = 90
    changed = compile_shape(beam)
    assert resolve_face(changed, "part:xmax").center().X == pytest.approx(90)
    with pytest.raises(ValueError, match="Missing or ambiguous"):
        resolve_face(changed, strict)


def test_export_audit_revision_and_result_invalidation(store):
    p = store.load(); first = build(store, p)
    checks = check(store, p)
    assert checks["state"] == "passed" and checks["mesh_volume_relative_error"] < .01
    imported = bd.import_step(store.artifact(first["artifacts"]["step"]))
    assert imported.volume == pytest.approx(first["volume_mm3"], rel=1e-6)
    result = export_bundle(store, p)
    assert store.artifact(result["artifact"]).stat().st_size > 1000
    assert store.artifact(render(store, p)["artifact"]).exists()
    old_geometry, old_study = geometry_key(p), study_key(p)
    p.study.forces[0].vector_n = (0, 0, -20)
    assert geometry_key(p) == old_geometry and study_key(p) != old_study
    p.features[1].distance = 90
    store.apply(Command(expected_revision=1, actor="human", project=p))
    assert store.state()["geometry_state"] == "stale"
    with pytest.raises(ValueError, match="older revision"):
        export_bundle(store, store.load())


def test_reference_import_interference_and_path_guards(store, tmp_path):
    shape = bd.Box(10, 10, 10)
    source = tmp_path / "reference.step"; bd.export_step(shape, source)
    p = store.import_step(source.read_bytes(), "motor.step", 1)
    assert p.references[0].file.startswith("assets/")
    build(store, p)
    assert any(f["code"] == "interference" for f in check(store, p)["findings"])
    with pytest.raises(ValueError):
        store.artifact("../../o3d.json")
    with pytest.raises(ValueError):
        store.import_step(b"not a step", "bad.step", 2)
    path = store.root / p.references[0].file
    path.write_text("tampered")
    with pytest.raises(ValueError, match="hash changed"):
        build(store, p)


def test_quadratic_surface_load_preserves_force_and_moment(beam):
    mesh = {"nodes": {1:[0,0,0], 2:[2,0,0], 3:[0,2,0], 4:[1,0,0], 5:[1,1,0], 6:[0,1,0], 7:[-1,0,0],8:[-1,1,0],9:[-1,0,1]},
            "surfaces":{"load_0":[[1,2,3,4,5,6]],"fixed_0":[[7,8,9,7,8,9]]}}
    fixed, loads = load_boundary(mesh, beam.study)
    total = sum(loads.values(), np.zeros(3))
    moment = sum((np.cross(mesh["nodes"][n], f) for n, f in loads.items()), np.zeros(3))
    assert total == pytest.approx([0, 0, -10])
    assert moment == pytest.approx(np.cross([2/3,2/3,0], total))
    assert set(loads) == {4,5,6}


def test_missing_physics_and_cancellation(store):
    p = store.load(); p.material = None; p.study.assumptions_confirmed = False
    with pytest.raises(ValueError, match="material"):
        preflight(p)
    cancel = threading.Event()
    cancel.set()
    import sys
    with pytest.raises(Cancelled):
        run_process([sys.executable, "-c", "import time;time.sleep(20)"], store.root, store.root / "cancel.log", cancel)


def test_static_cantilever_against_analytical_solution(store):
    p = store.load()
    result = simulate(store, p)
    force, length, width, thickness, youngs = 10, 80, 10, 4, 200000
    inertia = width * thickness ** 3 / 12
    expected = force * length ** 3 / (3 * youngs * inertia)
    assert result["max_displacement_mm"] == pytest.approx(expected, rel=.05)
    assert result["force_balance_error"] < .01
    assert result["moment_balance_error"] < .01
    assert result["converged"] and result["state"] == "passed"
    assert len(result["iterations"]) >= 2
    p.study.forces[0].vector_n = (0, 0, -20)
    store.apply(Command(expected_revision=1, actor="human", project=p))
    assert store.state()["analysis_state"] == "stale"


def test_http_concurrency_origins_jobs_and_context(store):
    with TestClient(create_app(store.root)) as client:
        p = client.get("/api/project").json()
        p["name"] = "HTTP edit"
        command = {"expected_revision":1,"actor":"human","project":p}
        assert client.post("/api/commands",json=command,headers={"Origin":"https://example.com"}).status_code == 403
        assert client.post("/api/commands",json=command).status_code == 200
        assert client.post("/api/commands",json=command).status_code == 409
        assert client.post("/api/context",json={"selection":{"id":"part:xmin"}}).status_code == 200
        assert client.get("/api/state").json()["context"]["selection"]["id"] == "part:xmin"
        response = client.post("/api/jobs/build").json()
        for _ in range(150):
            job = client.get(f"/api/jobs/{response['id']}").json()
            if job["state"] not in ("running","queued"):
                break
            time.sleep(.1)
        assert job["state"] == "succeeded", job
        assert client.get("/api/state").json()["geometry_state"] == "current"


def test_profile_and_edge_operation_coverage():
    from o3d.model import EdgeTreatment
    circle = Project(features=[Profile(id="p", shape="circle", radius=5), Extrude(id="e", profile="p", distance=3)])
    assert compile_shape(circle).volume == pytest.approx(math.pi * 25 * 3)
    polygon = Project(features=[Profile(id="p", shape="polygon", points=[(0,0),(10,0),(10,10),(0,10)]), Extrude(id="e", profile="p", distance=3)])
    assert compile_shape(polygon).volume == pytest.approx(300)
    base = [Profile(id="p", width=20, height=10), Extrude(id="e", profile="p", distance=4)]
    cut = Project(features=base+[Profile(id="cut", shape="circle", radius=1), Extrude(id="remove", profile="cut", distance=4, operation="cut")])
    assert compile_shape(cut).volume == pytest.approx(800 - 4*math.pi)
    blind = Project(features=base+[Hole(id="h",diameter=2,through=False,center=(0,0,4),depth=1)])
    assert compile_shape(blind).volume == pytest.approx(800-math.pi)
    for kind, expected in [("fillet",800-16*(1-math.pi/4)),("chamfer",792)]:
        p = Project(features=base+[EdgeTreatment(id="edge",type=kind,size=1)])
        assert compile_shape(p).volume == pytest.approx(expected)
        p.features[-1].suppressed=True
        assert compile_shape(p).volume == pytest.approx(800)


def test_agent_cannot_detach_locked_dimensions_or_weaken_requirements(store):
    p = store.load()
    p.parameters["depth"] = Parameter(value=80,locked=True)
    p.features[1].distance = "$depth"
    store.apply(Command(expected_revision=1,actor="human",project=p))
    p = store.load(); p.features[1].distance=90.0
    with pytest.raises(Conflict,match="binding"):
        store.apply(Command(expected_revision=2,actor="agent",project=p))
    p = store.load();p.requirements=[]
    with pytest.raises(Conflict,match="user-owned"):
        store.apply(Command(expected_revision=2,actor="agent",project=p))
    assert store.load().revision==2
