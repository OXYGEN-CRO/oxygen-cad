"""Assembly acceptance on the source-built engine and canonical Store documents.

The tests deliberately compare matrices/intervals/volumes against independent numbers,
and reopen each committed edit through a new process instead of retaining a raw session.
"""
import csv
import hashlib
import io
import json
import math

import pytest

from o3d.domains import assemblies
from o3d.domains.assembly_models import Stack, native_key, stack_result
from o3d.model import Command, Project
from o3d.native import NativeSession
from o3d.native_document import execute_native, query_native
from o3d.store import Conflict, Store


@pytest.fixture
def assembly_store(tmp_path):
    store = Store(tmp_path / "mechanism")
    store.init(Project(name="Mechanism acceptance"))
    return store


def edit(store, command, **params):
    project = store.load()
    return execute_native(store, project.revision, "human", command, params)["result"]


def run(store, operation, payload=None):
    project = store.load()
    result = assemblies.execute(store, project, operation, payload or {})
    if result.get("changed"):
        candidate = Project.model_validate(result["project"]) if "project" in result else project.model_copy(deep=True)
        if "data" in result:
            candidate.domains["assemblies"] = result["data"]
        store.apply(Command(expected_revision=project.revision, actor="human", project=candidate, summary=f"Assembly {operation}"))
    return result


def query(store, command, **params):
    return query_native(store.load(), command, params, store=store)


def part(store, name, corner=(0, 0, 0), size=(10, 10, 10), parent=0):
    made = edit(store, "component.create", name=name, parent=parent, grounded=False)
    edit(store, "solid.box", length=size[0], width=size[1], height=size[2], corner=list(corner), body_name=name + "Body", name=name + "Box")
    return made


def pair(store, kind="slider", face=False):
    a = part(store, "Base")
    b = part(store, "Moving", (20, 0, 0))
    edit(store, "occurrence.ground", occurrence=a["occurrence"], grounded=True)
    if face:
        made = edit(store, "joint.create", type=kind, name="J", a={"occurrence": a["occurrence"], "face": [10, 5, 5]}, b={"occurrence": b["occurrence"], "face": [20, 5, 5]})
    else:
        made = edit(store, "joint.as_built", type=kind, name="J", a=a["occurrence"], b=b["occurrence"])
    return a, b, made


def matrix(store, occurrence):
    return next(o["transform"] for o in store.load().native_document["occurrences"] if o["id"] == occurrence)


def multiply(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)] for r in range(3)]


def rz(deg):
    s, c = math.sin(math.radians(deg)), math.cos(math.radians(deg))
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def ry(deg):
    s, c = math.sin(math.radians(deg)), math.cos(math.radians(deg))
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


@pytest.mark.parametrize("kind,dof,values,translation,rotation", [
    ("rigid", 0, [], [0, 0, 0], rz(0)),
    ("revolute", 1, [30], [0, 0, 0], rz(30)),
    ("slider", 1, [7], [0, 0, 7], rz(0)),
    ("cylindrical", 2, [30, 7], [0, 0, 7], rz(30)),
    ("pin_slot", 2, [30, 7], [7, 0, 0], rz(30)),
    ("planar", 3, [3, 7, 30], [3, 7, 0], rz(30)),
    ("ball", 3, [30, 20, 10], [0, 0, 0], multiply(multiply(rz(30), ry(20)), rz(10))),
])
def test_seven_families_actual_dofs_transforms_and_reopen(assembly_store, kind, dof, values, translation, rotation):
    store = assembly_store
    a, b, made = pair(store, kind)
    listed = query(store, "joint.list")
    assert next(d["dof"] for d in listed["dof"] if d["occurrence"] == b["occurrence"]) == dof
    assert listed["errors"] == listed["conflicts"] == []
    assert matrix(store, b["occurrence"])[3][:3] == pytest.approx([0, 0, 0])  # as-built causes no jump
    if values:
        edit(store, "joint.drive", joint=made["joint"], values=values)
    m = matrix(store, b["occurrence"])
    assert m[3][:3] == pytest.approx(translation, abs=1e-6)
    for row in range(3):
        assert [m[col][row] for col in range(3)] == pytest.approx(rotation[row], abs=1e-6)
    reopened = Store(store.root).load()
    assert reopened.native_document == store.load().native_document
    assert query_native(reopened, "joint.list", store=store)["joints"][0]["type"] == kind
    assert matrix(store, a["occurrence"])[3][:3] == pytest.approx([0, 0, 0])


def test_shared_nested_independent_copies_ground_pending_undo_and_bom(assembly_store):
    store = assembly_store
    kit = edit(store, "component.create", name="Gearbox", grounded=False)
    child = part(store, "Shaft", parent=kit["component"])
    edit(store, "occurrence.ground", occurrence=child["occurrence"], grounded=True)
    copy = edit(store, "occurrence.copy", occurrence=kit["occurrence"], translate=[50, 0, 0])
    independent = edit(store, "component.paste_new", component=kit["component"], translate=[100, 0, 0])
    assert independent["component"] != kit["component"]
    parameters = query(store, "parameters.list")["parameters"]
    length = next(p for p in parameters if p.get("feature") == "ShaftBox" and p.get("input") == "Length")
    edit(store, "parameters.change", name=length["name"], expression="20 mm")
    assert query(store, "inspect.measure")["total"]["volume_mm3"] == pytest.approx(5000)  # two edited instances + untouched independent copy
    before = matrix(store, kit["occurrence"])
    edit(store, "occurrence.move", occurrence=kit["occurrence"], translate=[0, 20, 0], capture=False)
    assert matrix(store, kit["occurrence"]) == before
    assert query(store, "component.list")["pending_moves"] == [kit["occurrence"]]
    edit(store, "component.revert_position")
    assert query(store, "component.list")["pending_moves"] == []
    edit(store, "occurrence.move", occurrence=kit["occurrence"], translate=[0, 20, 0], capture=False)
    edit(store, "component.capture_position")
    assert matrix(store, kit["occurrence"])[3][:3] == pytest.approx([0, 20, 0])
    assert matrix(store, child["occurrence"])[3][:3] == pytest.approx([0, 0, 0])  # grounded to its moving parent
    snapshot = assemblies.bom_snapshot(store, store.load(), {"group_by": ["definition"]})
    row = next(r for r in snapshot["rows"] if child["component"] in r["components"])
    assert row["count"] == 2
    assert sorted(row["occurrence_paths"]) == sorted([[kit["occurrence"], child["occurrence"]], [copy["occurrence"], child["occurrence"]]])
    assert snapshot["total_occurrences"] == 3  # flat lists leaf parts; top lists three gearbox placements
    assert assemblies.bom_snapshot(store, store.load(), {"levels": "top"})["total_occurrences"] == 3
    row_key = row["key"]
    edit(store, "component.metadata.set", component=child["component"], part_number="S-10", revision="B")
    after = assemblies.bom_snapshot(store, store.load(), {"group_by": ["definition"]})
    assert next(r for r in after["rows"] if child["component"] in r["components"])["key"] == row_key
    rev = store.load().revision
    store.apply(Command(expected_revision=rev, action="undo", actor="human"))
    assert query(store, "component.metadata", component=child["component"])["part_number"] != "S-10"
    store.apply(Command(expected_revision=store.load().revision, action="redo", actor="human"))
    assert query(store, "component.metadata", component=child["component"])["revision"] == "B"
    with pytest.raises(ValueError, match="rigid|scale|shear"):
        edit(store, "occurrence.move", occurrence=copy["occurrence"], matrix=[2,0,0,0, 0,1,0,0, 0,0,1,0])


def test_limits_lock_saved_home_suppress_and_conflicts(assembly_store):
    store = assembly_store
    _a, b, made = pair(store, "cylindrical")
    jid = made["joint"]
    edit(store, "joint.limits", joint=jid, index=0, min=-45, max=45)
    edit(store, "joint.limits", joint=jid, index=1, min=-5, max=5)
    edit(store, "joint.drive", joint=jid, values=[100, 20])
    assert [v["value"] for v in query(store, "joint.list")["joints"][0]["values"]] == pytest.approx([45, 5])
    run(store, "pose.capture", {"name": "Home"})
    run(store, "joint.lock", {"joint": jid})
    edit(store, "joint.drive", joint=jid, values=[0, 0])
    assert [v["value"] for v in query(store, "joint.list")["joints"][0]["values"]] == pytest.approx([45, 5])
    run(store, "joint.unlock", {"joint": jid})
    edit(store, "joint.drive", joint=jid, values=[-40, -4])
    run(store, "pose.restore", {"name": "Home"})
    assert matrix(store, b["occurrence"])[3][2] == pytest.approx(5)
    edit(store, "joint.edit", joint=jid, suppressed=True)
    listed = query(store, "joint.list")
    assert next(d["dof"] for d in listed["dof"] if d["occurrence"] == b["occurrence"]) == 6
    edit(store, "joint.edit", joint=jid, suppressed=False)
    assert query(store, "joint.inspect")["joints"][0]["a"]["resolves"]
    stale = store.load().revision
    edit(store, "joint.drive", joint=jid, values=[0, 0])
    with pytest.raises(Conflict, match="Revision conflict"):
        execute_native(store, stale, "human", "joint.drive", {"joint": jid, "values": [1, 1]})


def test_floating_chains_and_conflicting_anchor_report_diagnostics(assembly_store):
    store = assembly_store
    a, b, made = pair(store)
    edit(store, "occurrence.ground", occurrence=a["occurrence"], grounded=False)
    assert run(store, "inspect")["anchors"]["implicit"] == [a["occurrence"]]
    edit(store, "occurrence.ground", occurrence=a["occurrence"], grounded=True)
    pedestal = part(store, "Loose pedestal", (50, 0, 0))
    turret = part(store, "Loose turret", (70, 0, 0))
    floating = edit(store, "joint.as_built", name="Floating", type="revolute", a=pedestal["occurrence"], b=turret["occurrence"])
    assert query(store, "joint.list")["floating"]
    before = matrix(store, turret["occurrence"])
    unable = edit(store, "joint.drive", joint=floating["joint"], value=8)
    assert unable.get("reason")
    assert matrix(store, turret["occurrence"]) == before
    edit(store, "joint.create", type="rigid", name="Conflicting fixed anchor", flip=True,
         a={"occurrence": 0, "point": [0, 0, 0]}, b={"occurrence": b["occurrence"], "point": [20, 0, 0]})
    listed = query(store, "joint.list")
    assert listed["conflicts"] or listed["errors"]


def test_swept_contact_stops_between_clear_endpoints_and_disabled_passes(assembly_store):
    store = assembly_store
    a, b, made = pair(store, "slider", face=True)
    edit(store, "joint.drive", joint=made["joint"], value=10)
    edit(store, "contact.create", occurrences=[a["occurrence"], b["occurrence"]], name="Rail stop")
    stopped = edit(store, "joint.drive", joint=made["joint"], value=-30)
    assert isinstance(stopped.get("stopped_by_contact"), dict)
    stopped_value = query(store, "joint.list")["joints"][0]["values"][0]["value"]
    assert -30 < stopped_value < 10
    away = edit(store, "joint.drive", joint=made["joint"], value=10)
    assert not away.get("stopped_by_contact")
    before_budget = store.load()
    with pytest.raises(ValueError, match="sampling budget"):
        edit(store, "joint.drive", joint=made["joint"], value=-100000)
    assert store.load() == before_budget
    edit(store, "contact.disable_all")
    passed = edit(store, "joint.drive", joint=made["joint"], value=-30)
    assert not passed.get("stopped_by_contact")
    assert query(store, "joint.list")["joints"][0]["values"][0]["value"] == pytest.approx(-30)


def test_revolute_sweep_blocks_between_clear_endpoints(assembly_store):
    store = assembly_store
    arm = part(store, "Arm", (20, -2, -2), (4, 4, 4))
    blocker = part(store, "Blocker", (-2, 20, -2), (4, 4, 4))
    pivot = edit(store, "joint.create", name="Pivot", type="revolute", flip=True,
                 a={"occurrence": 0, "point": [0, 0, 0]}, b={"occurrence": arm["occurrence"], "point": [0, 0, 0]})
    edit(store, "contact.create", occurrences=[arm["occurrence"], blocker["occurrence"]])
    stopped = edit(store, "joint.drive", joint=pivot["joint"], value=180)
    assert stopped["stopped_by_contact"]
    angle = query(store, "joint.list")["joints"][0]["values"][0]["value"]
    assert 60 < angle < 90
    edit(store, "contact.disable_all")
    edit(store, "joint.drive", joint=pivot["joint"], value=180)
    assert query(store, "joint.list")["joints"][0]["values"][0]["value"] == pytest.approx(180)


def test_nonidentity_rigid_group_and_as_built_hinge_keep_relative_placements(assembly_store):
    store = assembly_store
    base = part(store, "Base", (-20, -20, -20))
    a = part(store, "Plate A", (20, 0, 0))
    b = part(store, "Plate B", (40, 0, 0))
    edit(store, "occurrence.ground", occurrence=base["occurrence"], grounded=True)
    edit(store, "occurrence.move", occurrence=a["occurrence"], translate=[3, 4, 5])
    edit(store, "occurrence.move", occurrence=b["occurrence"], translate=[13, 14, 5], axis=[0, 0, 1], angle="30 deg")
    before = {p["occurrence"]: matrix(store, p["occurrence"]) for p in [a, b]}
    edit(store, "joint.rigid_group", occurrences=[a["occurrence"], b["occurrence"]])
    group_id = query(store, "joint.list")["joints"][0]["id"]
    hinge = edit(store, "joint.as_built", name="Group hinge", type="revolute", a=base["occurrence"], b=a["occurrence"])
    assert all(matrix(store, oid) == m for oid, m in before.items())
    delta = [before[b["occurrence"]][3][i] - before[a["occurrence"]][3][i] for i in range(3)]
    edit(store, "joint.drive", joint=hinge["joint"], value=90)
    moved_delta = [matrix(store, b["occurrence"])[3][i] - matrix(store, a["occurrence"])[3][i] for i in range(3)]
    assert moved_delta == pytest.approx([-delta[1], delta[0], delta[2]], abs=1e-6)
    edit(store, "joint.edit", joint=group_id, suppressed=True)
    assert next(d["dof"] for d in query(store, "joint.list")["dof"] if d["occurrence"] == b["occurrence"]) == 6


def test_motion_kinematics_trace_and_explode_leave_engineering_pose_unchanged(assembly_store):
    store = assembly_store
    a, b, made = pair(store)
    edit(store, "motion.study", name="Travel", steps=100, keys=[{"joint": made["joint"], "points": [[0, 0], [100, 10]]}])
    before = store.load().native_document["occurrences"]
    report = run(store, "report", {"command": "motion.plot", "params": {"study": "Travel", "samples": 11, "duration": 2}})
    series = report["result"]["joints"][0]
    assert series["value"] == pytest.approx(list(range(11)))
    assert series["velocity"] == pytest.approx([5] * 11)
    assert series["acceleration"] == pytest.approx([0] * 11, abs=1e-6)
    trace = run(store, "query", {"command": "motion.trace", "params": {"study": "Travel", "occurrence": b["occurrence"], "point": [20, 0, 0], "samples": 3}})["result"]
    for point, height in zip(trace["points"], [0, 5, 10]):
        assert point == pytest.approx([20, 0, height])
    assert store.load().native_document["occurrences"] == before
    edit(store, "motion.trace.keep", name="Travel trace", study="Travel", occurrence=b["occurrence"], point=[20, 0, 0], samples=5)
    assert len(query(store, "motion.list")["traces"]) == 1
    edit(store, "explode.create", name="Service", moves=[{"occurrence": b["occurrence"], "translate": [0, 0, 100]}])
    edit(store, "explode.show", view="Service")
    assert store.load().native_document["occurrences"] == before
    shown = query(store, "oxygen.scene", mesh=False)
    assert next(body for body in shown["bodies"] if body["component"] == b["component"])["bbox"]["max"][2] == pytest.approx(110)
    clearance = run(store, "query", {"command": "inspect.clearance", "params": {"clearance": 11, "nearest": 1}})["result"]
    distances = [entry["distance_mm"] for near in clearance["nearest"].values() for entry in near]
    assert distances == pytest.approx([10, 10])  # ignores exploded display offset
    opened = run(store, "report.open", {"report_artifact": report["report_artifact"]})
    assert opened["stale"] is True
    assert opened["result"] == report["result"]


def test_all_pairs_overlap_exact_clearance_bom_export_and_incomplete_policy(assembly_store):
    store = assembly_store
    first = part(store, "First")
    second = part(store, "Second", (5, 0, 0))
    third = part(store, "Third", (15.5, 0, 0))
    part(store, "Contained", (2, 2, 2), (2, 2, 2))
    part(store, "Tangent", (25.5, 0, 0))
    result = run(store, "report", {"command": "inspect.clearance", "params": {"clearance": 0.6, "nearest": 2}})
    assert result["status"] == "interference"
    volumes = [overlap["volume_mm3"] for overlap in result["result"]["overlaps"]]
    assert sorted(volumes) == pytest.approx([8, 500], abs=1e-5)
    distances = [entry["distance_mm"] for near in result["result"]["nearest"].values() for entry in near]
    assert any(abs(d - 0.5) < 1e-6 for d in distances)
    assert any(abs(near["distance_mm"]) < 1e-6 for near in result["result"]["near"])
    exported = run(store, "export_bom", {"format": "csv"})
    rows = list(csv.DictReader(io.StringIO(store.artifact(exported["artifact"]).read_text())))
    assert sum(int(r["quantity"]) for r in rows) == 5
    assert len({r["key"] for r in exported["result"]["rows"]}) == 5
    assert assemblies._quality("inspect.interference", {"count": 0, "unchecked": [{"reason": "budget"}]}) == "unresolved"
    assert assemblies._quality("motion.interference", {"clear": True, "truncated": True}) == "unresolved"
    assert assemblies._quality("motion.interference", {"clear": True}) == "sampled_clear"
    exhausted = run(store, "report", {"command": "inspect.interference", "params": {"budget_s": 1e-9}})
    assert exhausted["status"] == "unresolved"
    assert exhausted["result"]["budget_exceeded"] and exhausted["result"]["unchecked"]
    with pytest.raises(ValueError, match="file paths"):
        run(store, "query", {"command": "motion.export", "params": {"path": "/tmp/unowned.json"}})


def test_stale_and_ambiguous_occurrence_references_rejected(assembly_store):
    store = assembly_store
    a = part(store, "A")
    run(store, "native", {"command": "occurrence.move", "params": {"occurrence": a["occurrence"], "translate": [1, 0, 0]}})
    with pytest.raises(ValueError, match="Stale occurrence"):
        run(store, "native", {"command": "occurrence.move", "params": {"occurrence": 99999, "translate": [1, 0, 0]}})
    proposal = store.load().model_copy(deep=True)
    o = proposal.native_document["occurrences"][0]
    proposal.native_document["occurrences"].append({**o, "id": 99999})
    with pytest.raises(ValueError, match="Ambiguous occurrence"):
        assemblies._guard_refs(proposal, "occurrence.move", {"occurrence": o["name"]})


def test_explicit_fit_and_fastener_stacks_missing_limits_and_invalid_inputs():
    fit = Stack(name="Diametral fit", dimensions=[{"name": "Hole", "nominal": 10, "lower": 0, "upper": 0.015}, {"name": "Shaft", "nominal": 10, "lower": -0.014, "upper": -0.005, "sign": -1}], required_min=0.005, required_max=0.03)
    result = stack_result(fit)
    assert [result["minimum"], result["maximum"]] == pytest.approx([0.005, 0.029])
    assert result["status"] == "passed"
    fastener = Stack(name="Engagement", dimensions=[{"name": "Bolt under head", "nominal": 30, "lower": -0.2, "upper": 0.2}, {"name": "Grip including washers", "nominal": 24, "lower": -0.4, "upper": 0.4, "sign": -1}], required_min=5, required_max=7)
    assert [stack_result(fastener)["minimum"], stack_result(fastener)["maximum"]] == pytest.approx([5.4, 6.6])
    assert stack_result(Stack(name="Unspecified", dimensions=[{"name": "Length", "nominal": 1}]))["status"] == "unresolved"
    with pytest.raises(ValueError):
        assemblies.validate({"schema_version": 1, "samples": 1})
    with pytest.raises(ValueError):
        Stack(name="Bad", dimensions=[{"name": "Length", "nominal": 1, "lower": 2, "upper": 1}])
    with pytest.raises(ValueError):
        assemblies.validate({"clearance_mm": float("nan")})


def test_persistent_face_origins_follow_parameter_edits_and_report_lost_interface(assembly_store):
    store = assembly_store
    edit(store, "parameters.add", name="PlateThickness", expression="4 mm")
    base = edit(store, "component.create", name="Plate", grounded=False)
    edit(store, "solid.box", length=20, width=20, height="PlateThickness", name="PlateBox")
    moving = part(store, "Foot", (30, 0, 0), (10, 10, 2))
    edit(store, "occurrence.ground", occurrence=base["occurrence"], grounded=True)
    edit(store, "joint.origin", name="Plate shoulder", occurrence=base["occurrence"], face=[10, 10, 4])
    edit(store, "joint.origin", name="Foot seat", occurrence=moving["occurrence"], face=[35, 5, 0])
    edit(store, "joint.create", name="Seat", type="rigid", a="Plate shoulder", b="Foot seat")
    before = query(store, "joint.inspect")["joints"][0]
    assert before["a"]["resolves"] and before["a"]["references"]
    edit(store, "parameters.change", name="PlateThickness", expression="9 mm")
    after = query(store, "joint.inspect")["joints"][0]
    assert after["a"]["frame"]["origin"][2] == pytest.approx(9)
    assert after["b"]["frame"]["origin"][2] == pytest.approx(9)
    assert after["a"]["references"][0]["name"] == before["a"]["references"][0]["name"]
    assert after["a"]["references"][0]["feature"]["name"] == "PlateBox"
    with pytest.raises(ValueError, match="no face|named"):
        edit(store, "joint.origin", name="Missing interface", occurrence=base["occurrence"], face="F999999:top")
    edit(store, "timeline.suppress", feature="PlateBox", suppressed=True)
    lost = query(store, "joint.inspect")["joints"][0]
    assert lost["a"]["resolves"] is False and lost["a"]["error"]
    edit(store, "timeline.suppress", feature="PlateBox", suppressed=False)
    assert query(store, "joint.inspect")["joints"][0]["a"]["resolves"] is True


def test_gears_rack_and_screw_match_analytic_transmission_ratios(assembly_store):
    store = assembly_store
    base = part(store, "Ground", (-50, -50, -20))
    edit(store, "occurrence.ground", occurrence=base["occurrence"], grounded=True)
    joints = []
    for index, kind in enumerate(["revolute", "revolute", "slider", "cylindrical"]):
        component = part(store, f"Part{index}", (20 * index, 0, 0))
        joints.append(edit(store, "joint.as_built", name=f"Joint{index}", type=kind, a=base["occurrence"], b=component["occurrence"])["joint"])
    edit(store, "joint.gear", a=joints[0], b=joints[1], ra=20, rb=10)
    edit(store, "joint.rack_pinion", a=joints[0], b=joints[2], radius=5)
    edit(store, "joint.screw", joint=joints[3], pitch=2)
    edit(store, "joint.drive", joint=joints[0], value=90)
    edit(store, "joint.drive", joint=joints[3], values=[180, 0])
    values = {j["id"]: [v["value"] for v in j["values"]] for j in query(store, "joint.list")["joints"]}
    assert values[joints[1]][0] == pytest.approx(-180)
    assert values[joints[2]][0] == pytest.approx(5 * math.pi / 2)
    assert values[joints[3]] == pytest.approx([180, 1])


def test_raw_design_import_integrated_edit_and_linked_source_revision(assembly_store, tmp_path):
    store = assembly_store
    raw = tmp_path / "source.json"
    raw_native = tmp_path / "source.solidcraft"
    # A genuine raw native authoring session, closed before any Oxygen project edit.
    with NativeSession() as source:
        source.execute("parameters.add", {"name": "Length", "expression": "10 mm"})
        source.execute("solid.box", {"length": "Length", "width": 10, "height": 10, "body_name": "SupplierPart"})
        source.execute("file.save", {"path": str(raw)})
        source.execute("file.save", {"path": str(raw_native)})
    proposal = store.load().model_copy(deep=True)
    proposal.native_document = json.loads(raw.read_text())
    store.apply(Command(expected_revision=proposal.revision, actor="human", project=proposal, summary="Import raw native design"))
    edit(store, "parameters.change", name="Length", expression="12 mm")
    assert query(store, "inspect.measure")["total"]["volume_mm3"] == pytest.approx(1200)
    assert Store(store.root).load().native_document == store.load().native_document

    linked_store = Store(tmp_path / "linked")
    linked_store.init(Project(name="Linked product"))
    def register(content):
        digest = hashlib.sha256(content).hexdigest()
        path = linked_store.root / "assets" / f"{digest}.solidcraft"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        current = linked_store.load()
        current.domains.setdefault("interop", {"schema_version": 1}).setdefault("assets", []).append({"id": digest, "sha256": digest, "file": f"assets/{digest}.solidcraft", "media_type": "application/x-solidcraft"})
        linked_store.apply(Command(expected_revision=current.revision, actor="human", project=current, summary="Import linked source revision"))
        return digest
    first_asset = register(raw_native.read_bytes())
    inserted = edit(linked_store, "component.insert_design", asset_id=first_asset, name="Supplier")
    copied = edit(linked_store, "occurrence.copy", component=inserted["component"], translate=[30, 0, 0])
    before_occurrences = linked_store.load().native_document["occurrences"]
    with NativeSession() as source:
        source.execute("doc.open", {"path": str(raw_native)})
        source.execute("parameters.change", {"name": "Length", "expression": "20 mm"})
        source.execute("file.save", {"path": str(raw_native)})
    second_asset = register(raw_native.read_bytes())
    edit(linked_store, "component.relink", component=inserted["component"], asset_id=second_asset)
    assert linked_store.load().native_document["occurrences"] == before_occurrences
    assert query(linked_store, "inspect.measure")["total"]["volume_mm3"] == pytest.approx(4000)
    edit(linked_store, "component.break_link", component=inserted["component"])
    assert query(linked_store, "component.links")["links"] == []


def test_atomic_relation_sets_and_configuration_specific_procurement(assembly_store):
    store = assembly_store
    base = part(store, "Base")
    moving = part(store, "Moving", (20, 0, 0))
    edit(store, "occurrence.ground", occurrence=base["occurrence"], grounded=True)
    origin_a = {"occurrence": base["occurrence"], "point": [0, 0, 0], "z": [0, 0, 1]}
    origin_b = {"occurrence": moving["occurrence"], "point": [20, 0, 0], "z": [0, 0, 1]}
    run(store, "constraints.create_set", {"name": "Seating", "relations": [
        {"type": "parallel", "a": origin_a, "b": origin_b, "flip": True},
        {"type": "point_on_plane", "a": origin_a, "b": origin_b},
    ]})
    group = store.load().domains["assemblies"]["constraint_sets"][0]
    assert len(group["joints"]) == 2
    assert query(store, "joint.list")["errors"] == []
    run(store, "constraints.suppress_set", {"id": group["id"], "suppressed": True})
    assert all(j["suppressed"] for j in query(store, "joint.list")["joints"])
    run(store, "constraints.suppress_set", {"id": group["id"], "suppressed": False})
    assert not any(j["suppressed"] for j in query(store, "joint.list")["joints"])
    edit(store, "config.configure", features=["MovingBox"])
    edit(store, "config.row", name="Service", values={"suppress:MovingBox": True})
    preview = run(store, "query", {"command": "config.preview", "params": {"row": "Service"}})
    assert preview["result"]["errors"] == []
    assert query(store, "config.table")["active"] == "Default"
    # Suppressing geometry alone does not silently remove a procurement item.
    edit(store, "config.activate", row="Service")
    assert assemblies.bom_snapshot(store, store.load())["total_occurrences"] == 2
    run(store, "bom.exclude", {"component": moving["component"], "excluded": True})
    assert assemblies.bom_snapshot(store, store.load())["total_occurrences"] == 1
    edit(store, "config.activate", row="Default")
    assert assemblies.bom_snapshot(store, store.load())["total_occurrences"] == 2
