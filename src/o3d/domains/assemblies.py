"""Persisted SolidCraft assembly workflows and provenance-aware review artifacts.

No second assembly solver is maintained here. Every placement/joint/contact/trace/BOM
operation goes through the canonical native document and Store's optimistic transactions.
"""
from __future__ import annotations

import csv
import io
import hashlib
import json
from uuid import uuid4

from .assembly_models import AssemblyData, ConstraintSet, Pose, Stack, native_key, stack_result


READ_COMMANDS = frozenset({
    "component.list", "component.properties", "occurrence.properties", "component.metadata", "component.links", "component.copies",
    "joint.list", "joint.inspect", "contact.list", "contact.check", "motion.list", "motion.export", "motion.trace", "motion.plot",
    "motion.interference", "explode.list", "assembly.bom", "inspect.interference", "inspect.clearance", "config.table", "config.rules", "config.preview",
})
EDIT_COMMANDS = frozenset({
    "component.create", "component.from_bodies", "component.activate", "component.rename", "component.delete", "component.unlock",
    "component.move_bodies", "component.move_sketches", "component.paste_new", "component.capture_position", "component.revert_position",
    "component.metadata.set", "component.insert_design", "component.relink", "component.break_link", "occurrence.copy", "occurrence.move", "occurrence.drag", "occurrence.ground",
    "joint.origin", "joint.create", "joint.as_built", "joint.rigid_group", "joint.drive", "joint.reach", "joint.motion_link", "joint.gear",
    "joint.rack_pinion", "joint.screw", "joint.cam", "joint.limits", "joint.edit", "joint.delete", "joint.solve", "joint.sweep",
    "contact.enable_sets", "contact.enable_all", "contact.disable_all", "contact.create", "contact.edit",
    "motion.study", "motion.play", "motion.trace.keep", "motion.trace.edit", "motion.delete", "explode.create", "explode.show", "explode.delete",
    "config.configure", "config.column", "config.row", "config.activate", "config.rule",
})


def validate(data) -> dict:
    return AssemblyData.model_validate(data or {}).model_dump(mode="json")


def _cancel(cancel):
    if cancel:
        if callable(cancel):
            stopped = cancel()
        else:
            stopped = cancel.is_set()
        if stopped:
            raise RuntimeError("Assembly operation cancelled")


def _query(store, project, command, params=None):
    if command not in READ_COMMANDS:
        raise ValueError("This operation requires an assembly read command")
    from ..native_document import query_native
    if command in {"inspect.interference", "inspect.clearance", "contact.check"}:
        project = project.model_copy(deep=True)
        context = project.domains.setdefault("modeling", {}).setdefault("native_context", {})
        context["pending_moves"] = {}
    return query_native(project, command, params or {}, store=store)


def bom_snapshot(store, project, params=None):
    """Native quantities with stable definition-based association keys for drawings."""
    result = _query(store, project, "assembly.bom", params or {})
    doc = project.native_document or {}
    occurrences = doc.get("occurrences", [])
    data = AssemblyData.model_validate(project.domains.get("assemblies", {}))
    active = _query(store, project, "config.table").get("active") or "Default"
    excluded = set(data.bom_exclusions.get(active, []))
    placements = []
    def visit(parent, path, blocked=False):
        if len(path) >= 64 or len(placements) >= 100_000:
            raise ValueError("Assembly placement graph exceeds traversal budget")
        for occurrence in occurrences:
            if occurrence.get("parent", 0) == parent:
                nested = [*path, occurrence["id"]]
                hidden = blocked or occurrence["component"] in excluded
                placements.append({"path": nested, "occurrence": occurrence["id"], "component": occurrence["component"], "name": occurrence["name"], "excluded": hidden})
                visit(occurrence["component"], nested, hidden)
    visit(result.get("component", 0), [])
    if excluded and result.get("group_children"):
        for row in result.get("rows", []):
            if row.get("children"):
                kit_paths = [p["path"] for p in placements if p["component"] in row.get("components", []) and not p["excluded"]]
                if any(p["excluded"] and any(p["path"][:len(path)] == path for path in kit_paths) for p in placements):
                    raise ValueError("Configured exclusion inside a grouped kit requires an ungrouped BOM; disable group_children")
    for row in result.get("rows", []):
        ids = sorted(row.get("components", []))
        row["key"] = "bom:" + hashlib.sha256(json.dumps(ids).encode()).hexdigest()[:20]
        candidates = [p for p in placements if p["component"] in ids and (result.get("levels") != "top" or len(p["path"]) == 1)]
        row["occurrence_paths"] = [p["path"] for p in candidates if not p["excluded"]]
        row["component_ids"] = ids
        row["feature_ids"] = [f["id"] for f in doc.get("features", []) if f.get("component", 0) in ids]
        if excluded:
            previous = row["count"]
            row["count"] = len(row["occurrence_paths"])
            for child in row.get("children", []):
                child["count"] = child["count"] * row["count"] // max(previous, 1)
    result["rows"] = [r for r in result.get("rows", []) if r["count"]]
    if excluded:
        result["total_occurrences"] = sum(r["count"] + sum(c["count"] for c in r.get("children", [])) for r in result["rows"])
    result["configuration"] = active
    result["excluded_component_ids"] = sorted(excluded)
    return result


def _safe_params(params):
    """Artifact paths are host allocated; a design cannot request arbitrary file access."""
    if not isinstance(params, dict):
        raise ValueError("Assembly params must be an object")
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"path", "file", "directory", "font"} and isinstance(child, str):
                    raise ValueError("Use assembly artifact/import operations instead of file paths")
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(params)
    json.dumps(params, allow_nan=False)
    return params


def _guard_refs(project, command, params):
    """Occurrence IDs and paths distinguish shared placements; never pick the first duplicate name."""
    doc = project.native_document or {}
    occurrences = doc.get("occurrences", [])
    def entity(value, records, label, root=False):
        if root and (value == 0 or value == "root"):
            return
        found = [record for record in records if str(record.get("id")) == str(value) or record.get("name") == value]
        if len(found) != 1:
            raise ValueError(f"{'Ambiguous' if found else 'Stale'} {label} reference {value!r}; select an exact current ID")
    def check(value):
        if value == 0 or value == "root":
            return
        found = [o for o in occurrences if str(o.get("id")) == str(value) or o.get("name") == value]
        if len(found) != 1:
            raise ValueError(f"{'Ambiguous' if found else 'Stale'} occurrence reference {value!r}; select an exact occurrence ID/path")
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "occurrence":
                    if isinstance(child, list):
                        for item in child:
                            check(item)
                    else:
                        check(child)
                elif key in {"occurrences", "parent_path"} or (key == "path" and isinstance(child, list)):
                    for item in child:
                        check(item)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(params)
    if command == "joint.as_built":
        check(params.get("a")); check(params.get("b"))
    assembly = doc.get("assembly", {})
    for key in ("component", "parent"):
        if key in params:
            entity(params[key], doc.get("components", []), "component", root=True)
    for key in ("joint", "study"):
        if key in params:
            entity(params[key], assembly.get("joints" if key == "joint" else "studies", []), key)
    for value in params.get("joints", []):
        entity(value, assembly.get("joints", []), "joint")
    if command in {"joint.motion_link", "joint.gear", "joint.rack_pinion", "joint.screw", "joint.cam"}:
        for key in ("a", "b"):
            if key in params:
                entity(params[key], assembly.get("joints", []), "joint")
    if command == "contact.edit":
        entity(params.get("set"), assembly.get("contact_sets", []), "contact set")
    if command in {"explode.show", "explode.delete"} and params.get("view") not in {None, "none"}:
        entity(params["view"], assembly.get("exploded", []), "exploded view")


def _transaction(store, project, commands, actor="human", data=None):
    from ..native_document import prepare_native_command
    current = project
    results = []
    for command, params in commands:
        _safe_params(params)
        _guard_refs(current, command, params)
        current, result = prepare_native_command(current, command, params, store=store)
        results.append(result)
    if data is not None:
        current.domains["assemblies"] = validate(data)
    # The Jobs owner performs the sole Store.apply after cancellation/revision checks.
    return {"project": current.model_dump(mode="json"), "changed": True, "results": results}


def _quality(command, result):
    incomplete = bool(result.get("unchecked") or result.get("truncated") or result.get("budget_exceeded") or result.get("skipped_pairs") or result.get("pairs_not_checked"))
    if incomplete:
        return "unresolved"
    if result.get("interferences") or result.get("overlaps") or result.get("collisions"):
        return "interference"
    if result.get("near"):
        return "below_clearance"
    samples = result.get("samples", [])
    if result.get("stopped_by_contact") or result.get("stopped_by_loop") or result.get("stopped_at") is not None or (isinstance(samples, list) and any(s.get("stopped_by_contact") or s.get("stopped_by_loop") for s in samples if isinstance(s, dict))):
        return "stopped"
    if command == "motion.interference":
        return "sampled_clear"
    return "complete"


def _artifact(store, project, command, params, result, fmt="json"):
    directory = store.cache / "assemblies" / f"{project.revision:08d}-{uuid4().hex[:12]}"
    directory.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "command": command, "params": params, "revision": project.revision,
              "native_key": native_key(project), "status": _quality(command, result), "result": result}
    if fmt == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["item", "part_number", "description", "revision", "quantity", "definition"])
        def rows(items):
            for item in items:
                writer.writerow([item["item"], item.get("part_number", ""), item.get("description", ""), item.get("revision", ""), item.get("count", ""), item.get("definition", "")])
                rows(item.get("children", []))
        rows(result.get("rows", []))
        (directory / "bom.csv").write_text(stream.getvalue())
        report["artifact"] = str((directory / "bom.csv").relative_to(store.cache))
    (directory / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    report["report_artifact"] = str((directory / "report.json").relative_to(store.cache))
    return report


def execute(store, project, operation, payload, cancel=None) -> dict:
    payload = payload or {}
    _cancel(cancel)
    if operation == "bom.exclude":
        data = AssemblyData.model_validate(project.domains.get("assemblies", {}))
        component = payload.get("component")
        if component not in {c["id"] for c in (project.native_document or {}).get("components", [])}:
            raise ValueError("Select a current component definition")
        active = _query(store, project, "config.table").get("active") or "Default"
        excluded = set(data.bom_exclusions.get(active, []))
        if payload.get("excluded", True):
            excluded.add(component)
        else:
            excluded.discard(component)
        data.bom_exclusions[active] = sorted(excluded)
        return {"changed": True, "data": data.model_dump(mode="json"), "configuration": active}
    if operation == "report.open":
        registered = AssemblyData.model_validate(project.domains.get("assemblies", {})).reports
        report = next((r for r in registered if r.get("report_artifact") == payload.get("report_artifact")), None)
        if report is None:
            raise ValueError("Assembly report is not registered in this project")
        content = json.loads(store.artifact(report["report_artifact"]).read_text())
        return content | {"report_artifact": report["report_artifact"], "stale": content["native_key"] != native_key(project)}
    if operation == "inspect":
        result = {}
        from ..native_document import opened_native
        with opened_native(project, store=store) as session:
            for name, command in {"components": "component.list", "joints": "joint.list", "origins": "joint.inspect", "contact": "contact.list", "motion": "motion.list", "explode": "explode.list", "config": "config.table"}.items():
                _cancel(cancel)
                result[name] = session.execute(command)
        explicit = [o["id"] for o in (project.native_document or {}).get("occurrences", []) if o.get("grounded")]
        implicit = [oid for oid in result["joints"].get("fixed", []) if oid != 0 and oid not in explicit]
        result["anchors"] = {"explicit": explicit, "implicit": implicit,
                             "warning": "The native solver uses the first joint's A side as a temporary anchor when no explicit anchor participates; ground the intended occurrence to record engineering intent." if implicit else None}
        return {"revision": project.revision, "native_key": native_key(project), **result}
    if operation in {"query", "report", "export_bom", "export_motion"}:
        command = {"export_bom": "assembly.bom", "export_motion": "motion.export"}.get(operation, payload.get("command"))
        params = _safe_params(payload.get("params", {}))
        _guard_refs(project, command, params)
        result = bom_snapshot(store, project, params) if command == "assembly.bom" else _query(store, project, command, params)
        if operation == "query":
            return {"result": result, "revision": project.revision, "native_key": native_key(project), "status": _quality(command, result)}
        _cancel(cancel)
        report = _artifact(store, project, command, params, result, "csv" if operation == "export_bom" and payload.get("format") == "csv" else "json")
        data = AssemblyData.model_validate(project.domains.get("assemblies", {}))
        data.reports = (data.reports + [{k: v for k, v in report.items() if k != "result"}])[-100:]
        return report | {"changed": True, "data": data.model_dump(mode="json")}
    if operation == "native":
        command = payload.get("command")
        if command not in EDIT_COMMANDS:
            raise ValueError("Unknown assembly edit command")
        return _transaction(store, project, [(command, _safe_params(payload.get("params", {})))], payload.get("actor", "human"))
    if operation in {"constraints.create_set", "constraints.suppress_set"}:
        data = AssemblyData.model_validate(project.domains.get("assemblies", {}))
        if operation == "constraints.create_set":
            relations = payload.get("relations", [])
            kinds = {"parallel", "perpendicular", "angle", "point_on_plane", "point_on_line", "line_on_plane"}
            if not isinstance(relations, list) or not 1 <= len(relations) <= 100 or any(not isinstance(r, dict) or r.get("type") not in kinds for r in relations):
                raise ValueError("A constraint set requires 1..100 supported geometric relations")
            result = _transaction(store, project, [("joint.create", relation) for relation in relations])
            data.constraint_sets.append(ConstraintSet(id=uuid4().hex, name=payload.get("name", "Constraint set"), joints=[r["joint"] for r in result["results"]]))
        else:
            group = next((s for s in data.constraint_sets if s.id == payload.get("id")), None)
            if group is None:
                raise ValueError("Constraint set not found")
            current = {j["id"] for j in _query(store, project, "joint.list")["joints"]}
            if any(j not in current for j in group.joints):
                raise ValueError("Constraint set contains stale joint references; repair the set")
            suppressed = payload.get("suppressed")
            if not isinstance(suppressed, bool):
                raise ValueError("suppressed must be true or false")
            result = _transaction(store, project, [("joint.edit", {"joint": j, "suppressed": suppressed}) for j in group.joints])
        result["project"].setdefault("domains", {})["assemblies"] = data.model_dump(mode="json")
        return result
    if operation in {"pose.capture", "pose.restore", "joint.lock", "joint.unlock"}:
        data = AssemblyData.model_validate(project.domains.get("assemblies", {}))
        joints = _query(store, project, "joint.list")["joints"]
        if operation == "pose.capture":
            pose = Pose(name=payload.get("name", "Home"), values={str(j["id"]): [v["value"] for v in j["values"]] for j in joints}, native_key=native_key(project), joint_types={str(j["id"]): j["type"] for j in joints})
            data.poses = [p for p in data.poses if p.name != pose.name] + [pose]
            return _transaction(store, project, [], data=data.model_dump(mode="json"))
        if operation == "pose.restore":
            pose = next((p for p in data.poses if p.name == payload.get("name")), None)
            if pose is None:
                raise ValueError("Saved pose not found")
            if set(pose.values) != {str(j["id"]) for j in joints}:
                raise ValueError("Saved pose has stale joint references; capture or repair it")
            if pose.joint_types and pose.joint_types != {str(j["id"]): j["type"] for j in joints}:
                raise ValueError("Saved pose joint types changed; capture or repair it before restoring coordinates")
            commands = [("joint.drive", {"joint": int(key), "values": values}) for key, values in pose.values.items() if values]
            return _transaction(store, project, commands)
        joint = next((j for j in joints if str(j["id"]) == str(payload.get("joint"))), None)
        if joint is None:
            raise ValueError("Select an exact current joint ID")
        key = str(joint["id"])
        if operation == "joint.lock":
            if key in data.locks:
                raise ValueError("Joint is already locked")
            data.locks[key] = [v.get("limits") for v in joint["values"]]
            data.lock_kinds[key] = joint["type"]
            limits = [[v["value"], v["value"]] for v in joint["values"]]
        else:
            if key not in data.locks:
                raise ValueError("Joint has no saved lock to restore")
            if data.lock_kinds.get(key, joint["type"]) != joint["type"]:
                raise ValueError("Joint type changed; repair the saved lock before unlocking")
            limits = data.locks.pop(key)
            data.lock_kinds.pop(key, None)
            if len(limits) != len(joint["values"]):
                raise ValueError("Joint kind changed; repair the saved lock before unlocking")
        commands = [("joint.limits", {"joint": joint["id"], "index": i, **({"min": lim[0], "max": lim[1]} if lim else {})}) for i, lim in enumerate(limits)]
        return _transaction(store, project, commands, data=data.model_dump(mode="json"))
    if operation == "fit_stack":
        stack = Stack.model_validate(payload.get("stack", payload))
        return {"revision": project.revision, "native_key": native_key(project), **stack_result(stack)}
    raise ValueError(f"Unknown assemblies operation: {operation}")
