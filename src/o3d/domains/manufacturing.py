"""Versioned manufacturing domain; each run executes a real, bounded workflow.

Saved setup/library edits use the central Store domain-save transaction. Workers
write only content-addressed artifacts; probe feedback returns a proposed domain
change for the parent to apply with its normal expected-revision contract.
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import zipfile

from .manufacturing_ops.schema import Manufacturing
from .manufacturing_ops.util import cancelled

VERSION = "oxygen-manufacturing-v1.1"


def validate(data) -> dict:
    return Manufacturing.model_validate(data or {}).model_dump(mode="json")


def input_key(project, data=None):
    from ..model import geometry_key
    value = {"geometry": geometry_key(project), "native_document": getattr(project, "native_document", None),
             "domain": data if data is not None else getattr(project, "domains", {}).get("manufacturing", {}), "toolchain": VERSION}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def validate_artifact(store, relative, project=None):
    """Artifact-route hook: refuse obsolete NC/build reports after relevant edits."""
    segments = Path(relative).parts
    if not segments or segments[0] != "manufacturing":
        return
    project = project or store.load()
    current = input_key(project, validate(getattr(project, "domains", {}).get("manufacturing", {})))
    if len(segments) != 4 or segments[1] != current:
        raise ValueError("Manufacturing artifact is stale; regenerate it for the current design, machine, tool and setup")


def _json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False)+"\n")


def _current_mesh(store, project):
    from ..model import geometry_key
    mesh = store.cache/"builds"/geometry_key(project)/"part.stl"
    if not mesh.is_file():
        from ..geometry import build
        build(store, project)
    if not mesh.is_file():
        raise RuntimeError("Current design build produced no STL; multi-body/native mesh slicing requires an explicit printable body")
    return mesh


def _find(items, identifier, kind):
    found = next((i for i in items if i.id == identifier), None)
    if found is None:
        raise ValueError(f"Choose a saved {kind}; {identifier!r} was not found")
    return found


def _setup_sheet(project, setup, machine, tools, paths, verification):
    rows = "".join(f"<tr><td>{html.escape(p.operation_id)}</td><td>{html.escape(p.strategy)}</td><td>T{tools[p.tool_id].number} {html.escape(tools[p.tool_id].name)}</td><td>{len(p.moves)}</td></tr>" for p in paths)
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Manufacturing setup {html.escape(setup.name)}</title>
<style>body{{font:15px system-ui;max-width:900px;margin:40px auto;line-height:1.6}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #bbb;padding:8px;text-align:left}}pre{{white-space:pre-wrap}}</style>
<h1>{html.escape(setup.name)}</h1><p>Source CAD revision {project.revision}. Machine: {html.escape(machine.name)}. Post: {machine.post}.</p>
<p><strong>Review required. This software does not establish machine/controller, workholding, physical cut or operator qualification.</strong></p>
<p>Work offset G{setup.work_offset}; recorded origin {setup.wcs_origin} mm; XY rotation {setup.wcs_rotation_z_deg}°. Set the controller work offset to the recorded origin before use.</p>
<p>Stock {html.escape(setup.stock.material)}: {setup.stock.minimum} to {setup.stock.maximum} mm. Clearance Z {setup.clearance_z} mm.</p>
<p>Before running, establish a clear start position and verify controller tool table H numbers against the recorded measured tool lengths. The post enables G43 Hn for each tool; this software does not set the machine tool table.</p>
<table><tr><th>Operation</th><th>Strategy</th><th>Tool</th><th>Moves</th></tr>{rows}</table>
<h2>Declared checks and limits</h2><pre>{html.escape(json.dumps(verification, indent=2))}</pre><p>{html.escape(setup.notes)}</p></html>'''


def execute(store, project, operation, payload, cancel=None) -> dict:
    payload = payload or {}
    if not isinstance(payload, dict):
        raise ValueError("Manufacturing input must be an object")
    if len(json.dumps(payload,allow_nan=False).encode()) > 2*1024*1024:
        raise ValueError("Manufacturing input exceeds 2 MiB")
    data = validate(getattr(project, "domains", {}).get("manufacturing", {}))
    domain = Manufacturing.model_validate(data)
    key = input_key(project, data)
    # Payload belongs to the derived run key too: different imported measurements never overwrite each other.
    runtime = {}
    if operation == "slice":
        from .manufacturing_ops.additive import runtime_identity
        runtime = runtime_identity()
    run_key = hashlib.sha256(json.dumps({"input": key, "operation": operation, "payload": payload, "external_runtime":runtime}, sort_keys=True, allow_nan=False).encode()).hexdigest()
    destination = store.cache/"manufacturing"/key/run_key
    destination.mkdir(parents=True, exist_ok=True)
    if cancelled(cancel):
        raise RuntimeError("Manufacturing cancelled")
    result = {"schema_version": 1, "operation": operation, "source_revision": project.revision, "input_key": key,
              "run_key": run_key, "toolchain": VERSION, "state": "review_required", "physical_process_qualified": False}
    _json(destination/"inputs.json", {"manufacturing": data, "request": payload, "source_revision": project.revision, "input_key": key})
    if operation in {"toolpath", "post", "verify", "release"}:
        from .manufacturing_ops.cam import generate
        from .manufacturing_ops.verification import post, verify_path, verify_post
        setup = _find(domain.setups, payload.get("setup_id"), "setup")
        machine = _find(domain.machines, setup.machine_id, "machine")
        tools = {t.id: t for t in domain.tools}
        ops = [o for o in domain.operations if o.setup_id == setup.id]
        if payload.get("operation_ids"):
            requested = set(payload["operation_ids"])
            if requested-set(o.id for o in ops):
                raise ValueError("Requested operations do not belong to this setup")
            ops = [o for o in ops if o.id in requested]
            if any(not set(o.depends_on).issubset(requested) for o in ops):
                raise ValueError("Selected operations omit required predecessors")
        if not ops:
            raise ValueError("Add a machining operation to this setup")
        paths, reports = [], []
        for op in ops:
            surface = None
            roughing = None
            if op.strategy == "parallel_finish":
                from .manufacturing_ops.surface_cam import finish
                path, surface = finish(_current_mesh(store, project), destination, op, setup, tools[op.tool_id], machine, cancel)
            elif op.strategy in {"rough_3d", "rest_3d"}:
                from .manufacturing_ops.roughing import rough
                predecessors = [p for p in paths if p.operation_id in op.depends_on]
                path, roughing = rough(_current_mesh(store, project), op, setup, tools[op.tool_id], machine, predecessors, tools, cancel)
            else:
                path = generate(op, setup, tools[op.tool_id], machine, cancel)
            report = verify_path(path, op, setup, tools[op.tool_id], machine)
            if surface is not None:
                report.update(surface_finishing=surface, stock_removal_verified=False,
                              model="OpenCAMLib ball drop cutter; independent sampled sphere/triangle check + machine/fixture envelopes")
            if roughing is not None:
                report.update(layer_roughing=roughing, stock_removal_verified=False,
                              model="Conservative model projection + predecessor cylinder sweeps at discrete Z layers")
            reports.append({"operation_id": op.id, **report})
            paths.append(path)
        _json(destination/"toolpaths.json", [p.wire() for p in paths])
        _json(destination/"verification.json", reports)
        result.update(paths=[p.wire() for p in paths], verification=reports)
        violations = sum(r["violation_count"] for r in reports)
        if violations:
            result.update(state="rejected", reason=f"{violations} path violations; NC release blocked")
        else:
            code = post(paths, setup, tools, machine)
            backplot = verify_post(code, paths, setup, machine)
            (destination/"program.ngc").write_text(code)
            result["posted_motion_check"] = backplot
            (destination/"setup-sheet.html").write_text(_setup_sheet(project, setup, machine, tools, paths, reports))
            _json(destination/"tool-list.json", [tools[i].model_dump(mode="json") for i in sorted({p.tool_id for p in paths})])
            if operation == "release":
                # A real process release package; no claims of physical production acceptance.
                from ..model import geometry_key
                source = store.cache/"builds"/geometry_key(project)/"part.step"
                if not source.is_file():
                    from ..geometry import build
                    build(store, project)
                if not source.is_file():
                    raise ValueError("A current CAD STEP is required for the manufacturing handoff")
                release_files=["program.ngc", "inputs.json", "verification.json", "toolpaths.json", "setup-sheet.html", "tool-list.json"]
                _json(destination/"handoff-manifest.json", {"schema_version":1,"source_revision":project.revision,
                    "input_key":key,"manufacturing_toolchain":VERSION,"machine":machine.model_dump(mode="json"),
                    "cad_sha256":hashlib.sha256(source.read_bytes()).hexdigest(),
                    "files":{name:hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in release_files},
                    "physical_process_qualified":False,"posted_motion_check":backplot})
                with zipfile.ZipFile(destination/"manufacturing-release.zip", "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.write(source, "source/part.step")
                    archive.writestr("source/project.json", project.model_dump_json(indent=2))
                    for name in [*release_files,"handoff-manifest.json"]:
                        archive.write(destination/name, name)
    elif operation in {"nest", "cut"}:
        from .manufacturing_ops.fabrication import nest, dxf, cut_nc
        if domain.nesting is None:
            raise ValueError("Define material, sheets, part geometry and quantities before nesting")
        nested = nest(domain.nesting, cancel)
        result["nest"] = nested
        _json(destination/"nest.json", nested)
        for sheet in range(nested["sheet_count"]):
            (destination/f"sheet-{sheet+1}.dxf").write_text(dxf(nested, sheet))
            if operation == "cut":
                setup = _find(domain.setups, payload.get("setup_id"), "setup")
                machine = _find(domain.machines, setup.machine_id, "machine")
                tool = _find(domain.tools, payload.get("tool_id"), "laser tool")
                code, report = cut_nc(nested, sheet, setup, tool, machine, payload.get("power_s"))
                (destination/f"sheet-{sheet+1}.nc").write_text(code)
                result.setdefault("cutting", []).append(report)
    elif operation == "slice":
        from .manufacturing_ops.additive import slice_mesh
        if domain.printer is None:
            raise ValueError("Save an FFF printer and material profile before slicing")
        machine = _find(domain.machines, domain.printer.machine_id, "printer machine")
        result["slicing"] = slice_mesh(_current_mesh(store, project), destination, domain.printer, machine, cancel)
    elif operation == "sheet_release":
        from ..native_document import opened_native
        from .manufacturing_ops.sheet import release_session
        body = payload.get("body")
        if body is not None and (not isinstance(body, str) or len(body) > 200):
            raise ValueError("Sheet body must be a native body name")
        with opened_native(project, store) as session:
            result["sheet"] = release_session(session, destination, body)
    elif operation == "plastics_advice":
        from .manufacturing_ops.plastics import analyze
        if domain.plastics is None:
            raise ValueError("Provide material rules, their source and tooling pull direction")
        result["plastics"] = analyze(_current_mesh(store, project), domain.plastics, cancel)
    elif operation == "inspect":
        from .manufacturing_ops.inspection import inspect
        result["inspection"] = inspect(payload)
    elif operation in {"align", "apply_probe"}:
        from .manufacturing_ops.inspection import align
        setup = _find(domain.setups, payload.get("setup_id"), "setup")
        report = align(payload, setup.tolerance_mm)
        result["alignment"] = report
        if operation == "apply_probe":
            if payload.get("source_input_key") != key:
                raise ValueError("Probe feedback source is stale; re-inspect the current manufacturing setup")
            if not report["translation_within_tolerance"]:
                raise ValueError("Probe points require rotation or exceed the setup tolerance; automatic WCS translation refused")
            setup.wcs_origin = tuple(a+b for a, b in zip(setup.wcs_origin, report["translation_mm"]))
            result.update(data=domain.model_dump(mode="json"), changed=True,
                          feedback={"setup_id": setup.id, "wcs_translation_mm": report["translation_mm"], "source_input_key": key,
                                    "measurement_sha256": report["source_sha256"]})
    elif operation == "probe_plan":
        from .manufacturing_ops.inspection import probe_program
        setup = _find(domain.setups, payload.get("setup_id"), "setup")
        machine = _find(domain.machines, setup.machine_id, "machine")
        tool = _find(domain.tools, payload.get("tool_id"), "probe tool")
        (destination/"probe-plan.ngc").write_text(probe_program(payload, setup, tool, machine))
        result["probe"] = {"offline_plan": True, "cycle": "LinuxCNC G38.2", "live_connection": False}
    else:
        raise ValueError(f"Unsupported manufacturing operation {operation!r}; no output was manufactured")
    if cancelled(cancel):
        raise RuntimeError("Manufacturing cancelled")
    artifacts = {}
    for path in sorted(destination.iterdir()):
        if path.is_file() and path.name != "result.json":
            artifacts[path.name] = str(path.relative_to(store.cache))
    result["artifacts"] = artifacts
    result["artifact_hashes"] = {name: hashlib.sha256((destination/name).read_bytes()).hexdigest() for name in artifacts}
    _json(destination/"result.json", result)
    result["artifacts"]["result.json"] = str((destination/"result.json").relative_to(store.cache))
    return result
