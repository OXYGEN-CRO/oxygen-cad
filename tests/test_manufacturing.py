"""Measured domain acceptance fixtures; no physical machining/print claim."""
from __future__ import annotations

import copy
import io
import json
import math
import os
from pathlib import Path

import pytest

from o3d.domains import manufacturing as m
from o3d.domains.manufacturing_ops.schema import Manufacturing

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def data():
    return json.loads((ROOT/"examples/manufacturing.json").read_text())


@pytest.fixture
def process_project(beam, data):
    return beam.model_copy(update={"domains": {"manufacturing": m.validate(data)}})


def test_schema_process_graph_rejects_invalid_inputs(data):
    assert m.validate(data)["schema_version"] == 1
    for mutate in [lambda d: d["machines"][0].update(post="linuxcnc_lathe_v1"),
                   lambda d: d["operations"][0].update(depends_on=["holes"]),
                   lambda d: d["setups"][0].update(machine_id="missing"),
                   lambda d: d["tools"][0].update(gauge_length=1),
                   lambda d: d["operations"][0].update(strategy="five_axis"),
                   lambda d: d["machines"][0].update(max_feed_mm_min=float("nan"))]:
        bad = copy.deepcopy(data)
        mutate(bad)
        with pytest.raises(ValueError):
            m.validate(bad)


def test_pocket_island_drilling_and_independent_nc_backplot(store, process_project):
    from o3d.domains.manufacturing_ops.verification import backplot
    out = m.execute(store, process_project, "post", {"setup_id": "top"})
    assert out["state"] == "review_required"
    assert out["posted_motion_check"]["matches_generated_path"]
    assert all(r["violation_count"] == 0 for r in out["verification"])
    nc = store.artifact(out["artifacts"]["program.ngc"]).read_text()
    moves = backplot(nc)
    assert min(p["z"] for p in moves) == -8
    assert "T1 M6" in nc and "T2 M6" in nc
    # Independent sampled distance-to-segment check (no GEOS) across pocket/island.
    path = out["paths"][0]
    feed_segments = [(a, b) for a, b in zip(path["moves"], path["moves"][1:]) if b["motion"] == "feed" and a["z"] == b["z"] == -3]
    def distance(x, y, a, b):
        dx, dy = b["x"]-a["x"], b["y"]-a["y"]
        t = max(0, min(1, ((x-a["x"])*dx+(y-a["y"])*dy)/(dx*dx+dy*dy))) if dx or dy else 0
        return math.hypot(x-a["x"]-t*dx, y-a["y"]-t*dy)
    removed = lambda x, y: min(distance(x, y, a, b) for a, b in feed_segments) < 2.99
    assert removed(20, 20) and removed(45, 35)
    assert not removed(32, 26)  # retained island
    assert not removed(8, 20)   # retained outside stock/part boundary
    pocket = out["verification"][0]
    assert pocket["gouge_area_mm2"] < 0.0025
    assert pocket["unreachable_corner_area_mm2"] > 0  # honest inaccessible sharp corners
    assert out["verification"][1]["hole_count"] == 2


def test_reach_fixtures_machine_limits_and_staleness(store, process_project, data):
    key = m.input_key(process_project)
    for mutate, expected in [
        (lambda d: d["tools"][0].update(flute_length=1), "insufficient_flute_reach"),
        (lambda d: d["machines"][0].update(travel_max=[20,300,200]), "axis_travel"),
        (lambda d: d["setups"][0].update(fixtures=[{"id":"clamp","name":"Seeded collision","minimum":[9,9,-5],"maximum":[20,20,2]}]), "fixture_collision")]:
        bad = copy.deepcopy(data)
        mutate(bad)
        project = process_project.model_copy(update={"domains": {"manufacturing": m.validate(bad)}})
        assert m.input_key(project) != key
        out = m.execute(store, project, "post", {"setup_id": "top"})
        assert out["state"] == "rejected"
        assert "program.ngc" not in out["artifacts"]
        assert any(v["kind"] == expected for r in out["verification"] for v in r["violations"])
    # Every process input invalidates output identity, independently of a structural load case.
    for name, mutate in [
        ("stock", lambda d: d["setups"][0]["stock"].update(material="Different")),
        ("wcs", lambda d: d["setups"][0].update(wcs_origin=[1,0,0])),
        ("holder", lambda d: d["tools"][0].update(holder_diameter=20))]:
        changed = copy.deepcopy(data)
        mutate(changed)
        assert m.input_key(process_project, changed) != key, name


def test_turning_radius_mode_post_and_reference_profile(store, process_project):
    out = m.execute(store, process_project, "post", {"setup_id": "od"})
    assert out["state"] == "review_required"
    code = store.artifact(out["artifacts"]["program.ngc"]).read_text()
    assert "G18 G8" in code and "Y" not in code
    assert "X12.00000 Z-25.00000" in code and "X17.00000 Z-65.00000" in code
    assert out["verification"][0]["stock_removal_verified"] is False
    assert out["posted_motion_check"]["matches_generated_path"]


def test_golden_linuxcnc_drill_program(data):
    from o3d.domains.manufacturing_ops.cam import generate
    from o3d.domains.manufacturing_ops.verification import post, verify_post, backplot
    domain = Manufacturing.model_validate(data)
    setup, machine, tool = domain.setups[0], domain.machines[0], domain.tools[1]
    op = domain.operations[1].model_copy(update={"holes": [(65,15)], "bottom_z": -4})
    path = generate(op, setup, tool, machine)
    code = post([path], setup, {tool.id: tool}, machine)
    assert code == (ROOT/"tests/fixtures/manufacturing/drill.ngc").read_text()
    assert verify_post(code, [path], setup, machine)["matches_generated_path"]
    with pytest.raises(ValueError, match="Unsupported NC G-code"):
        backplot(code.replace("G21", "G20"))


def test_nest_multisheet_kerf_dxf_and_cutting(store, process_project, data):
    import ezdxf
    from shapely.geometry import Polygon, box
    data["nesting"].update(sheet_width_mm=110, sheet_height_mm=70)
    project = process_project.model_copy(update={"domains": {"manufacturing": m.validate(data)}})
    out = m.execute(store, project, "cut", {"setup_id":"sheet", "tool_id":"beam", "power_s":200})
    nested = out["nest"]
    assert nested["sheet_count"] == 4
    assert nested["verified_nonoverlap"]
    assert nested["utilization"] == pytest.approx(3100/7700)
    for placement in nested["placements"]:
        actual = Polygon(placement["boundary"], placement["holes"])
        assert box(5,5,105,65).covers(actual.buffer(0.1))
    doc = ezdxf.readfile(store.artifact(out["artifacts"]["sheet-1.dxf"]))
    assert doc.units == 4 and not doc.audit().has_errors
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="CUT_OUTER"]')) == 1
    assert all(p.closed for p in doc.modelspace().query("LWPOLYLINE"))
    code = store.artifact(out["artifacts"]["sheet-1.nc"]).read_text()
    assert code.index("(inner contour)") < code.index("(outer contour)")
    assert "M4 S200.000" in code and code.endswith("M5\nM2\n")
    assert out["cutting"][0]["kerf_compensation_mm"] == 0.1
    bad = copy.deepcopy(data)
    bad["nesting"]["parts"][0]["material"] = "Aluminium"
    project = project.model_copy(update={"domains": {"manufacturing": m.validate(bad)}})
    with pytest.raises(ValueError, match="material/thickness"):
        m.execute(store, project, "nest", {})


def test_inspection_alignment_and_controlled_probe_feedback(store, process_project):
    payload = {"csv":"id,nominal,measured,lower_tolerance,upper_tolerance,unit\nwidth,10,10.02,-0.05,0.05,mm\nbore,4,4.1,-0.02,0.02,mm\n"}
    out = m.execute(store, process_project, "inspect", payload)
    assert out["inspection"]["pass_count"] == out["inspection"]["fail_count"] == 1
    points = [{"id":str(i), "nominal":p, "measured":[p[0]+0.2,p[1]-0.3,p[2]+0.1]} for i,p in enumerate([[0,0,0],[10,0,0],[0,10,0],[5,5,2]])]
    payload = {"setup_id":"top", "measurements":points, "source_input_key":m.input_key(process_project, m.validate(process_project.domains["manufacturing"]))}
    out = m.execute(store, process_project, "apply_probe", payload)
    assert out["changed"]
    assert out["data"]["setups"][0]["wcs_origin"] == pytest.approx([0.2,-0.3,0.1])
    assert out["alignment"]["rigid_max_residual_mm"] < 1e-10
    with pytest.raises(ValueError, match="stale"):
        m.execute(store, process_project, "apply_probe", payload|{"source_input_key":"old"})
    points[0]["measured"][0] += 1
    with pytest.raises(ValueError, match="exceed"):
        m.execute(store, process_project, "apply_probe", payload)


def test_probe_plan_is_real_cycle_and_no_live_connector(store, process_project):
    out = m.execute(store, process_project, "probe_plan", {"setup_id":"top", "tool_id":"probe", "points":[{"x":20,"y":20,"start_z":2,"seek_z":-2,"feed_mm_min":50}]})
    code = store.artifact(out["artifacts"]["probe-plan.ngc"]).read_text()
    assert "G38.2 Z-2.00000 F50.000" in code
    assert out["probe"]["live_connection"] is False


def test_plastics_localized_thickness_draft_and_pull_reanalysis(tmp_path, data):
    import trimesh
    from o3d.domains.manufacturing_ops.plastics import analyze
    from o3d.domains.manufacturing_ops.schema import PlasticsRules
    mesh = trimesh.creation.box(extents=[20,10,0.6])
    path = tmp_path/"thin.stl"
    mesh.export(path)
    rules = PlasticsRules.model_validate(data["plastics"])
    out = analyze(path, rules)
    thin = [f for f in out["findings"] if f["code"] == "thin_normal_section"]
    assert thin and thin[0]["value"] == pytest.approx(0.6, abs=0.0001)
    assert all(f["rule_source"] == rules.source and len(f["location_mm"]) == 3 for f in out["findings"])
    side = analyze(path, rules.model_copy(update={"pull_direction":(1,0,0)}))
    before = {f["face_index"] for f in out["findings"] if f["code"] == "insufficient_draft"}
    after = {f["face_index"] for f in side["findings"] if f["code"] == "insufficient_draft"}
    assert before != after


def test_unsupported_strategy_and_missing_slicer_fail(store, process_project, tmp_path, data, monkeypatch):
    with pytest.raises(ValueError, match="Unsupported"):
        m.execute(store, process_project, "simultaneous_5_axis", {})
    import trimesh
    from o3d.domains.manufacturing_ops.additive import slice_mesh
    domain = Manufacturing.model_validate(data)
    mesh = tmp_path/"cube.stl"
    trimesh.creation.box(extents=[10,10,10]).export(mesh)
    monkeypatch.setenv("O3D_CURA_ENGINE", str(tmp_path/"absent"))
    with pytest.raises(RuntimeError, match="No slicing"):
        slice_mesh(mesh, tmp_path/"run", domain.printer, domain.machines[3])


def test_worker_threading_event_cancel_contract(store, process_project):
    from threading import Event
    event = Event()
    out = m.execute(store, process_project, "post", {"setup_id":"top"}, event)
    assert out["posted_motion_check"]["matches_generated_path"]
    event.set()
    with pytest.raises(RuntimeError, match="cancelled"):
        m.execute(store, process_project, "post", {"setup_id":"top"}, event)


def test_canonical_bent_sheet_dxf_bend_allowance_cold_reopen_and_rules(tmp_path):
    from o3d.model import Project
    from o3d.store import Store
    from o3d.native_document import execute_native, query_native
    store = Store(tmp_path/"sheet")
    store.init(Project())
    for command, params in [
        ("sheet.manage_rules", {"name":"Test steel","thickness":2,"bend_radius":3,"k_factor":0.4,"active":True}),
        ("sketch.create", {"name":"Base","plane":"XY"}),
        ("sketch.rectangle.two_point", {"p0":[0,0],"p1":[100,60]}),
        ("sketch.finish", {}), ("sheet.flange", {"sketch":"Base"}),
        ("sheet.flange", {"edges":[[50,0,2]],"height":20,"angle":90}),
    ]:
        execute_native(store, store.load().revision, "human", command, params)
    project = Store(store.root).load()
    out = m.execute(store, project, "sheet_release", {})
    sheet = out["sheet"]
    assert sheet["dxf_audit"]["units"] == "mm"
    assert sheet["dxf_audit"]["bend_lines"] == 1
    assert sheet["dxf_audit"]["max_contour_deviation_mm"] < 0.01
    assert sheet["flat_pattern"]["bends"][0]["allowance"] == pytest.approx(math.pi/2*3.8)
    assert sheet["folded_step_volume_mm3"] == pytest.approx(sheet["native_volume_mm3"], rel=0.002)
    assert query_native(project, "sheet.flat_pattern")["outline"] == sheet["flat_pattern"]["outline"]
    execute_native(store, project.revision, "human", "sheet.manage_rules", {"name":"Test steel","k_factor":0.5})
    changed = m.execute(store, Store(store.root).load(), "sheet_release", {})
    assert changed["input_key"] != out["input_key"]
    assert changed["sheet"]["flat_pattern"]["bends"][0]["allowance"] == pytest.approx(math.pi/2*4)


def test_sheet_perforation_release_blocked(tmp_path):
    from o3d.native import NativeSession
    from o3d.domains.manufacturing_ops.sheet import release_session
    with NativeSession() as session:
        session.execute("sketch.create", {"name":"Base","plane":"XY"})
        session.execute("sketch.rectangle.two_point", {"p0":[0,0],"p1":[100,60]})
        session.execute("sketch.finish", {})
        session.execute("sheet.flange", {"sketch":"Base"})
        session.execute("sheet.perforate", {"diameter":3,"pitch":8,"margin":5})
        with pytest.raises(ValueError, match="metadata/B-rep mismatch"):
            release_session(session, tmp_path/"perforated")


@pytest.mark.skipif(not os.environ.get("O3D_CAM_PYTHON"), reason="real OpenCAMLib worker requires explicit host tool")
def test_opencamlib_ball_finishing_independent_sloped_plane_contact(tmp_path,data):
    import numpy as np
    import trimesh
    from o3d.domains.manufacturing_ops.schema import Operation
    from o3d.domains.manufacturing_ops.surface_cam import finish
    domain = Manufacturing.model_validate(data)
    mesh = trimesh.creation.box(extents=[30,30,10])
    mesh.apply_translation([15,15,-5])
    top = mesh.vertices[:,2] > -0.1
    mesh.vertices[top,2] = mesh.vertices[top,0]*0.1
    source = tmp_path/"wedge.stl"
    mesh.export(source)
    setup = domain.setups[0].model_copy(update={"stock":domain.setups[0].stock.model_copy(update={"maximum":(30,30,4)}),"clearance_z":10})
    tool = domain.tools[0].model_copy(update={"kind":"ball_endmill"})
    op = Operation(id="finish",name="Slope",setup_id=setup.id,tool_id=tool.id,strategy="parallel_finish",boundary=[(8,8),(22,8),(22,22),(8,22)],top_z=4,bottom_z=-2,stepover=0.4)
    path,report = finish(source,tmp_path/"finish",op,setup,tool,domain.machines[0])
    # Analytic ball-tip contact on z=0.1*x: tip z=surface+R*(sqrt(1+slope^2)-1).
    compensation = 3*(math.sqrt(1.01)-1)
    points = [p for p in path.moves if p["motion"] == "feed"]
    assert points and max(abs(p["z"]-(0.1*p["x"]+compensation)) for p in points) < 1e-5
    assert report["sampled_gouges"] == 0
    assert report["minimum_sampled_ball_clearance_mm"] >= -1e-5
    assert report["flat_reference_scallop_bound_mm"] <= setup.tolerance_mm
    assert report["independent_sphere_samples"] > 100
    assert len(report["binding_sha256"]) == 64


@pytest.mark.skipif(not os.environ.get("O3D_CURA_ENGINE"), reason="real CuraEngine integration requires explicit host tool")
def test_real_cura_slice_layers_extrusion_and_volume_fit(tmp_path, data):
    import trimesh
    from o3d.domains.manufacturing_ops.additive import slice_mesh, parse_fff
    domain = Manufacturing.model_validate(data)
    source = tmp_path/"cube.stl"
    trimesh.creation.box(extents=[10,10,5]).export(source)
    destination = tmp_path/"sliced"
    out = slice_mesh(source, destination, domain.printer, domain.machines[3])
    assert out["provenance"]["engine"] == "CuraEngine"
    assert out["analysis"]["layer_count"] == 25
    assert out["analysis"]["positive_extrusion_moves"] > 100
    assert out["analysis"]["extrusion_filament_mm"] > 20
    assert out["analysis"]["layers"][-1]["z_mm"] == pytest.approx(5)
    readback = parse_fff((destination/"build.gcode").read_text(), ((0,0,0),(200,200,200)))
    assert readback["layer_count"] == 25
    assert len(out["provenance"]["output_sha256"]) == 64
    domain.printer.center_xy = (1,1)
    with pytest.raises(ValueError, match="fit"):
        slice_mesh(source, tmp_path/"bad", domain.printer, domain.machines[3])
