"""Exact CAD compilation and measured, face-addressable viewport artifacts."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import build123d as bd
import numpy as np

from .model import EdgeTreatment, Extrude, Finding, Hole, Profile, Project, geometry_key, verification_key
from .store import Store, write_json


def vector(value):
    return [float(value.X), float(value.Y), float(value.Z)]


def signature(shape):
    c = vector(shape.center())
    values = [shape.geom_type.name, *[round(x, 6) for x in c]]
    if isinstance(shape, bd.Face):
        values += [round(shape.area, 6)]
        if shape.geom_type == bd.GeomType.PLANE:
            values += [round(x, 6) for x in vector(shape.normal_at())]
    else:
        values += [round(shape.length, 6)]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()[:20]


def face_map(shape):
    """Semantic extreme planes, with strict signatures for all other faces.

    A semantic role is exposed only when it resolves to exactly one face.
    No list index is ever used as a persistent native selection.
    """
    faces = list(shape.faces())
    result = {f"face:{signature(f)}": f for f in faces}
    box = shape.bounding_box()
    for axis, coord in enumerate("xyz"):
        for side, bound in (("min", vector(box.min)[axis]), ("max", vector(box.max)[axis])):
            selected = [f for f in faces if f.geom_type == bd.GeomType.PLANE
                        and abs(abs(vector(f.normal_at())[axis]) - 1) < 1e-6
                        and abs(vector(f.center())[axis] - bound) < 1e-5]
            if len(selected) == 1:
                result[f"part:{coord}{side}"] = selected[0]
    return result


def resolve_face(shape, name):
    mapping = face_map(shape)
    if name not in mapping:
        raise ValueError(f"Missing or ambiguous face reference {name}; explicitly select a replacement")
    return mapping[name]


def select_edges(shape, selection):
    edges = list(shape.edges())
    if selection == "part:all_edges":
        return edges
    if selection == "part:vertical_edges":
        return [e for e in edges if e.geom_type == bd.GeomType.LINE and abs(abs(vector(e.tangent_at())[2]) - 1) < 1e-6]
    if selection in ("part:top_edges", "part:bottom_edges"):
        face = resolve_face(shape, "part:zmax" if selection == "part:top_edges" else "part:zmin")
        return list(face.edges())
    matches = [e for e in edges if f"edge:{signature(e)}" == selection]
    if len(matches) != 1:
        raise ValueError(f"Missing or ambiguous edge reference {selection}; select a replacement")
    return matches


def compile_occt_shape(project: Project):
    profiles = {}
    shape = None
    for feature in project.features:
        if feature.suppressed:
            continue
        try:
            if isinstance(feature, Profile):
                plane = {"XY": bd.Plane.XY, "XZ": bd.Plane.XZ, "YZ": bd.Plane.YZ}[feature.plane]
                if feature.shape == "rectangle":
                    sketch = bd.Rectangle(project.resolve(feature.width), project.resolve(feature.height))
                elif feature.shape == "circle":
                    sketch = bd.Circle(project.resolve(feature.radius))
                else:
                    sketch = bd.Polygon(*feature.points, align=None)
                profiles[feature.id] = (plane * sketch).moved(bd.Location(feature.origin, feature.rotation))
            elif isinstance(feature, Extrude):
                if feature.profile not in profiles:
                    raise ValueError("The referenced profile is suppressed or unavailable")
                body = bd.extrude(profiles[feature.profile], amount=project.resolve(feature.distance))
                if shape is None:
                    if feature.operation == "cut":
                        raise ValueError("The first extrusion must add material")
                    shape = body
                else:
                    old_volume = shape.volume
                    shape = shape.fuse(body) if feature.operation == "add" else shape.cut(body)
                    if abs(shape.volume - old_volume) < 1e-8:
                        raise ValueError("Operation made no measured change to the solid")
            elif isinstance(feature, Hole):
                if shape is None:
                    raise ValueError("A hole needs an existing solid")
                axis = "XYZ".index(feature.axis)
                box = shape.bounding_box()
                low, high = vector(box.min), vector(box.max)
                other_axes = [i for i in range(3) if i != axis]
                old_volume = shape.volume
                for row in range(feature.rows):
                    for column in range(feature.columns):
                        center = list(feature.center)
                        center[other_axes[0]] += column * feature.spacing[0]
                        center[other_axes[1]] += row * feature.spacing[1]
                        if feature.through:
                            center[axis] = low[axis] - 1
                            depth = high[axis] - low[axis] + 2
                            normal = [int(i == axis) for i in range(3)]
                        else:
                            depth = project.resolve(feature.depth)
                            normal = [(-1 if feature.reverse else 1) * int(i == axis) for i in range(3)]
                        cutter = bd.Plane(origin=center, z_dir=normal) * bd.Cylinder(project.resolve(feature.diameter) / 2, depth, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
                        shape = shape.cut(cutter)
                if old_volume - shape.volume < 1e-8:
                    raise ValueError("Hole pattern did not intersect the solid")
            elif isinstance(feature, EdgeTreatment):
                if shape is None:
                    raise ValueError("Edge treatment needs an existing solid")
                edges = select_edges(shape, feature.selection)
                if not edges:
                    raise ValueError("Edge selection is empty")
                size = project.resolve(feature.size)
                shape = shape.fillet(size, edges) if feature.type == "fillet" else shape.chamfer(size, None, edges)
            elif feature.type == "native":
                raise ValueError("Native command features require geometry_engine=solidcraft")
            if shape is not None and not shape.is_valid:
                raise ValueError("Kernel reports invalid geometry")
        except Exception as error:
            raise ValueError(f"Feature {feature.id}: {error}") from error
    if shape is None or len(shape.solids()) != 1 or shape.volume <= 0:
        raise ValueError("The project must produce exactly one connected, positive-volume solid")
    return shape.moved(bd.Location(project.placement.translation, project.placement.rotation))


def compile_shape(project: Project):
    if project.geometry_engine == "occt":
        return compile_occt_shape(project)
    import tempfile
    from .native import compile_native
    with tempfile.TemporaryDirectory(prefix="oxygen-geometry-") as folder:
        return compile_native(project, Path(folder))[0]


def face_metadata(shape):
    mapping = face_map(shape)
    output = []
    for face in shape.faces():
        strict_id = f"face:{signature(face)}"
        roles = [name for name, value in mapping.items() if name.startswith("part:") and value.is_same(face)]
        vertices, triangles = face.tessellate(0.15, 0.15)
        output.append({"id": roles[0] if roles else strict_id, "signature": strict_id,
                       "aliases": roles, "kind": face.geom_type.name, "area_mm2": face.area,
                       "center": vector(face.center()), "normal": vector(face.normal_at()) if face.geom_type == bd.GeomType.PLANE else None,
                       "vertices": [vector(v) for v in vertices], "triangles": [list(t) for t in triangles]})
    return output


def load_reference(store: Store, reference):
    path = store.root / reference.file
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != reference.sha256:
        raise ValueError(f"Reference {reference.id} is missing or its immutable asset hash changed")
    shape = bd.import_step(path)
    if not shape.is_valid or not shape.solids():
        raise ValueError(f"Reference {reference.id} contains no valid solids")
    return shape.moved(bd.Location(reference.placement.translation, reference.placement.rotation))


def build(store: Store, project: Project):
    key = geometry_key(project)
    destination = store.cache / "builds" / key
    destination.mkdir(parents=True, exist_ok=True)
    if project.geometry_engine == "solidcraft":
        from .native import compile_native
        shape, engine = compile_native(project, destination.resolve())
    else:
        shape, engine = compile_occt_shape(project), {"engine": "occt"}
    scene = engine.pop("scene", None)
    engine.pop("canonical", None)
    artifacts = {}
    if shape is not None:
        if not bd.export_brep(shape, destination / "part.brep"):
            raise ValueError("BREP export failed")
        if project.geometry_engine == "occt":
            bd.export_step(shape, destination / "part.step")
        bd.export_stl(shape, destination / "part.stl", tolerance=0.05, angular_tolerance=0.1)
        artifacts.update({name: f"builds/{key}/part.{extension}" for name, extension in (("step", "step"), ("stl", "stl"), ("brep", "brep"))})
    faces = face_metadata(shape) if shape is not None else []
    if scene:
        # Preserve body-specific identities/topology alongside legacy aggregate viewport data.
        for body, independent in zip([b for b in scene["bodies"] if b["geometry_kind"] != "mesh"], engine["independent_bodies"]):
            body_shape = bd.import_step(destination / independent["step"])
            body["faces"] = face_metadata(body_shape)
            for face in body["faces"]:
                face["body_id"] = body["id"]
            body["artifacts"] = {"step": f"builds/{key}/{independent['step']}"}
        write_json(destination / "scene.json", scene)
        artifacts["scene"] = f"builds/{key}/scene.json"
    references = []
    for ref in project.references:
        other = load_reference(store, ref)
        ref_faces = face_metadata(other)
        for index, face in enumerate(ref_faces):
            face["id"] = f"asset:{ref.sha256}:{index}"
        references.append({"id": ref.id, "name": ref.name, "faces": ref_faces, "visible": ref.visible})
    if shape is not None:
        bbox = shape.bounding_box()
        bounds = [vector(bbox.min), vector(bbox.max)]
        dimensions = vector(bbox.size)
        volume = sum(s.volume for s in shape.solids())
        center = vector(shape.center(bd.CenterOf.MASS)) if volume > 0 else vector(shape.center())
    elif scene and scene["bodies"]:
        low = [min(b["bbox"]["min"][i] for b in scene["bodies"]) for i in range(3)]
        high = [max(b["bbox"]["max"][i] for b in scene["bodies"]) for i in range(3)]
        bounds, dimensions, volume = [low, high], [high[i]-low[i] for i in range(3)], None
        center = [(low[i]+high[i])/2 for i in range(3)]
    else:
        bounds, dimensions, volume, center = [[0,0,0],[0,0,0]], [0,0,0], 0, [0,0,0]
    result = {"key": key, "revision": project.revision, "engine": engine, "volume_mm3": volume,
              "dimensions_mm": dimensions, "bounds": bounds, "center_of_mass": center,
              "faces": faces, "references": references, "scene": scene,
              "edges": [{"id": f"edge:{signature(e)}", "center": vector(e.center()), "length_mm": e.length} for e in shape.edges()] if shape is not None else [],
              "artifacts": artifacts}
    if project.geometry_engine == "solidcraft":
        result["artifacts"].update({"native_design": f"builds/{key}/part.solidcraft", "engine_script": f"builds/{key}/engine-script.json"})
    write_json(destination / "build.json", result)
    write_json(store.cache / "last-build.json", result)
    return result


def check(store: Store, project: Project):
    key = geometry_key(project)
    path = store.cache / "builds" / key
    if not (path / "build.json").exists():
        build(store, project)
    if not (path / "part.brep").exists():
        raise ValueError("Engineering checks require B-rep geometry; mesh-only and sketch-only documents are not solids")
    shape = bd.import_brep(path / "part.brep")
    if not shape.solids() or shape.volume <= 0:
        raise ValueError("Solid engineering checks require positive-volume solid bodies; use surface diagnostics for open surfaces")
    findings = [Finding(code="valid_solid", severity="info", message=f"{len(shape.solids())} valid solid bodies", value=shape.volume)]
    import trimesh
    mesh = trimesh.load(path / "part.stl", force="mesh")
    if not mesh.is_watertight or not mesh.is_winding_consistent:
        findings.append(Finding(code="mesh_integrity", severity="error", message="Independent exported-mesh audit failed"))
    mesh_error = abs(abs(mesh.volume) - shape.volume) / shape.volume
    if mesh_error > 0.01:
        findings.append(Finding(code="volume_mismatch", severity="error", message="Exported mesh and exact-solid volume differ by more than 1%", value=mesh_error, limit=0.01))
    refs = {}
    for ref in project.references:
        other = load_reference(store, ref)
        clearance = shape.distance_to(other)
        intersection = shape.intersect(other)
        interference = sum(abs(s.volume) for s in intersection) if isinstance(intersection, (list, bd.ShapeList)) else abs(intersection.volume) if intersection else 0
        refs[ref.id] = {"clearance_mm": clearance, "interference_mm3": interference}
        if ref.check_interference and interference > 1e-5:
            findings.append(Finding(code="interference", severity="error", feature=ref.id, message=f"Interference with {ref.name}", value=interference, limit=0))
    for req in project.requirements:
        if not req.enabled or req.kind == "max_displacement":
            continue
        if req.kind == "max_mass":
            if project.material is None:
                findings.append(Finding(code="missing_material", severity="warning", feature=req.id, message="Mass requirement needs material density"))
                continue
            value = shape.volume * project.material.density_g_cm3 / 1000
            passed = value <= req.value
        elif req.kind == "max_dimension":
            value = vector(shape.bounding_box().size)["XYZ".index(req.axis)]
            passed = value <= req.value
        else:
            if req.reference not in refs:
                findings.append(Finding(code="missing_reference", severity="error", feature=req.id, message="Clearance requirement references a missing component"))
                continue
            value = refs[req.reference]["clearance_mm"]
            passed = value >= req.value and refs[req.reference]["interference_mm3"] <= 1e-5
        findings.append(Finding(code=req.kind, severity="info" if passed else "error", feature=req.id, message=f"{req.id}: {'passed' if passed else 'failed'}", value=value, limit=req.value))
    result = {"revision": project.revision, "key": key, "input_key": verification_key(project), "state": "failed" if any(f.severity == "error" for f in findings) else "needs_input" if any(f.severity == "warning" for f in findings) else "passed",
              "findings": [f.model_dump() for f in findings], "references": refs, "mesh_volume_relative_error": mesh_error,
              "mass_g": shape.volume * project.material.density_g_cm3 / 1000 if project.material else None}
    write_json(path / "checks.json", result)
    return result
